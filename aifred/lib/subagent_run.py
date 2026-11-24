"""One sub-agent run: a fresh model inference with its own context, toolkit and tool loop.

The core of the ``delegate_task`` tool (plugin ``subagent``) and of the plan engine (``task_plan``): both start a
``SubagentSession``. What is the plugin's own (its settings: allowed tiers, depth) comes in as parameters; the
transcript labels live in ``lib/i18n`` (``subagent_transcript_*``), one text for every caller.

``run`` yields ``progress``, ``work``, ``artifacts`` and ``result`` events like a tool executor. ``follow_up`` sends one
more user message into the same run (same toolkit instance, the report as the assistant's turn before it).
Design: docs/de/architecture/subagent-plugin.md.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass, replace
from typing import Any

from ..backends.base import LLMOptions
from ..theme import DELEGATION_ICON
from .agent_memory import prepare_agent_toolkit
from .bubble import (
    KIND_COLLAPSIBLE,
    KIND_SANDBOX_HTML,
    KIND_SANDBOX_IMAGE,
    BubbleArtifact,
)
from .formatting import performance_footer_text
from .function_calling import Tool, ToolKit
from .i18n import t
from .llm_client import LLMClient, build_llm_options
from .llm_pipeline import run_llm_stream
from .perf_metrics import InferenceWork
from .plugin_base import PluginContext
from .sandbox import SANDBOX_HTML_URL_MARKER, SANDBOX_IMAGE_URL_MARKER

SUBAGENT_ICON = DELEGATION_ICON

# PluginContext.metadata key carrying the nesting depth of the current run.
# A sub-agent's toolkit is built with depth+1; at max depth the plugin offers
# no tool at all, so recursion is bounded structurally, without a counter.
DEPTH_METADATA_KEY = "subagent_depth"

_TRANSCRIPT_LABEL_KEYS = ("title", "task", "thinking", "call", "result", "expected", "report", "no_report", "follow_up")
_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)
_TITLE_LEN = 60


@dataclass(frozen=True)
class RunParams:
    """Everything one inference needs, resolved through the same helpers the
    browser path (multi_agent) and the hub path (message_processor) use."""

    backend_type: str
    backend_url: str | None
    model: str
    num_ctx: int
    temperature: float


def transcript_labels(lang: str) -> dict[str, str]:
    return {key: t(f"subagent_transcript_{key}", lang=lang) for key in _TRANSCRIPT_LABEL_KEYS}


def within_tiers(tools: list[Tool], allowed_tiers: frozenset[int]) -> list[Tool]:
    """The tools whose tier is allowed."""
    return [tool for tool in tools if tool.tier in allowed_tiers]


def inherit_reasoning(own: LLMOptions, caller: LLMOptions) -> LLMOptions:
    """The sub-agent thinks as deeply as the main agent that delegated: thinking
    switch and reasoning level come from the caller; temperature and sampling
    stay the target agent's. Before, a target agent with no level of its own
    sent none, and the chat template fell back to its default (xhigh on
    Qwen3.8) — deeper than the caller had chosen."""
    return replace(own, enable_thinking=caller.enable_thinking, reasoning_effort=caller.reasoning_effort)


def resolve_run_params(agent_id: str) -> RunParams:
    """Model, context and temperature for the sub-agent's inference, from settings.json."""
    from .agent_settings import get_persisted_tuning
    from .config import (
        BACKEND_URLS,
        DEFAULT_SETTINGS,
        DEFAULT_TEMPERATURE,
        get_effective_model_from_settings,
    )
    from .research.context_utils import get_stateless_num_ctx
    from .settings import load_settings
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


async def build_subagent_toolkit(
    ctx: PluginContext, allowed_tiers: frozenset[int], depth: int, target_agent: str, task: str,
    extra_tools: Sequence[Tool] = (),
) -> ToolKit | None:
    """The sub-agent's toolkit: the target agent's normal kit (same source and
    tier ceiling as the caller, memory off, depth+1 so the recursion bound
    applies), reduced to the allowed tiers; the caller's ``extra_tools`` are added as they are."""
    _, toolkit = await prepare_agent_toolkit(
        target_agent,
        task,
        lang=ctx.lang,
        memory_enabled=False,
        research_tools_enabled=True,
        session_id=ctx.session_id,
        llm_history=[],
        max_tier=ctx.max_tier,
        source=ctx.source,
        metadata={**ctx.metadata, DEPTH_METADATA_KEY: depth + 1},
    )
    tools = within_tiers(toolkit.tools, allowed_tiers) if toolkit is not None else []
    tools += extra_tools
    if not tools:
        return None
    return ToolKit(
        tools=tools,
        _session_id=ctx.session_id,
        _agent_id=target_agent,
        _source=ctx.source,
        _max_tier=ctx.max_tier,
    )


def subagent_messages(
    ctx: PluginContext, persona_agent: str | None, toolkit: ToolKit | None, task: str, expected_result: str,
) -> list[dict[str, Any]]:
    """The sub-agent's request: a worker for its caller. The persona of ``persona_agent`` (its value may sit in
    the prompt, e.g. a review method), none when None; instructions for exactly the tools
    it holds; no memory. The task is stamped like every user turn (the disciplines promise date and time from it)."""
    from .message_builder import stamp_user_turn, user_turn_stamp
    from .prompt_loader import agent_system_prompt, load_prompt
    from .security import in_owner_context

    granted = {tool.name for tool in toolkit.tools} if toolkit else set()
    system_prompt = agent_system_prompt(
        persona_agent, load_prompt("shared/subagent_frame", lang=ctx.lang), ctx.lang,
        answer_channel=ctx.source, external=not in_owner_context(ctx.source, ctx.trust), tools=granted, memory=False,
        answers_user=False,
    )
    user_text = stamp_user_turn(
        load_prompt("shared/subagent_task", lang=ctx.lang, task=task, expected_result=expected_result),
        user_turn_stamp(),
    )
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_text}]


class SubagentSession:
    """One sub-agent: ``run`` for the first request, ``follow_up`` for more in the same run.

    ``allowed_tiers`` and ``depth`` are the caller's rules (the plugin's settings); ``with_persona`` lets the
    sub-agent carry the identity of ``target_agent`` (delegated to another agent); ``extra_tools`` join the toolkit
    whatever the tiers. ``toolkit`` is readable by the caller once ``run`` has built it."""

    def __init__(
        self,
        ctx: PluginContext,
        *,
        allowed_tiers: frozenset[int],
        depth: int,
        target_agent: str,
        task: str,
        expected_result: str,
        with_persona: bool = False,
        extra_tools: Sequence[Tool] = (),
    ) -> None:
        self.ctx = ctx
        self.allowed_tiers = allowed_tiers
        self.depth = depth
        self.target_agent = target_agent
        self.task = task
        self.expected_result = expected_result
        self.with_persona = with_persona
        self.extra_tools = list(extra_tools)
        self._toolkit: ToolKit | None = None
        self._messages: list[dict[str, Any]] = []

    @property
    def toolkit(self) -> ToolKit | None:
        return self._toolkit

    async def run(self) -> AsyncGenerator[dict[str, Any], None]:
        """The first request: yields ``progress`` while the sub-agent works, ``work``, one ``artifacts`` with the
        full transcript, and the ``result`` (the report)."""
        from .agent_config import get_agent_config

        if get_agent_config(self.target_agent) is None:
            yield {"result": json.dumps({"error": f"unknown agent '{self.target_agent}'"})}
            return
        self._toolkit = await build_subagent_toolkit(
            self.ctx, self.allowed_tiers, self.depth, self.target_agent, self.task, self.extra_tools,
        )
        self._messages = subagent_messages(
            self.ctx, self.target_agent if self.with_persona else None, self._toolkit, self.task, self.expected_result,
        )
        labels = transcript_labels(self.ctx.lang)
        intro = f"{labels['task']}:\n{self.task}\n\n{labels['expected']}:\n{self.expected_result}"
        async for event in self._execute(intro, self.task):
            yield event

    async def follow_up(self, text: str) -> AsyncGenerator[dict[str, Any], None]:
        """One more user message into the same run: the same toolkit, the messages so far with the earlier report
        as the assistant's turn. Yields the same events as ``run``; the result is the new report."""
        if not self._messages:
            raise RuntimeError("follow_up before run")
        self._messages.append({"role": "user", "content": text})
        intro = f"{transcript_labels(self.ctx.lang)['follow_up']}:\n{text}"
        async for event in self._execute(intro, text):
            yield event

    async def _execute(self, intro: str, title_text: str) -> AsyncGenerator[dict[str, Any], None]:
        from .agent_config import get_agent_config
        from .context_manager import (
            LoopCompression,
            estimate_tokens,
            estimate_toolkit_tokens,
        )
        from .tool_output_cap import budget_var, compute_budget

        # The tool loop appends its rounds to the list it gets; the session keeps only request and reports.
        ctx, toolkit, messages = self.ctx, self._toolkit, list(self._messages)
        labels = transcript_labels(ctx.lang)
        cfg = get_agent_config(self.target_agent)
        assert cfg is not None  # checked by run
        agent_label = f"{cfg.display_name} ({labels['title']})"
        progress_prefix = f"{SUBAGENT_ICON} {labels['title']} {cfg.display_name}"

        params = resolve_run_params(self.target_agent)
        caller_model = resolve_run_params(ctx.agent_id).model
        options = inherit_reasoning(
            build_llm_options(None, self.target_agent, params.model, params.temperature, params.num_ctx),
            build_llm_options(None, ctx.agent_id, caller_model, params.temperature, params.num_ctx),
        )

        transcript: list[str] = [intro]
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

        yield {"progress": f"{progress_prefix}: {params.model}"}

        # Own tool-output budget for the inner loop (ContextVar is task-local;
        # reset afterwards so the caller's budget is untouched).
        sys_tok = estimate_tokens([{"content": messages[0]["content"]}], model_name=params.model)
        # Everything but the system prompt: the task, and in a follow-up the earlier reports and the new text.
        task_tok = estimate_tokens(messages[1:], model_name=params.model)
        tools_tok = estimate_toolkit_tokens(toolkit, model_name=params.model)
        budget_token = budget_var.set(compute_budget(params.num_ctx, sys_tok, task_tok, 0, tools_tok))
        llm_client = LLMClient(backend_type=params.backend_type, base_url=params.backend_url)
        try:
            # Its own loop compresses with its own model and client: the sub-agent's backend can be
            # another one than the caller's, and the caller's turn is not known here.
            async for event in run_llm_stream(
                llm_client, params.model, messages, options, agent_label,
                source=ctx.source, toolkit=toolkit,
                compression=LoopCompression(llm_client, params.model, ctx.lang),
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
                    yield {"progress": f"{progress_prefix}: {name}"}
                elif kind == "tool_progress":
                    yield {"progress": f"{progress_prefix}: {event.get('message', '')}"}
                elif kind == "tool_result":
                    transcript.append(f"[{labels['result']}]\n{event.get('result', '')}")
                elif kind == "pipeline_result":
                    pipeline_result = event["result"]
                    report = pipeline_result.text_clean.strip()
        except Exception:
            # The requests that did run are unknown now: the caller's turn
            # metrics must not look complete without them.
            yield {"work": InferenceWork.unmeasured().to_dict()}
            raise
        finally:
            budget_var.reset(budget_token)
            await llm_client.close()

        # The sub-agent's own "done" line (tokens, rates, PP, thinking) in the
        # debug console and log, like a main agent's; before, only its tool calls
        # showed up there.
        if pipeline_result is not None:
            yield {"progress": pipeline_result.debug_msg}
        # The sub-agent's inference is part of the caller's turn: its footer sums
        # this work into its own rates (the transcript footer below shows only the
        # sub-agent's).
        yield {"work": pipeline_result.metrics["work"] if pipeline_result is not None
               else InferenceWork.unmeasured().to_dict()}
        flush_round(final=True)
        transcript.append(f"[{labels['report']}]\n{report or labels['no_report']}")
        # Same performance line as below a main agent's answer (one builder);
        # shown below the transcript text.
        footer = performance_footer_text(pipeline_result.metadata_dict) if pipeline_result is not None else ""

        # The transcript, then everything the sub-agent's own tools produced for
        # the bubble (nested transcripts, sources, sandbox pages, camera images,
        # VLM descriptions): one artifact list for the caller's turn, shown where
        # the caller delegated, exactly as if the caller's tools had produced it.
        transcript_artifact = BubbleArtifact(KIND_COLLAPSIBLE, {
            "icon": SUBAGENT_ICON,
            "title": f"{labels['title']} {cfg.display_name}: {_short(title_text)}",
            "content": "\n\n".join(transcript),
            "footer": footer,
        })
        inner = pipeline_result.artifacts if pipeline_result is not None else []
        yield {"artifacts": [a.to_dict() for a in (transcript_artifact, *inner)]}
        # What a follow-up continues from: the report is the assistant's turn, even an empty one, so that
        # user and assistant alternate (some chat templates refuse two user messages in a row).
        self._messages.append({"role": "assistant", "content": report})
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
