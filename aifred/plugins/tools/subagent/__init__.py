"""Sub-agent plugin — delegate a self-contained task to a fresh model run.

The main agent calls ``delegate_task`` like any other tool. The plugin runs a
sub-agent: a new inference with its own context window, its own toolkit and
its own tool loop, but without the caller's persona, conversation or memory.
Only the sub-agent's final report comes back as the tool result; the full
transcript (thinking, text, every tool call with arguments and result) is
delivered as a collapsible block for the chat bubble and never reaches the
model. Design: docs/de/architecture/subagent-plugin.md.

Settings (settings.json next to this module, gear icon in the plugin modal):
    SUBAGENT_ALLOWED_TIERS             tiers the sub-agent's tools may have
    SUBAGENT_MAX_DEPTH                 how deep delegation may nest
    SUBAGENT_DELEGATE_TO_OTHER_AGENTS  expose the ``agent`` parameter
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any, AsyncGenerator, Optional

from ....lib.bubble import KIND_COLLAPSIBLE, KIND_SANDBOX_HTML, KIND_SANDBOX_IMAGE, BubbleArtifact
from ....lib.formatting import performance_footer_text
from ....lib.function_calling import Tool, ToolKit
from ....lib.llm_client import LLMClient, build_llm_options
from ....lib.llm_pipeline import run_llm_stream
from ....lib.agent_memory import prepare_agent_toolkit
from ....lib.plugin_base import (
    CredentialField,
    PluginContext,
    load_plugin_instructions,
    load_plugin_settings,
    load_tool_description,
    save_plugin_settings,
)
from ....lib.sandbox import SANDBOX_HTML_URL_MARKER, SANDBOX_IMAGE_URL_MARKER
from ....lib.security import (
    TIER_COMMUNICATE,
    TIER_READONLY,
    TIER_WRITE_DATA,
    TIER_WRITE_SYSTEM,
)

TOOL_NAME = "delegate_task"

# settings.json keys (also the env_key of the CredentialField)
ALLOWED_TIERS_KEY = "SUBAGENT_ALLOWED_TIERS"
MAX_DEPTH_KEY = "SUBAGENT_MAX_DEPTH"
DELEGATE_OTHERS_KEY = "SUBAGENT_DELEGATE_TO_OTHER_AGENTS"

# Defaults: read, research, write files, sandbox — but no sending (tier 1)
# and no deleting (tier 3). Sending stays with the main agent, which answers
# for it in the conversation. Tiers are joined with "+", not ",": the generic
# settings modal splits option values on commas.
TIER_SEPARATOR = "+"
DEFAULT_ALLOWED_TIERS = f"{TIER_READONLY}{TIER_SEPARATOR}{TIER_WRITE_DATA}"
DEFAULT_MAX_DEPTH = "1"
DEFAULT_DELEGATE_OTHERS = "0"

# PluginContext.metadata key carrying the nesting depth of the current run.
# A sub-agent's toolkit is built with depth+1; at max depth the plugin offers
# no tool at all, so recursion is bounded structurally, without a counter.
DEPTH_METADATA_KEY = "subagent_depth"

def _tiers(*tiers: int) -> str:
    return TIER_SEPARATOR.join(str(t) for t in tiers)


_TIER_OPTIONS: list[tuple[str, str]] = [
    # Labels are i18n keys, resolved by the credentials modal per UI language.
    (_tiers(TIER_READONLY), "subagent_opt_tiers_0"),
    (_tiers(TIER_READONLY, TIER_WRITE_DATA), "subagent_opt_tiers_0_2"),
    (_tiers(TIER_READONLY, TIER_COMMUNICATE, TIER_WRITE_DATA), "subagent_opt_tiers_0_1_2"),
    (_tiers(TIER_READONLY, TIER_WRITE_DATA, TIER_WRITE_SYSTEM), "subagent_opt_tiers_0_2_3"),
    (
        _tiers(TIER_READONLY, TIER_COMMUNICATE, TIER_WRITE_DATA, TIER_WRITE_SYSTEM),
        "subagent_opt_tiers_0_1_2_3",
    ),
]


@dataclass(frozen=True)
class SubAgentSettings:
    allowed_tiers: frozenset[int]
    max_depth: int
    delegate_to_other_agents: bool


@dataclass(frozen=True)
class RunParams:
    """Everything one inference needs, resolved through the same helpers the
    browser path (multi_agent) and the hub path (message_processor) use."""

    backend_type: str
    backend_url: Optional[str]
    model: str
    num_ctx: int
    temperature: float


def _parse_settings(raw: dict[str, str]) -> SubAgentSettings:
    tiers = raw.get(ALLOWED_TIERS_KEY, DEFAULT_ALLOWED_TIERS)
    return SubAgentSettings(
        allowed_tiers=frozenset(int(t) for t in tiers.split(TIER_SEPARATOR) if t.strip()),
        max_depth=int(raw.get(MAX_DEPTH_KEY, DEFAULT_MAX_DEPTH)),
        delegate_to_other_agents=raw.get(DELEGATE_OTHERS_KEY, DEFAULT_DELEGATE_OTHERS) == "1",
    )


def _agent_model_and_tools(agent_id: str, state: Any) -> tuple[str, Optional[frozenset[str]]]:
    """Base model id and tool whitelist of an agent — the two things that make
    a sub-agent "after" another agent differ from one after the caller."""
    from ....lib.agent_config import get_agent_config
    cfg = get_agent_config(agent_id)
    tools = frozenset(cfg.tools) if (cfg and cfg.tools is not None) else None
    if state is not None:
        from ....lib.agent_settings import get_agent_base_model_id
        model = get_agent_base_model_id(state, agent_id)
    else:
        from ....lib.config import get_effective_model_from_settings
        model = get_effective_model_from_settings(agent_id)
    return model, tools


def delegation_candidates(ctx: PluginContext) -> list[str]:
    """Agents a sub-agent could run "after": those whose model or tool
    whitelist differs from the caller's. Derived on every toolkit build, so
    the schema never offers a useless option."""
    from ....lib.agent_config import ROLE_SYSTEM, get_agent_config, get_agent_ids
    caller_model, caller_tools = _agent_model_and_tools(ctx.agent_id, ctx.state)
    candidates: list[str] = []
    for agent_id in get_agent_ids():
        if agent_id == ctx.agent_id:
            continue
        cfg = get_agent_config(agent_id)
        if cfg is not None and cfg.role == ROLE_SYSTEM:
            # System agents back internal workflows (calibration, vision);
            # they are no chat partners and no delegation targets either.
            continue
        model, tools = _agent_model_and_tools(agent_id, ctx.state)
        if model != caller_model or tools != caller_tools:
            candidates.append(agent_id)
    return candidates


def within_allowed_tiers(tools: list[Tool], settings: SubAgentSettings) -> list[Tool]:
    """The plugin setting "allowed tiers" applied to a tool list."""
    return [t for t in tools if t.tier in settings.allowed_tiers]


def delegation_legend(
    ctx: PluginContext, settings: SubAgentSettings, depth: int, candidates: list[str],
) -> str:
    """What a sub-agent run "after" each candidate could use, grouped by
    plugin, plus one line per plugin saying what it is. Everything comes from
    the plugins' own i18n texts and the agents' whitelists, derived on every
    toolkit build — a new, changed or disabled plugin shows up by itself."""
    from ....lib.agent_config import get_agent_config
    from ....lib.plugin_base import plugin_description, plugin_display_name
    from ....lib.plugin_registry import collect_plugin_tools
    from ....lib.security import filter_tools_by_tier

    # The sub-agent's view: its own depth (so the recursion bound applies to
    # this plugin too), the caller's tier ceiling, the allowed tiers.
    sub_ctx = replace(ctx, metadata={**ctx.metadata, DEPTH_METADATA_KEY: depth + 1})
    offered = [
        (owner, within_allowed_tiers(filter_tools_by_tier(tools, ctx.max_tier), settings))
        for owner, tools in collect_plugin_tools(sub_ctx)
    ]

    agent_lines: list[str] = []
    described: dict[str, str] = {}
    for agent_id in candidates:
        cfg = get_agent_config(agent_id)
        whitelist = set(cfg.tools) if cfg is not None and cfg.tools is not None else None
        groups: list[str] = []
        for owner, tools in offered:
            if any(whitelist is None or t.name in whitelist for t in tools):
                name = plugin_display_name(owner, ctx.lang)
                groups.append(name)
                described.setdefault(name, plugin_description(owner, ctx.lang))
        label = cfg.display_name if cfg is not None else agent_id
        agent_lines.append(f"- {agent_id} ({label}): {', '.join(groups) or '-'}")

    plugin_lines = [f"- {name}: {text}" for name, text in described.items()]
    return "\n".join([
        "Tools per agent:", *agent_lines,
        "Tool groups:", *plugin_lines,
    ])


def resolve_run_params(agent_id: str, state: Any) -> RunParams:
    """Model, context and temperature for the sub-agent's inference."""
    if state is not None:
        from ....lib.agent_settings import get_agent_base_model_id
        from ....lib.multi_agent import resolve_agent_temperature
        from ....lib.research.context_utils import get_agent_num_ctx
        model = state._effective_model_id(agent_id) or state._effective_model_id("aifred")
        base_id = get_agent_base_model_id(state, agent_id)
        num_ctx, _ = get_agent_num_ctx(agent_id, state, base_id)
        return RunParams(
            backend_type=state.backend_type,
            backend_url=state.backend_url,
            model=model,
            num_ctx=num_ctx,
            temperature=resolve_agent_temperature(state, agent_id),
        )
    from ....lib.agent_settings import get_persisted_tuning
    from ....lib.config import (
        BACKEND_URLS,
        DEFAULT_SETTINGS,
        DEFAULT_TEMPERATURE,
        get_effective_model_from_settings,
    )
    from ....lib.research.context_utils import get_stateless_num_ctx
    from ....lib.settings import load_settings
    settings = load_settings() or {}
    backend_type = settings.get("backend_type", DEFAULT_SETTINGS["backend_type"])
    model = get_effective_model_from_settings(agent_id)
    if not model:
        raise RuntimeError(f"no model configured for agent '{agent_id}' on {backend_type}")
    num_ctx, _ = get_stateless_num_ctx(model, backend_type)
    return RunParams(
        backend_type=backend_type,
        backend_url=BACKEND_URLS.get(backend_type, ""),
        model=model,
        num_ctx=num_ctx,
        temperature=get_persisted_tuning(settings, agent_id, "temperature", DEFAULT_TEMPERATURE),
    )


_THINK_RE = re.compile(r"<think>(.*?)</think>", re.S)
_TITLE_LEN = 60


def _short(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= _TITLE_LEN else flat[:_TITLE_LEN].rstrip() + "…"


def format_transcript_call(label: str, name: str, arguments: str) -> str:
    """A tool call for the transcript, readable in full: short arguments on
    one line, multi-line strings (code, file contents) as their own block."""
    try:
        parsed = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError:
        # The model sent arguments that are not JSON; show them as they came.
        return f"[{label}] {name}({arguments})"
    if not isinstance(parsed, dict):
        return f"[{label}] {name}({arguments})"
    lines = [f"[{label}] {name}"]
    for key, value in parsed.items():
        if isinstance(value, str) and "\n" in value:
            lines.append(f"{key}:\n{value}")
        else:
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    return "\n".join(lines)


def _transcript_labels(lang: str) -> dict[str, str]:
    de = str(lang).startswith("de")
    return {
        # ⇄: task goes out to the right, the report comes back to the left.
        "title": "⇄ Sub-Agent" if de else "⇄ Sub-agent",
        "task": "AUFGABE" if de else "TASK",
        "thinking": "DENKEN" if de else "THINKING",
        "call": "WERKZEUG" if de else "TOOL",
        "result": "ERGEBNIS" if de else "RESULT",
        "expected": "ERWARTETES ERGEBNIS" if de else "EXPECTED RESULT",
        "report": "BERICHT" if de else "REPORT",
        "no_report": "Der Sub-Agent hat keinen Bericht geliefert." if de else "The sub-agent delivered no report.",
    }


@dataclass
class SubAgentPlugin:
    name: str = "subagent"

    # ── Plugin settings (settings.json next to this module) ──────────
    @property
    def credential_fields(self) -> list[CredentialField]:
        return [
            CredentialField(
                env_key=ALLOWED_TIERS_KEY,
                label_key="subagent_cred_allowed_tiers",
                options=_TIER_OPTIONS,
                default=DEFAULT_ALLOWED_TIERS,
            ),
            CredentialField(
                env_key=MAX_DEPTH_KEY,
                label_key="subagent_cred_max_depth",
                options=[("1", "1"), ("2", "2"), ("3", "3")],
                default=DEFAULT_MAX_DEPTH,
            ),
            CredentialField(
                env_key=DELEGATE_OTHERS_KEY,
                label_key="subagent_cred_delegate_others",
                options=[("0", "subagent_opt_off"), ("1", "subagent_opt_on")],
                default=DEFAULT_DELEGATE_OTHERS,
            ),
        ]

    def _load_settings(self) -> dict[str, str]:
        return load_plugin_settings(__file__)

    def _save_settings(self, settings: dict[str, str]) -> None:
        save_plugin_settings(__file__, settings)

    def settings(self) -> SubAgentSettings:
        return _parse_settings(self._load_settings())

    def is_available(self) -> bool:
        return True

    # ── Tools ───────────────────────────────────────────────────────
    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        settings = self.settings()
        depth = int(ctx.metadata.get(DEPTH_METADATA_KEY, 0))
        if depth >= settings.max_depth:
            # A sub-agent at the last allowed level gets no delegation tool:
            # this is the recursion bound.
            return []

        candidates = delegation_candidates(ctx) if settings.delegate_to_other_agents else []
        properties: dict[str, Any] = {
            "task": {
                "type": "string",
                "description": (
                    "The complete task, self-contained: everything the sub-agent "
                    "must know, because it sees neither the conversation nor your memory."
                ),
            },
            "expected_result": {
                "type": "string",
                "description": "What the report must contain so you can continue.",
            },
        }
        required = ["task", "expected_result"]
        if candidates:
            properties["agent"] = {
                "type": "string",
                "enum": candidates,
                "description": (
                    "Run the sub-agent with this agent's model and tool list instead "
                    "of your own (a different model means a model swap per call). "
                    "Pick the agent whose tool groups fit the task.\n"
                    + delegation_legend(ctx, settings, depth, candidates)
                ),
            }

        async def _delegate(
            task: str, expected_result: str, agent: Optional[str] = None,
        ) -> AsyncGenerator[dict[str, Any], None]:
            async for event in run_subagent(
                ctx, settings, depth, task, expected_result, agent, candidates,
            ):
                yield event

        return [
            Tool(
                name=TOOL_NAME,
                tier=TIER_READONLY,
                description=load_tool_description(__file__, TOOL_NAME),
                parameters={"type": "object", "properties": properties, "required": required},
                executor=_delegate,
            ),
        ]

    def get_prompt_instructions(self, lang: str, granted_tools: "set[str] | None" = None) -> str:
        return load_plugin_instructions(self, lang, granted_tools)

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        if tool_name != TOOL_NAME:
            return ""
        task = str(tool_args.get("task", "")).strip().replace("\n", " ")
        return f"{_transcript_labels(lang)['title']}: {task[:70]}"


async def build_subagent_toolkit(
    ctx: PluginContext, settings: SubAgentSettings, depth: int, target_agent: str, task: str,
) -> Optional[ToolKit]:
    """The sub-agent's toolkit: the target agent's normal kit (same source and
    tier ceiling as the caller, memory off, depth+1 so the recursion bound
    applies), reduced to the tiers the plugin setting allows."""
    _, toolkit = await prepare_agent_toolkit(
        target_agent,
        task,
        lang=ctx.lang,
        memory_enabled=False,
        research_tools_enabled=True,
        state=ctx.state,
        session_id=ctx.session_id,
        llm_history=[],
        max_tier=ctx.max_tier,
        source=ctx.source,
        metadata={**ctx.metadata, DEPTH_METADATA_KEY: depth + 1},
    )
    if toolkit is None:
        return None
    tools = within_allowed_tiers(toolkit.tools, settings)
    if not tools:
        return None
    return ToolKit(
        tools=tools,
        _session_id=ctx.session_id,
        _agent_id=target_agent,
        _source=ctx.source,
        _max_tier=ctx.max_tier,
    )


async def run_subagent(
    ctx: PluginContext,
    settings: SubAgentSettings,
    depth: int,
    task: str,
    expected_result: str,
    agent: Optional[str],
    candidates: list[str],
) -> AsyncGenerator[dict[str, Any], None]:
    """Executor body: yields ``progress`` events while the sub-agent works,
    one ``collapsible`` with the full transcript, and the ``result``."""
    from ....lib.agent_config import get_agent_config
    from ....lib.context_manager import estimate_tokens, estimate_toolkit_tokens
    from ....lib.prompt_loader import get_subagent_system_prompt, load_prompt
    from ....lib.tool_output_cap import budget_var, compute_budget

    labels = _transcript_labels(ctx.lang)
    target_agent = agent or ctx.agent_id
    if agent is not None and agent not in candidates:
        yield {"result": json.dumps({"error": f"agent '{agent}' is not available for delegation"})}
        return
    cfg = get_agent_config(target_agent)
    if cfg is None:
        yield {"result": json.dumps({"error": f"unknown agent '{target_agent}'"})}
        return
    agent_label = f"{cfg.display_name} ({labels['title'][2:].strip()})"

    toolkit = await build_subagent_toolkit(ctx, settings, depth, target_agent, task)
    granted = {t.name for t in toolkit.tools} if toolkit else set()
    system_prompt = get_subagent_system_prompt(ctx.lang, granted, source=ctx.source)
    user_text = load_prompt("shared/subagent_task", lang=ctx.lang, task=task, expected_result=expected_result)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]

    params = resolve_run_params(target_agent, ctx.state)
    options = build_llm_options(ctx.state, target_agent, params.temperature, params.num_ctx)

    transcript: list[str] = [f"{labels['task']}:\n{task}\n\n{labels['expected']}:\n{expected_result}"]
    # Text of the current model round; flushed into the transcript before
    # each tool call, so thinking, text and tools read in the order they ran.
    round_text: list[str] = []
    report = ""
    pipeline_result: Any = None

    def flush_round(final: bool) -> None:
        text = "".join(round_text)
        round_text.clear()
        # Backends deliver reasoning inline as <think>…</think>.
        for block in _THINK_RE.findall(text):
            if block.strip():
                transcript.append(f"[{labels['thinking']}]\n{block.strip()}")
        plain = _THINK_RE.sub("", text).strip()
        # The last round's text is the report, shown once below.
        if plain and not final:
            transcript.append(f"[{cfg.display_name}]\n{plain}")

    yield {"progress": f"{labels['title']} {cfg.display_name}: {params.model}"}

    # Own tool-output budget for the inner loop (ContextVar is task-local;
    # reset afterwards so the caller's budget is untouched).
    sys_tok = estimate_tokens([{"content": system_prompt}], model_name=params.model)
    task_tok = estimate_tokens([{"content": user_text}], model_name=params.model)
    tools_tok = estimate_toolkit_tokens(toolkit, model_name=params.model)
    budget_token = budget_var.set(compute_budget(params.num_ctx, sys_tok, task_tok, 0, tools_tok))
    llm_client = LLMClient(backend_type=params.backend_type, base_url=params.backend_url)
    try:
        async for event in run_llm_stream(
            llm_client, params.model, messages, options, agent_label,
            toolkit=toolkit, retry=False,
        ):
            kind = event.get("type")
            if kind == "content":
                round_text.append(event.get("text", ""))
            elif kind == "thinking":
                round_text.append(f"<think>{event.get('text', '')}</think>")
            elif kind == "tool_call":
                flush_round(final=False)
                name = event.get("name", "")
                transcript.append(format_transcript_call(labels["call"], name, event.get("arguments", "")))
                yield {"progress": f"{labels['title']} {cfg.display_name}: {name}"}
            elif kind == "tool_progress":
                yield {"progress": f"{labels['title']} {cfg.display_name}: {event.get('message', '')}"}
            elif kind == "tool_result":
                transcript.append(f"[{labels['result']}]\n{event.get('result', '')}")
            elif kind == "pipeline_result":
                pipeline_result = event["result"]
                report = pipeline_result.text_clean.strip()
    finally:
        budget_var.reset(budget_token)
        await llm_client.close()

    flush_round(final=True)
    transcript.append(f"[{labels['report']}]\n{report or labels['no_report']}")
    if pipeline_result is not None:
        # Same performance line as below a main agent's answer (one builder),
        # with the backend the sub-agent ran on.
        transcript.append(performance_footer_text(
            {**pipeline_result.metadata_dict, "backend_type": params.backend_type},
        ))

    # The transcript, then everything the sub-agent's own tools produced for
    # the bubble (nested transcripts, sources, sandbox pages, camera images,
    # VLM descriptions): one artifact list for the caller's turn, shown where
    # the caller delegated, exactly as if the caller's tools had produced it.
    transcript_artifact = BubbleArtifact(KIND_COLLAPSIBLE, {
        "title": f"{labels['title']} {cfg.display_name}: {_short(task)}",
        "content": "\n\n".join(transcript),
    })
    inner = pipeline_result.artifacts if pipeline_result is not None else []
    yield {"artifacts": [a.to_dict() for a in (transcript_artifact, *inner)]}
    if not report:
        yield {"result": json.dumps({"error": labels["no_report"]})}
        return
    # The caller's model needs the sandbox URLs too (render_html takes them):
    # marker lines lead the report so a capped result cannot cut them off. The
    # caller's pipeline already has these pages as artifacts and does not show
    # them twice.
    markers = [
        f"{SANDBOX_HTML_URL_MARKER}{a.data['url']}" for a in inner if a.kind == KIND_SANDBOX_HTML
    ] + [
        f"{SANDBOX_IMAGE_URL_MARKER}{a.data['url']}" for a in inner if a.kind == KIND_SANDBOX_IMAGE
    ]
    yield {"result": "\n".join([*markers, report]) if markers else report}


plugin = SubAgentPlugin()
