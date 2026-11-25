"""Sub-agent plugin — delegate a self-contained task to a fresh model run.

The main agent calls ``delegate_task`` like any other tool. The plugin runs a
sub-agent: a new inference with its own context window, its own toolkit and
its own tool loop, but without the caller's persona, conversation or memory.
Delegated to another main agent (``agent`` parameter), it carries that agent's
identity and personality. Only the sub-agent's final report comes back as the tool result; the full
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
from dataclasses import dataclass, replace
from typing import Any
from collections.abc import AsyncGenerator

from ....lib.function_calling import Tool
from ....lib.plugin_base import (
    CredentialField,
    PluginContext,
    load_plugin_instructions,
    load_plugin_settings,
    load_tool_description,
    load_tool_parameters,
    plugin_dir_of,
    plugin_i18n_text,
    save_plugin_settings,
)
from ....lib.security import (
    TIER_COMMUNICATE,
    TIER_READONLY,
    TIER_WRITE_DATA,
    TIER_WRITE_SYSTEM,
)
from ....lib.subagent_run import DEPTH_METADATA_KEY, SubagentSession, transcript_labels, within_tiers
from ....lib.task_plan import TaskPlan, plan_parameters_schema, plan_problem
from ....lib.user_error import Invalid

TOOL_NAME = "delegate_task"
PLAN_TOOL_NAME = "run_task_plan"

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


def _parse_settings(raw: dict[str, str]) -> SubAgentSettings:
    tiers = raw.get(ALLOWED_TIERS_KEY, DEFAULT_ALLOWED_TIERS)
    return SubAgentSettings(
        allowed_tiers=frozenset(int(t) for t in tiers.split(TIER_SEPARATOR) if t.strip()),
        max_depth=int(raw.get(MAX_DEPTH_KEY, DEFAULT_MAX_DEPTH)),
        delegate_to_other_agents=raw.get(DELEGATE_OTHERS_KEY, DEFAULT_DELEGATE_OTHERS) == "1",
    )


def delegation_candidates(ctx: PluginContext) -> list[str]:
    """Agents a sub-agent could run as: every selectable agent except the caller
    (no system agent, none switched off). Each brings its own identity and
    personality, so none is a useless option even with the caller's model and
    tool list. Derived on every toolkit build."""
    from ....lib.agent_config import selectable_agents
    return [agent_id for agent_id in selectable_agents() if agent_id != ctx.agent_id]


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
        (owner, within_tiers(filter_tools_by_tier(tools, ctx.max_tier), settings.allowed_tiers))
        for owner, tools in collect_plugin_tools(sub_ctx)
    ]

    agent_lines: list[str] = []
    described: dict[str, str] = {}
    for agent_id in candidates:
        cfg = get_agent_config(agent_id)
        assert cfg is not None  # the candidates are configured agents (delegation_candidates)
        whitelist = set(cfg.tools)
        groups: list[str] = []
        for owner, tools in offered:
            if any(t.name in whitelist for t in tools):
                name = plugin_display_name(owner, ctx.lang)
                groups.append(name)
                described.setdefault(name, plugin_description(owner, ctx.lang))
        agent_lines.append(f"- {agent_id} ({cfg.display_name}): {', '.join(groups) or '-'}")

    plugin_lines = [f"- {name}: {text}" for name, text in described.items()]
    return "\n".join([
        "Tools per agent:", *agent_lines,
        "Tool groups:", *plugin_lines,
    ])


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
        if candidates:
            parameters = load_tool_parameters(__file__, TOOL_NAME, enums={"agent": candidates})
            agent_param = parameters["properties"]["agent"]
            agent_param["description"] += "\n" + delegation_legend(ctx, settings, depth, candidates)
        else:
            # No other agent to run as: the parameter is not offered at all.
            parameters = load_tool_parameters(__file__, TOOL_NAME)
            del parameters["properties"]["agent"]

        async def _delegate(
            task: str, expected_result: str, agent: str | None = None,
        ) -> AsyncGenerator[dict[str, Any], None]:
            async for event in run_subagent(
                ctx, settings, depth, task, expected_result, agent, candidates,
            ):
                yield event

        async def _run_plan(**plan: Any) -> AsyncGenerator[dict[str, Any], None]:
            async for event in run_task_plan(ctx, settings, depth, plan):
                yield event

        plan_parameters = load_tool_parameters(__file__, PLAN_TOOL_NAME)
        # The fields are the one plan description of lib/task_plan.py; here a plan needs its frame and its parts.
        plan_parameters["properties"] = plan_parameters_schema()
        plan_parameters["required"] = ["frame", "parts"]

        return [
            Tool(
                name=TOOL_NAME,
                tier=TIER_READONLY,
                description=self._description(ctx, settings),
                parameters=parameters,
                executor=_delegate,
            ),
            Tool(
                name=PLAN_TOOL_NAME,
                tier=TIER_READONLY,
                description=load_tool_description(__file__, PLAN_TOOL_NAME),
                parameters=plan_parameters,
                executor=_run_plan,
            ),
        ]

    @staticmethod
    def _description(ctx: PluginContext, settings: SubAgentSettings) -> str:
        """The tool description; when the channel leaves sub-agents only the
        read-only tier (e.g. the Echo: channel tier 1, sub-agents 0 and 2),
        it says so — delegating a writing or printing task there cannot work
        (10.10.2026: two futile delegations before AIfred found another way)."""
        description = load_tool_description(__file__, TOOL_NAME)
        reachable = {tier for tier in settings.allowed_tiers if tier <= ctx.max_tier}
        if reachable <= {TIER_READONLY}:
            description += "\n\n" + load_tool_description(__file__, "delegate_task_read_only")
        return description

    def get_prompt_instructions(self, lang: str, granted_tools: set[str]) -> str:
        return load_plugin_instructions(self, lang, granted_tools)

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        if tool_name == PLAN_TOOL_NAME:
            frame = str(tool_args.get("frame", "")).strip().replace("\n", " ")
            return plugin_i18n_text(plugin_dir_of(self), "tool_run_task_plan", lang).format(frame=frame[:70])
        if tool_name != TOOL_NAME:
            return ""
        task = str(tool_args.get("task", "")).strip().replace("\n", " ")
        return f"{transcript_labels(lang)['title']}: {task[:70]}"


async def run_subagent(
    ctx: PluginContext,
    settings: SubAgentSettings,
    depth: int,
    task: str,
    expected_result: str,
    agent: str | None,
    candidates: list[str],
) -> AsyncGenerator[dict[str, Any], None]:
    """Executor body of ``delegate_task``: the delegation rules (is the agent a candidate) and the plugin's
    settings, then the run itself in the lib."""
    if agent is not None and agent not in candidates:
        yield {"result": json.dumps({"error": f"agent '{agent}' is not available for delegation"})}
        return
    session = SubagentSession(
        ctx,
        allowed_tiers=settings.allowed_tiers,
        depth=depth,
        target_agent=agent or ctx.agent_id,
        task=task,
        expected_result=expected_result,
        with_persona=agent is not None,
    )
    async for event in session.run():
        yield event


async def run_task_plan(
    ctx: PluginContext, settings: SubAgentSettings, depth: int, arguments: dict[str, Any],
) -> AsyncGenerator[dict[str, Any], None]:
    """Executor body of ``run_task_plan``: the check of the plan, then the engine in the lib with the plugin's
    settings; the result is the text of the findings and the line of what was checked."""
    from ....lib.plan_engine import coverage_line, render_results, run_plan

    problem = plan_problem(arguments) or (None if arguments.get("parts") else Invalid("plan_no_parts"))
    if problem is not None:
        yield {"result": json.dumps({"error": str(problem)})}
        return
    async for event in run_plan(
        TaskPlan.from_payload(arguments), ctx, allowed_tiers=settings.allowed_tiers, depth=depth,
    ):
        if "plan_result" in event:
            result = event["plan_result"]
            # The line leads: a capped result is cut at its tail, and what was not checked must survive.
            yield {"result": f"{coverage_line(result, ctx.lang)}\n\n{render_results(result, ctx.lang)}"}
        elif "part_done" not in event:   # the tool's bubble gets the artifacts passed on as they come
            yield event


plugin = SubAgentPlugin()
