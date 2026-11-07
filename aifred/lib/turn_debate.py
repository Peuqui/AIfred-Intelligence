"""The multi-agent modes of a server turn, without a Reflex state (port of lib/multi_agent.py).

Seats, not names (lib/debate_seats.py): the author (the agent that answered first) refines and
defends, the session's critic critiques and attacks, its judge mediates and judges. Each speaks with
its own persona and the seat's task prompt. A seat whose agent is gone rejects the turn before
anyone speaks.

- critical_review: the direct answer, then one critique.
- auto_consensus: the direct answer, then per round critique, synthesis, vote and refinement, until
  the votes reach ``consensus_type`` or the rounds ``max_debate_rounds``.
- tribunal: the direct answer, then ``max_debate_rounds`` of attack and defense, the verdict last.
- symposion: the session's agents speak in turn for ``max_debate_rounds``, each sees all earlier
  contributions; with one agent it answers directly, with none the turn's addressing decides.

Every speaker runs through the turn's three steps (``turn_service``: call, stream, finish); the
history is read fresh from the session for each call, so every speaker sees the ones before and a
compression between the calls.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .agent_config import (
    BASE_AGENT,
    AgentConfig,
    agent_display_name,
    get_agent_config,
    selectable_agents,
)
from .debate_seats import (
    CHOSEN_SEATS,
    SEAT_CRITIC,
    SEAT_JUDGE,
    SEAT_MAIN,
    SeatError,
    check_seat_agent,
    seat_prompt,
)
from .debug_bus import debug
from .formatting import format_number
from .turn_service import (
    AgentCall,
    TurnContext,
    TurnRejected,
    agent_context,
    agent_identity,
    agent_model,
    agent_toolkit,
    compress_history,
    finish_call,
    measure_prompt,
    session_llm_history,
    stream_call,
)

if TYPE_CHECKING:
    from ..backends.base import LLMOptions
    from .llm_pipeline import PipelineResult
    from .runtime_settings import RuntimeSettings

# The agent's prompt for multi-agent modes (its task prompt); every debate speaker has it in front of
# the task of its seat.
_BASE_PROMPT = "task"

# The seats that speak after the direct answer, per mode.
_DEBATE_SEATS: dict[str, tuple[str, ...]] = {
    "critical_review": (SEAT_CRITIC,),
    "auto_consensus": (SEAT_MAIN, SEAT_CRITIC, SEAT_JUDGE),
    "tribunal": (SEAT_MAIN, SEAT_CRITIC, SEAT_JUDGE),
}

def chosen_seats(mode: str) -> tuple[str, ...]:
    """The seats of ``mode`` a session chooses (critic, judge); standard and symposion have none."""
    return tuple(seat for seat in _DEBATE_SEATS.get(mode, ()) if seat != SEAT_MAIN)




# ── Who speaks ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TurnPlan:
    """Who speaks in a turn."""

    responder: str | None                                    # answers the user directly; None: the symposion
    debate: dict[str, str] = field(default_factory=dict)     # seat → agent speaking in the debate after the answer
    symposion: tuple[str, ...] = ()                          # the round table, at least two agents
    seats: dict[str, str] = field(default_factory=dict)      # every seat → its agent, as the prompts name them

    @property
    def agents(self) -> list[str]:
        """Every agent of the turn, once: the history compression reckons with all of them."""
        speakers = [self.responder] if self.responder else list(self.symposion)
        return list(dict.fromkeys([*speakers, *self.debate.values()]))


def plan_turn(
    mode: str, routed: str, addressed_to: str | None, symposion_agents: list[str], seats: dict[str, str],
) -> TurnPlan:
    """Who speaks in ``mode``. ``routed`` is the turn's addressee (lib/turn_service._route) and the
    author of the first answer; ``seats`` the session's critic and judge (debate_seats.session_seats)."""
    if mode == "standard":
        return TurnPlan(_speaker(routed))
    if mode == "symposion":
        return _plan_symposion(routed, symposion_agents)
    if mode not in _DEBATE_SEATS:
        raise TurnRejected("mode", f"Unknown multi-agent mode '{mode}'")
    _speaker(routed)
    if addressed_to == BASE_AGENT:   # addressing AIfred by name in a debate mode asks for his answer alone
        return TurnPlan(routed)
    seated = {SEAT_MAIN: routed, **{seat: seats[seat] for seat in CHOSEN_SEATS}}
    try:
        for seat in _DEBATE_SEATS[mode]:
            if seat != SEAT_MAIN:
                check_seat_agent(seat, seated[seat])
    except SeatError as error:
        raise TurnRejected("seat", str(error)) from error
    return TurnPlan(routed, {seat: seated[seat] for seat in _DEBATE_SEATS[mode]}, seats=seated)


def _speaker(agent: str) -> str:
    """``agent`` may speak in a turn: it is selectable. One switched off (or gone) is refused out loud,
    never replaced by another."""
    if agent not in selectable_agents():
        config = get_agent_config(agent)
        why = "is switched off" if config is not None and not config.enabled else "is no agent a chat can address"
        raise TurnRejected("agent", f"{agent_display_name(agent)} {why}: choose another agent")
    return agent


def _plan_symposion(routed: str, agents: list[str]) -> TurnPlan:
    # An agent switched off leaves the round table. Fewer than two agents is a plain direct answer, so the
    # user can switch agents off mid-session and go on with one; the one selected answers, with none the
    # turn's addressing decides.
    selectable = selectable_agents()
    gone = [agent for agent in agents if agent not in selectable]
    if gone:
        debug(f"🏛️ Symposion without {', '.join(gone)}: switched off or gone")
        agents = [agent for agent in agents if agent in selectable]
    if len(agents) >= 2:
        return TurnPlan(None, symposion=tuple(agents))
    if len(agents) == 1:
        debug(f"🏛️ Symposion with a single agent → direct response by {agents[0]}")
        return TurnPlan(agents[0])
    debug("⚠️ Symposion: no agents selected → falling back to direct response")
    return TurnPlan(_speaker(routed))


# ── The debate's speakers ────────────────────────────────────────────────


@dataclass(frozen=True)
class _Member:
    """An agent of the debate with what its calls share across the rounds."""

    agent: str
    label: str
    emoji: str
    model: str
    num_ctx: int
    options: LLMOptions
    memory: str        # recalled once before the debate
    toolkit: Any
    memory_enabled: bool   # the session is not incognito: the memory layer of the prompt is there


async def _cast(ctx: TurnContext, seats: dict[str, str]) -> dict[str, _Member]:
    """Contexts, temperatures and options of the debate's agents, then their memories and tools."""
    from .agent_settings import resolve_agent_temperature
    from .llm_client import build_llm_options

    view = ctx.view
    configs = {seat: agent_identity(agent) for seat, agent in seats.items()}
    contexts = {seat: agent_context(view, agent) for seat, agent in seats.items()}
    for seat, (num_ctx, source) in contexts.items():
        debug(f"🎯 {configs[seat].display_name}: {format_number(num_ctx)} tok ({source})")
    limits = ", ".join(f"{configs[seat].display_name}={format_number(num_ctx)} tok" for seat, (num_ctx, _) in contexts.items())
    smallest = min(num_ctx for num_ctx, _ in contexts.values())
    debug(f"📊 Context limits: {limits}, Compression={format_number(smallest)} tok")
    temperatures = {seat: resolve_agent_temperature(view, agent) for seat, agent in seats.items()}
    debug("🌡️ Temps: " + ", ".join(
        f"{configs[seat].display_name}={format_number(temperature, 1)}" for seat, temperature in temperatures.items()
    ))

    members: dict[str, _Member] = {}
    for seat, agent in seats.items():
        config, num_ctx, model = configs[seat], contexts[seat][0], agent_model(view, agent)
        memory, toolkit = await agent_toolkit(ctx, agent)
        if memory:
            debug(f"🧠 Memory context recalled for {config.display_name}")
        members[seat] = _Member(
            agent, config.display_name, config.emoji, model, num_ctx,
            build_llm_options(view, agent, model, temperatures[seat], num_ctx), memory, toolkit, ctx.memory_enabled,
        )
    return members


def _base_system(member: _Member, lang: str, seats: dict[str, str]) -> str:
    from .prompt_loader import get_agent_system_prompt

    return get_agent_system_prompt(member.agent, _BASE_PROMPT, lang=lang, seats=seats, memory=member.memory_enabled)


def _seat_system(member: _Member, key: str, lang: str, seats: dict[str, str], **variables: Any) -> str:
    """The agent's multi-agent prompt, then the task of the seat it speaks in."""
    return f"{_base_system(member, lang, seats)}\n\n{seat_prompt(key, seats, lang, **variables)}"


def _with_memory(system_prompt: str, member: _Member) -> str:
    return f"{system_prompt}\n\n{member.memory}" if member.memory else system_prompt


def _prompt_tokens(*prompts: str) -> int:
    from .context_manager import estimate_tokens

    return sum(estimate_tokens([{"content": prompt}]) for prompt in prompts)


def _call(
    ctx: TurnContext,
    member: _Member,
    system_prompt: str,
    *,
    observer: bool = False,
    question: str = "",
    memory_before_question: bool = False,
    log_label: str = "",
) -> AgentCall:
    """A debate call: the system prompt, then the session's history as the member sees it (as an
    ``observer``, the judge, every reply is someone else's), then ``question`` (the refinement or
    defense task) when there is one."""
    from .message_builder import build_messages_from_llm_history, inject_before_question
    from .tool_output_cap import compute_budget

    history = build_messages_from_llm_history(
        session_llm_history(ctx.session_id), current_user_text=question, perspective=member.agent,
        detected_language=ctx.intent.language, observer=observer,
    )
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}, *history]
    if memory_before_question and member.memory:
        # At the end instead of the system prompt: the prefix cache keeps the history.
        inject_before_question(messages, member.memory)
    system_tokens, history_tokens, tool_tokens = measure_prompt(messages, member.toolkit, member.model)
    return AgentCall(
        member.agent, member.label, member.emoji, member.model, messages, member.options, member.toolkit,
        prompt_tokens=system_tokens + history_tokens,
        tool_budget=compute_budget(member.num_ctx, system_tokens, history_tokens, 0, tool_tokens),
        log_label=log_label or member.label,
    )


def _prompt_line(call: AgentCall, step: str, num_ctx: int) -> str:
    return f"📊 {call.label} {step}: {format_number(call.prompt_tokens)} / {format_number(num_ctx)} tokens"


def _llm_line(view: RuntimeSettings, agent: str) -> str:
    config = agent_identity(agent)
    return f"{config.emoji} {config.display_name}-LLM: {agent_model(view, agent)}"


async def _compress_before(ctx: TurnContext, plan: TurnPlan, member: _Member, system_prompt_tokens: int) -> None:
    """Between the speakers: the debate itself can fill the context of the next one."""
    from .context_manager import estimate_toolkit_tokens

    await compress_history(
        ctx, plan.agents, member.num_ctx, system_prompt_tokens,
        estimate_toolkit_tokens(member.toolkit, model_name=member.model), "History compressed",
    )


async def _speak(ctx: TurnContext, call: AgentCall) -> PipelineResult | None:
    result = await stream_call(ctx, call)
    if result is None:
        debug(f"❌ {call.log_label} stream returned no result")
    return result


# ── critical_review, auto_consensus, tribunal ────────────────────────────


async def run_debate(ctx: TurnContext, plan: TurnPlan, answer: str) -> None:
    """The debate after the direct ``answer`` (its full text)."""
    from .logging_utils import console_separator

    if ctx.mode == "tribunal":
        debug("⚖️ Multi-Agent: Tribunal starting...")
        await _tribunal(ctx, plan)
    else:
        debug(f"🏛️ Multi-Agent: {agent_display_name(plan.debate[SEAT_CRITIC])} analysis starting...")
        await _critique(ctx, plan, answer)
    console_separator()
    debug("────────────────────")


async def _critique(ctx: TurnContext, plan: TurnPlan, answer: str) -> None:
    """critical_review (one critique, the user decides) and auto_consensus (rounds until consensus)."""
    from .context_manager import strip_thinking_blocks
    from .logging_utils import log_raw_messages
    from .multi_agent import check_consensus, count_lgtm_votes

    lang, auto = ctx.intent.language, ctx.mode == "auto_consensus"
    debug(f"🔍 Debate START: mode={ctx.mode}, answer_len={format_number(len(answer))}")
    debug(_llm_line(ctx.view, plan.debate[SEAT_CRITIC]))
    members = await _cast(ctx, plan.debate)
    critic = members[SEAT_CRITIC]
    max_rounds = ctx.view.max_debate_rounds if auto else 1
    current_answer = answer
    votes: dict[str, bool] = {}
    consensus_round = 0
    round_num = 0
    for round_num in range(1, max_rounds + 1):
        # The round number keeps the critic from inventing "progress" in round 1.
        system_prompt = _seat_system(critic, "critic", lang, plan.seats, round_num=round_num)
        await _compress_before(ctx, plan, critic, _prompt_tokens(system_prompt))
        call = _call(ctx, critic, system_prompt, memory_before_question=True)
        debug(_prompt_line(call, f"R{round_num}", critic.num_ctx))
        log_raw_messages(f"{critic.label} R{round_num}", call.messages)
        result = await _speak(ctx, call)
        if result is None:
            break
        finish_call(ctx, call, result, mode="critical_review", round_num=round_num)
        critique = result.text
        if not auto:
            break

        judge = members[SEAT_JUDGE]
        if round_num == 1:
            debug(f"{judge.emoji} {judge.label}-LLM: {judge.model}")
        system_prompt = _with_memory(_seat_system(judge, "mediator", lang, plan.seats, round_num=round_num), judge)
        await _compress_before(ctx, plan, judge, _prompt_tokens(system_prompt))
        call = _call(ctx, judge, system_prompt, observer=True)
        debug(_prompt_line(call, f"R{round_num}", judge.num_ctx))
        log_raw_messages(f"{judge.label} R{round_num}", call.messages)
        result = await _speak(ctx, call)
        if result is None:
            break
        finish_call(ctx, call, result, mode="synthesis", round_num=round_num)
        synthesis = result.text

        # The vote slots of the old code are positions: answer, critique, synthesis.
        tags = count_lgtm_votes(current_answer, critique, synthesis)
        votes = {SEAT_MAIN: tags["alfred"], SEAT_CRITIC: tags["sokrates"], SEAT_JUDGE: tags["salomo"]}
        debug(_votes_line(members, votes, round_num))
        if check_consensus(votes, ctx.view.consensus_type):
            kind = "unanimous" if ctx.view.consensus_type == "unanimous" else "majority"
            debug(
                f"✅ Consensus reached in round {format_number(round_num)} "
                f"({format_number(sum(votes.values()))}/3 votes, {kind})"
            )
            consensus_round = round_num
            break
        if round_num == max_rounds:
            break

        main = members[SEAT_MAIN]
        question = seat_prompt(
            "refinement", plan.seats, lang,
            critique=strip_thinking_blocks(synthesis), user_interjection="", round_num=round_num + 1,
        )
        system_prompt = _with_memory(_base_system(main, lang, plan.seats), main)
        await _compress_before(ctx, plan, main, _prompt_tokens(system_prompt, question))
        call = _call(ctx, main, system_prompt, question=question, log_label=f"{main.label} Refinement")
        debug(_prompt_line(call, f"R{round_num + 1}", main.num_ctx))
        log_raw_messages(f"{main.label} R{round_num + 1}", call.messages)
        result = await _speak(ctx, call)
        if result is None:
            break
        finish_call(ctx, call, result, mode="refinement", round_num=round_num + 1)
        current_answer = result.text

    if auto:
        if consensus_round:
            debug(f"🎯 Debate finished: consensus after {format_number(consensus_round)} rounds")
        else:
            debug(f"⚠️ No consensus after {format_number(max_rounds)} rounds")
            if votes:
                debug(_votes_line(members, votes, round_num))


def _votes_line(members: dict[str, _Member], votes: dict[str, bool], round_num: int) -> str:
    marks = ", ".join(
        f"{members[seat].label} {'✅' if votes[seat] else '❌'}" for seat in (SEAT_MAIN, SEAT_CRITIC, SEAT_JUDGE)
    )
    return f"🗳️ Votes R{format_number(round_num)}: {marks} ({format_number(sum(votes.values()))}/3)"


async def _tribunal(ctx: TurnContext, plan: TurnPlan) -> None:
    """The critic attacks, the main agent defends, round by round; the judge speaks only at the end."""
    from .context_manager import strip_thinking_blocks

    lang = ctx.intent.language
    debug("⚖️ Tribunal mode started")
    for seat in (SEAT_CRITIC, SEAT_JUDGE):
        debug(_llm_line(ctx.view, plan.debate[seat]))
    members = await _cast(ctx, plan.debate)
    critic, judge, main = members[SEAT_CRITIC], members[SEAT_JUDGE], members[SEAT_MAIN]
    max_rounds = ctx.view.max_debate_rounds

    for round_num in range(1, max_rounds + 1):
        system_prompt = _seat_system(critic, "tribunal", lang, plan.seats, round_num=round_num)
        await _compress_before(ctx, plan, critic, _prompt_tokens(system_prompt))
        call = _call(ctx, critic, system_prompt, memory_before_question=True)
        result = await _speak(ctx, call)
        if result is None:
            break
        finish_call(ctx, call, result, mode="tribunal", round_num=round_num)
        if round_num == max_rounds:
            break

        # The defense may hold its position or revise it (unlike the refinement of auto_consensus).
        question = seat_prompt(
            "defense", plan.seats, lang,
            critique=strip_thinking_blocks(result.text), user_interjection="", round_num=round_num + 1,
        )
        system_prompt = _base_system(main, lang, plan.seats)
        await _compress_before(ctx, plan, main, _prompt_tokens(system_prompt, question))
        call = _call(ctx, main, system_prompt, question=question, log_label=f"{main.label} Defense")
        result = await _speak(ctx, call)
        if result is None:
            break
        finish_call(ctx, call, result, mode="tribunal", round_num=round_num + 1)

    debug(f"{judge.emoji} {judge.label} rendering verdict...")
    system_prompt = _with_memory(_seat_system(judge, "judge", lang, plan.seats), judge)
    await _compress_before(ctx, plan, judge, _prompt_tokens(system_prompt))
    call = _call(ctx, judge, system_prompt, observer=True)
    debug(_prompt_line(call, "Verdict", judge.num_ctx))
    result = await _speak(ctx, call)
    if result is None:
        raise RuntimeError(f"{judge.label} verdict stream returned no result")
    finish_call(ctx, call, result, mode="verdict", round_num=max_rounds)
    debug(f"⚖️ Tribunal completed after {format_number(max_rounds)} rounds + verdict")


# ── symposion ────────────────────────────────────────────────────────────


def _round_table(agents: Sequence[str]) -> list[tuple[str, AgentConfig]]:
    """The participants that can speak: selectable agents only, one switched off (or gone) leaves."""
    selectable = selectable_agents()
    return [(agent, selectable[agent]) for agent in agents if agent in selectable]


async def run_symposion(ctx: TurnContext, plan: TurnPlan) -> None:
    """The round table: every agent speaks in turn, round by round, and sees all contributions so
    far. No winner, no vote: a discussion from several perspectives."""
    from .context_manager import strip_thinking_blocks
    from .logging_utils import console_separator
    from .prompt_loader import load_prompt
    from .session_storage import get_session_config

    lang, max_rounds = ctx.intent.language, ctx.view.max_debate_rounds
    lineup = _round_table(plan.symposion)
    if len(lineup) < 2:
        debug("⚠️ Symposion requires at least 2 agents")
        return
    names = ", ".join(config.display_name for _, config in lineup)
    debug(f"🏛️ Symposion: {names} ({format_number(max_rounds)} rounds)")
    # From round 2 on: each speaker looks for what the earlier contributions left open.
    reflection = load_prompt("shared/symposion_reflection", lang=lang)
    conversation = _conversation(session_llm_history(ctx.session_id), lineup)

    for round_num in range(1, max_rounds + 1):
        # The lineup is read again every round: the user can change the selection meanwhile (or switch
        # an agent off), and every speaker is told the participants of the round it speaks in.
        lineup = _round_table(get_session_config(ctx.session_id)["symposion_agents"])
        if len(lineup) < 2:
            debug("⚠️ Symposion requires at least 2 agents")
            break
        round_names = ", ".join(config.display_name for _, config in lineup)
        if round_names != names:
            names = round_names
            debug(f"🏛️ Symposion lineup changed: {names}")
        rules = load_prompt("shared/symposion", lang=lang, participants=names)

        for agent, config in lineup:
            debug(f"{config.emoji} {config.display_name} (R{round_num})")
            call = await _symposion_call(
                ctx, agent, config, rules, reflection if round_num >= 2 else "", conversation, round_num,
            )
            result = await stream_call(ctx, call)
            if result is None:
                debug(f"❌ {config.display_name} returned no result")
                continue
            finish_call(ctx, call, result, mode="symposion", round_num=round_num)
            conversation.append({
                "role": "assistant", "agent": agent,
                "content": f"[{config.display_name}]: {strip_thinking_blocks(result.text)}",
            })

    debug(f"🏛️ Symposion done ({format_number(max_rounds)} rounds, {format_number(len(lineup))} agents)")
    console_separator()
    debug("────────────────────")


def _conversation(
    llm_history: list[dict[str, Any]], lineup: list[tuple[str, AgentConfig]],
) -> list[dict[str, str]]:
    """The session so far as the round table sees it; an assistant entry knows its agent by the
    speaker label (the agent's id in capitals, or its display name)."""
    by_label: dict[str, str] = {}
    for agent, config in lineup:
        by_label[agent.strip().upper()] = agent
        by_label[config.display_name.strip().upper()] = agent
    conversation: list[dict[str, str]] = []
    for entry in llm_history:
        content = entry.get("content", "")
        if entry.get("role") == "user":
            conversation.append({"role": "user", "content": content})
        elif entry.get("role") == "assistant":
            label = re.match(r"^\[([^\]]+)\]:\s*", content)
            speaker = by_label.get(label.group(1).strip().upper(), "") if label else ""
            conversation.append({"role": "assistant", "agent": speaker, "content": content})
    return conversation


async def _symposion_call(
    ctx: TurnContext,
    agent: str,
    config: AgentConfig,
    rules: str,
    reflection: str,
    conversation: list[dict[str, str]],
    round_num: int,
) -> AgentCall:
    """A speaker's call: its identity, the symposion's rules, the conversation with its own
    contributions as its own; memories only in round 1, before the last contribution."""
    from .agent_settings import resolve_agent_temperature
    from .llm_client import build_llm_options
    from .message_builder import inject_before_question
    from .prompt_loader import get_agent_system_prompt
    from .tool_output_cap import compute_budget

    view, lang = ctx.view, ctx.intent.language
    model = agent_model(view, agent)
    num_ctx, _ = agent_context(view, agent)
    system_prompt = f"{get_agent_system_prompt(agent, prompt_key='direct', lang=lang, memory=ctx.memory_enabled)}\n\n{rules}"
    if reflection:
        system_prompt = f"{system_prompt}\n\n{reflection}"
    memory, toolkit = await agent_toolkit(ctx, agent)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for entry in conversation:
        own = entry["role"] == "assistant" and entry.get("agent") == agent
        messages.append({"role": "assistant" if own else "user", "content": entry["content"]})
    # Round 1 only, and at the end: round 1 stays a prefix of round 2 (which has the reflection
    # instead), the cache carries over.
    if round_num == 1 and memory:
        inject_before_question(messages, memory)
    options = build_llm_options(view, agent, model, resolve_agent_temperature(view, agent), num_ctx)
    system_tokens, history_tokens, tool_tokens = measure_prompt(messages, toolkit, model)
    return AgentCall(
        agent, config.display_name, config.emoji, model, messages, options, toolkit,
        prompt_tokens=system_tokens + history_tokens,
        tool_budget=compute_budget(num_ctx, system_tokens, history_tokens, 0, tool_tokens),
        log_label=config.display_name,
    )
