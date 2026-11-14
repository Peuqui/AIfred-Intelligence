"""The multi-agent modes of a server turn.

Seats, not names (lib/debate_seats.py): the author (the agent that answered first) refines and
defends, the session's critic critiques and attacks, its judge mediates and judges. Each speaks with
its own persona and the seat's task prompt. A seat whose agent is gone rejects the turn before
anyone speaks.

- critical_review: the direct answer, then one critique.
- recensio (code review): the direct answer, the censor reviews the files the auctor changed; per round the
  auctor's correctura and a new review, until the censor gives [LGTM] or the rounds reach ``max_debate_rounds``.
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

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from .agent_config import (
    BASE_AGENT,
    AgentConfig,
    agent_display_name,
    get_agent_config,
    selectable_agents,
)
from .config import RECENSIO_CENSOR_TOOLS
from .debate_seats import (
    CHOSEN_SEATS,
    SEAT_CRITIC,
    SEAT_JUDGE,
    SEAT_MAIN,
    SeatError,
    check_seat_agent,
    round_rules,
    seat_prompt,
)
from .debug_bus import debug
from .formatting import format_number
from .prompt_loader import CRITIQUE_ROLES, DEBATE_ROLES, REVIEW_ROLES
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
from .user_error import Conflict, Invalid

if TYPE_CHECKING:
    from ..backends.base import LLMOptions
    from .llm_pipeline import PipelineResult
    from .runtime_settings import RuntimeSettings


# The debate modes: the seats that speak after the direct answer, and how the prompts describe them
# (prompt_loader.*_ROLES).
_DEBATE_MODES: dict[str, tuple[tuple[str, ...], str]] = {
    "critical_review": ((SEAT_CRITIC,), CRITIQUE_ROLES),
    "recensio": ((SEAT_MAIN, SEAT_CRITIC), REVIEW_ROLES),
    "auto_consensus": ((SEAT_MAIN, SEAT_CRITIC, SEAT_JUDGE), DEBATE_ROLES),
    "tribunal": ((SEAT_MAIN, SEAT_CRITIC, SEAT_JUDGE), DEBATE_ROLES),
}


def chosen_seats(mode: str) -> tuple[str, ...]:
    """The seats of ``mode`` a session chooses (critic, judge); standard and symposion have none."""
    speaking = _DEBATE_MODES[mode][0] if mode in _DEBATE_MODES else ()
    return tuple(seat for seat in speaking if seat != SEAT_MAIN)


# ── Who speaks ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TurnPlan:
    """Who speaks in a turn."""

    responder: str | None                                    # answers the user directly; None: the symposion
    debate: dict[str, str] = field(default_factory=dict)     # seat → agent speaking in the debate after the answer
    symposion: tuple[str, ...] = ()                          # the round table, at least two agents
    seats: dict[str, str] = field(default_factory=dict)      # every seat → its agent, as the prompts name them
    roles: str = DEBATE_ROLES                                # the description of the seats (prompt_loader.*_ROLES)

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
    if mode not in _DEBATE_MODES:
        raise TurnRejected("mode", Invalid("unknown_mode", value=mode))
    _speaker(routed)
    if addressed_to == BASE_AGENT:   # addressing AIfred by name in a debate mode asks for his answer alone
        return TurnPlan(routed)
    speaking, roles = _DEBATE_MODES[mode]
    seated = {SEAT_MAIN: routed, **{seat: seats[seat] for seat in CHOSEN_SEATS}}
    try:
        for seat in speaking:
            if seat != SEAT_MAIN:
                check_seat_agent(seat, seated[seat])
    except SeatError as error:
        raise TurnRejected("seat", error) from error
    return TurnPlan(routed, {seat: seated[seat] for seat in speaking}, seats=seated, roles=roles)


def _speaker(agent: str) -> str:
    """``agent`` may speak in a turn: it is selectable. One switched off (or gone) is refused out loud,
    never replaced by another."""
    if agent not in selectable_agents():
        config = get_agent_config(agent)
        if config is not None and not config.enabled:
            raise TurnRejected("agent", Conflict("agent_switched_off", agent=agent_display_name(agent)))
        raise TurnRejected("agent", Invalid("agent_not_addressable", agent=agent_display_name(agent)))
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


def _system(member: _Member, situation: str, lang: str, plan: TurnPlan, ctx: TurnContext) -> str:
    """A speaker's system prompt (prompt_loader.agent_system_prompt): ``situation`` is the task of its
    seat, which takes the place of any everyday task; empty for the author, whose task comes as the
    question after the history. A stranger's message (``ctx.external_sender``) adds the security boundary."""
    from .prompt_loader import agent_system_prompt
    from .turn_service import toolkit_names

    return agent_system_prompt(
        member.agent, situation, lang, answer_channel=ctx.answer_channel, external=ctx.external_sender, tools=toolkit_names(member.toolkit),
        memory=member.memory_enabled, seats=plan.seats, roles=plan.roles,
    )


def _seat_system(member: _Member, key: str, lang: str, plan: TurnPlan, ctx: TurnContext, **variables: Any) -> str:
    """The system prompt of a seat whose task is the seat's prompt (``debate/<key>``)."""
    return _system(member, seat_prompt(key, plan.seats, lang, **variables), lang, plan, ctx)


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
    log_label: str = "",
) -> AgentCall:
    """A debate call: the system prompt, then the session's history as the member sees it (as an
    ``observer``, the judge, every reply is someone else's), the member's memories, and last its
    style reminder and output language — with ``question`` (the author's task) when there is one."""
    from .message_builder import build_messages_from_llm_history, inject_before_question
    from .tool_output_cap import compute_budget

    history = build_messages_from_llm_history(
        session_llm_history(ctx.session_id), current_user_text=question, perspective=member.agent,
        detected_language=ctx.intent.language, observer=observer, close=True,
    )
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}, *history]
    if member.memory:
        # Right before the last message, never in the system prompt: the prefix cache keeps the history.
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
        ctx, member.model, member.num_ctx, system_prompt_tokens,
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
    if ctx.mode == "tribunal":
        debug("⚖️ Multi-Agent: Tribunal starting...")
        await _tribunal(ctx, plan)
    elif ctx.mode == "recensio":
        debug(f"🔎 Multi-Agent: recensio by {agent_display_name(plan.debate[SEAT_CRITIC])} starting...")
        await _recensio(ctx, plan)
    else:
        debug(f"🏛️ Multi-Agent: {agent_display_name(plan.debate[SEAT_CRITIC])} analysis starting...")
        await _critique(ctx, plan, answer)
    debug("────────────────────")


async def _critique(ctx: TurnContext, plan: TurnPlan, answer: str) -> None:
    """critical_review (one critique, the user decides) and auto_consensus (rounds until consensus)."""
    from .logging_utils import log_raw_messages

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
        # The round's block keeps the critic from inventing "progress" in round 1; the vote only where it counts.
        system_prompt = _seat_system(
            critic, "critic", lang, plan, ctx,
            round_rules=round_rules("critic", round_num == 1, plan.seats, lang),
            voting=seat_prompt("critic_voting", plan.seats, lang) if auto else "",
        )
        await _compress_before(ctx, plan, critic, _prompt_tokens(system_prompt))
        call = _call(ctx, critic, system_prompt)
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
        system_prompt = _seat_system(
            judge, "mediator", lang, plan, ctx, round_rules=round_rules("mediator", round_num == 1, plan.seats, lang),
        )
        await _compress_before(ctx, plan, judge, _prompt_tokens(system_prompt))
        call = _call(ctx, judge, system_prompt, observer=True)
        debug(_prompt_line(call, f"R{round_num}", judge.num_ctx))
        log_raw_messages(f"{judge.label} R{round_num}", call.messages)
        result = await _speak(ctx, call)
        if result is None:
            break
        finish_call(ctx, call, result, mode="synthesis", round_num=round_num)
        synthesis = result.text

        # The author votes from its first refinement on: its direct answer was never asked to vote, so
        # in round 1 only critic and judge count (an unasked vote would always be a no).
        voters = [(SEAT_CRITIC, critique), (SEAT_JUDGE, synthesis)]
        if round_num > 1:
            voters.insert(0, (SEAT_MAIN, current_answer))
        votes = {seat: _agrees(text) for seat, text in voters}
        debug(_votes_line(members, votes, round_num))
        if _consensus(votes, ctx.view.consensus_type):
            kind = "unanimous" if ctx.view.consensus_type == "unanimous" else "majority"
            debug(
                f"✅ Consensus reached in round {format_number(round_num)} "
                f"({format_number(sum(votes.values()))}/{format_number(len(votes))} votes, {kind})"
            )
            consensus_round = round_num
            break
        if round_num == max_rounds:
            break

        main = members[SEAT_MAIN]
        question = seat_prompt(
            "refinement", plan.seats, lang,
            round_num=round_num + 1,
        )
        system_prompt = _system(main, "", lang, plan, ctx)
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


async def _recensio(ctx: TurnContext, plan: TurnPlan) -> None:
    """The censor (critic's seat) reads and tests the files the auctor (the author) changed in this turn
    and reports every fault. On [WEITER] a round follows: the auctor fixes the findings in the files
    (correctura), the censor checks again; until [LGTM] or ``max_debate_rounds`` rounds. The first
    review, of the answer, belongs to no round (Peuqui 14.11.2026: a round is fix and review)."""
    lang = ctx.intent.language
    debug(_llm_line(ctx.view, plan.debate[SEAT_CRITIC]))
    members = await _cast(ctx, plan.debate)
    author = members[SEAT_MAIN]
    # The censor reads and tests; it changes nothing (the author fixes): only those tools.
    censor = members[SEAT_CRITIC]
    if censor.toolkit is not None:
        censor = replace(censor, toolkit=censor.toolkit.restricted(RECENSIO_CENSOR_TOOLS))
    max_rounds = ctx.view.max_debate_rounds
    review = await _review(ctx, plan, censor, author, 0)
    for round_num in range(1, max_rounds + 1):
        if review is None:
            return
        if _agrees(review):
            debug(f"✅ Recensio passed after {format_number(round_num - 1)} fixes")
            return
        question = seat_prompt(
            "review_fix", plan.seats, lang,
            round_num=round_num, **_file_lists(_author_files(ctx, author.agent)),
        )
        system_prompt = _system(author, "", lang, plan, ctx)
        await _compress_before(ctx, plan, author, _prompt_tokens(system_prompt, question))
        call = _call(ctx, author, system_prompt, question=question, log_label=f"{author.label} Fix")
        debug(_prompt_line(call, f"R{round_num}", author.num_ctx))
        result = await _speak(ctx, call)
        if result is None:
            return
        finish_call(ctx, call, result, mode="correctura", round_num=round_num)
        review = await _review(ctx, plan, censor, author, round_num)
    if review is not None and _agrees(review):
        debug(f"✅ Recensio passed after {format_number(max_rounds)} fixes")
    elif review is not None:
        debug(f"⚠️ Recensio still open after {format_number(max_rounds)} rounds")


async def _review(ctx: TurnContext, plan: TurnPlan, censor: _Member, author: _Member, fixes: int) -> str | None:
    """One review of the author's files; ``fixes`` the corrections so far (0: the review of the answer,
    without a round number). Its text, None when the stream gave nothing."""
    from .logging_utils import log_raw_messages

    files = _author_files(ctx, author.agent)
    step = f"R{fixes}" if fixes else "answer"
    debug(f"🔎 Review ({step}): {format_number(len(files))} files of {author.label}")
    # The review is the censor's task, under the description of the two roles; the censor's toolkit
    # holds only RECENSIO_CENSOR_TOOLS, so only their instructions come in.
    lang = ctx.intent.language
    task = seat_prompt(
        "review", plan.seats, lang,
        round_rules=round_rules("review", fixes == 0, plan.seats, lang, round_num=fixes), **_file_lists(files),
    )
    system_prompt = _system(censor, task, lang, plan, ctx)
    await _compress_before(ctx, plan, censor, _prompt_tokens(system_prompt))
    call = _call(ctx, censor, system_prompt)
    debug(_prompt_line(call, step, censor.num_ctx))
    log_raw_messages(f"{censor.label} {step}", call.messages)
    result = await _speak(ctx, call)
    if result is None:
        return None
    finish_call(ctx, call, result, mode="recensio", round_num=fixes or None)
    return result.text


def _author_files(ctx: TurnContext, author: str) -> dict[str, str]:
    """The files the author changed in this turn so far (its answer and its fixes), path → kind
    (lib/document_changes.py); each of its toolkits once."""
    from . import document_changes

    files: dict[str, str] = {}
    toolkits = {id(call.toolkit): call.toolkit for call in ctx.calls if call.agent == author and call.toolkit is not None}
    for toolkit in toolkits.values():
        document_changes.merge(files, toolkit.changed_documents)
    return files


def _file_lists(files: dict[str, str]) -> dict[str, str]:
    """The prompts' three lists: new, changed and deleted files, each comma-separated or "—"."""
    from . import document_changes

    def listed(kind: str) -> str:
        return ", ".join(path for path, found in files.items() if found == kind) or "—"

    return {
        "new_files": listed(document_changes.NEW),
        "changed_files": listed(document_changes.CHANGED),
        "deleted_files": listed(document_changes.DELETED),
    }


def _agrees(text: str) -> bool:
    """A seat agrees with [LGTM]; [WEITER] outweighs it („das ist noch kein LGTM“ is no vote)."""
    from .context_manager import strip_thinking_blocks

    content = strip_thinking_blocks(text).strip().upper()
    return "[LGTM]" in content and "[WEITER]" not in content


def _consensus(votes: dict[str, bool], consensus_type: str) -> bool:
    """unanimous: every seat agrees; majority: more than half."""
    agreed = sum(votes.values())
    return agreed == len(votes) if consensus_type == "unanimous" else agreed * 2 > len(votes)


def _votes_line(members: dict[str, _Member], votes: dict[str, bool], round_num: int) -> str:
    marks = ", ".join(f"{members[seat].label} {'✅' if agreed else '❌'}" for seat, agreed in votes.items())
    return (
        f"🗳️ Votes R{format_number(round_num)}: {marks} "
        f"({format_number(sum(votes.values()))}/{format_number(len(votes))})"
    )


async def _tribunal(ctx: TurnContext, plan: TurnPlan) -> None:
    """The critic attacks, the main agent defends, round by round; the judge speaks only at the end."""

    lang = ctx.intent.language
    debug("⚖️ Tribunal mode started")
    for seat in (SEAT_CRITIC, SEAT_JUDGE):
        debug(_llm_line(ctx.view, plan.debate[seat]))
    members = await _cast(ctx, plan.debate)
    critic, judge, main = members[SEAT_CRITIC], members[SEAT_JUDGE], members[SEAT_MAIN]
    max_rounds = ctx.view.max_debate_rounds

    for round_num in range(1, max_rounds + 1):
        system_prompt = _seat_system(
            critic, "tribunal", lang, plan, ctx, round_rules=round_rules("tribunal", round_num == 1, plan.seats, lang),
        )
        await _compress_before(ctx, plan, critic, _prompt_tokens(system_prompt))
        call = _call(ctx, critic, system_prompt)
        result = await _speak(ctx, call)
        if result is None:
            break
        finish_call(ctx, call, result, mode="tribunal", round_num=round_num)
        if round_num == max_rounds:
            break

        # The defense may hold its position or revise it (unlike the refinement of auto_consensus).
        question = seat_prompt(
            "defense", plan.seats, lang,
            round_num=round_num + 1,
        )
        system_prompt = _system(main, "", lang, plan, ctx)
        await _compress_before(ctx, plan, main, _prompt_tokens(system_prompt, question))
        call = _call(ctx, main, system_prompt, question=question, log_label=f"{main.label} Defense")
        result = await _speak(ctx, call)
        if result is None:
            break
        finish_call(ctx, call, result, mode="tribunal", round_num=round_num + 1)

    debug(f"{judge.emoji} {judge.label} rendering verdict...")
    system_prompt = _seat_system(judge, "judge", lang, plan, ctx)
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
            call = await _symposion_call(ctx, agent, config, rules, reflection if round_num >= 2 else "", round_num)
            result = await stream_call(ctx, call)
            if result is None:
                debug(f"❌ {config.display_name} returned no result")
                continue
            finish_call(ctx, call, result, mode="symposion", round_num=round_num)

    debug(f"🏛️ Symposion done ({format_number(max_rounds)} rounds, {format_number(len(lineup))} agents)")
    debug("────────────────────")


async def _symposion_call(
    ctx: TurnContext,
    agent: str,
    config: AgentConfig,
    rules: str,
    reflection: str,
    round_num: int,
) -> AgentCall:
    """A speaker's call: the symposion's rules as its situation, the session's history as it sees it
    (its own contributions as its own, the others' and the user's under their names), and last its
    style reminder and output language — from round 2 on with the reflection. Memories only in round 1,
    before the last message (the cache carries over to round 2)."""
    from .agent_settings import resolve_agent_temperature
    from .llm_client import build_llm_options
    from .message_builder import build_messages_from_llm_history, inject_before_question
    from .prompt_loader import agent_system_prompt
    from .tool_output_cap import compute_budget
    from .turn_service import toolkit_names

    view, lang = ctx.view, ctx.intent.language
    model = agent_model(view, agent)
    num_ctx, _ = agent_context(view, agent)
    memory, toolkit = await agent_toolkit(ctx, agent)
    system_prompt = agent_system_prompt(
        agent, rules, lang, answer_channel=ctx.answer_channel, external=ctx.external_sender, tools=toolkit_names(toolkit),
        memory=ctx.memory_enabled,
    )
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}, *build_messages_from_llm_history(
        session_llm_history(ctx.session_id), current_user_text=reflection, perspective=agent,
        detected_language=lang, close=True,
    )]
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
