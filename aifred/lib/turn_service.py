"""A turn on the server: one message of a session and everything up to the finished reply.

The turn runs as a task of the server's event loop, independent of any open tab; the clients of
the session follow it through its event stream (``lib/session_events.py``). Each change goes to
the session file at once (load, change, save under ``session_rmw_lock``), so debug lines the
tools write meanwhile (``debug_bus``) are never overwritten.

Events of a turn, in order: ``turn_started`` (the user's entry), ``session_config`` (after a
mode switch or a sticky address), ``agent_started``, ``token`` (text pieces, bundled),
``tool_status``, ``message_added`` (the finished reply), ``turn_finished``; ``turn_rejected``
instead when the turn cannot start, ``message_queued`` for a message that waits behind it.
``debug`` lines arrive throughout, ``turn_phase`` while the turn works without text (Automatik call,
image description, compression, the pages of a web search; ``debug_bus.report_phase``),
``session_title`` after the first exchange. In a multi-agent
mode ``agent_started`` … ``message_added`` repeat for every agent that speaks. A reply read aloud
adds ``tts``, ``tts_pause`` and ``tts_speak`` (the playlist), ``notice`` (a message for the user)
and, once the reading is over, ``message_updated`` (the bubble with what was spoken); these may
come after ``turn_finished``.

Covered: the direct answer of one agent with tools and memory, addressee and language, mode switch,
history compression, sticky addressing, title, the multi-agent modes (``lib/turn_debate.py``), the
spoken reply in the browser (``lib/reply_speech.py``), images (seen by the answering agent or
described once, ``_look_at_images``). A message of a channel, a scheduled job or an alert runs the same
turn (``TurnOrigin``, from ``message_processor.process_inbound``); its caller delivers the replies.

Every model call of a turn runs through the same three steps, for the direct answer here and for
each speaker of a debate: build (``AgentCall``), stream (``stream_call``), finish (``finish_call``).
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from . import session_events
from .browser_push import LATEST_WINS_KINDS
from .config import LLAMASWAP_BACKENDS, TURN_TOKEN_EVENT_INTERVAL_S
from .debate_seats import session_seats
from .debug_bus import debug, report_phase, session_scope
from .logging_utils import log_exception, log_message
from .prompt_loader import get_language
from .runtime_settings import (
    RuntimeSettings,
    effective_automatik_id,
    effective_model_id,
)
from .user_error import Conflict, Invalid, UserError

if TYPE_CHECKING:
    from ..backends.base import LLMOptions
    from .agent_config import AgentConfig
    from .llm_pipeline import PipelineResult
    from .reply_speech import ReplySpeech
    from .turn_debate import TurnPlan
    from .turn_images import TurnImage


class TurnRejected(Exception):
    """The turn cannot start; ``reason`` is a stable key for the client, ``refusal`` the reason in words (it reaches
    the client in the UI language)."""

    def __init__(self, reason: str, refusal: UserError) -> None:
        super().__init__(str(refusal))
        self.reason = reason
        self.refusal = refusal


@dataclass(frozen=True)
class TurnOrigin:
    """A message from outside the chat: a channel, a scheduled job, an alert (``process_inbound``).

    Its bubble and what the model reads are built by the channel side; the rights of its sender go to
    every agent's tools of the turn.
    """

    chat_entry: dict[str, Any]       # the bubble (message_processor.build_inbound_chat_entry)
    llm_text: str                    # the user turn as the model reads it: wrapped as an external message
    max_tier: int                    # the highest tool tier the sender may reach (security.resolve_tier_for_sender)
    trust: str                       # owner or external (security.resolve_trust_label)
    metadata: dict[str, Any]         # the channel's extras the tools read (delivery channel of a job, …)
    pinned_agent: str | None = None  # a wake word or a job names who answers, whoever the text addresses
    task: bool = False               # AIfred wrote the text as a task (job, alert): no intent detection


@dataclass(frozen=True)
class TurnRequest:
    session_id: str
    text: str
    source: str = "browser"
    images: tuple[str, ...] = ()   # URLs of the images uploaded for this message (lib/turn_images.py)
    origin: TurnOrigin | None = None   # None: the message was written in the chat


@dataclass(frozen=True)
class TurnReply:
    """A reply of the turn, as a channel delivers it."""

    agent: str
    text: str        # without thinking (PipelineResult.text_clean)
    silent: bool     # a tool already answered out loud (audio_player): the channel says nothing
    bubble: str      # the timestamp of its bubble: a speaking channel keeps what it spoke there


@dataclass(frozen=True)
class TurnOutcome:
    status: str                  # done | rejected | error
    agent: str | None            # who the message went to; None when the turn ended before routing
    replies: tuple[TurnReply, ...]


def submit_turn(request: TurnRequest) -> bool:
    """Start the turn now, or queue it behind the session's running turn; True when it waits.

    The queue is the session's pending messages (session_storage), the same one dictation and
    injection use: a turn that ends starts the next waiting message, with or without a tab.
    """
    from .pipeline_registry import is_pipeline_running
    from .session_storage import queue_pending_message

    if is_pipeline_running(request.session_id):
        queue_pending_message(request.session_id, request.text, request.images)
        session_events.publish(request.session_id, "message_queued", {"text": request.text, "images": list(request.images)})
        return True
    start_turn(request)
    return False


def end_session_activity(session_id: str) -> None:
    """The session's turn, its reading aloud and what its tabs play end (before its chat is cleared or it goes)."""
    from . import reply_speech
    from .pipeline_registry import cancel_pipeline

    cancel_pipeline(session_id)
    reply_speech.discard(session_id)
    session_events.publish(session_id, "stop", {})


def start_turn(request: TurnRequest) -> asyncio.Task[TurnOutcome]:
    """Run the turn as a task of the current loop; an earlier turn of the session is cancelled."""
    return asyncio.create_task(run_turn(request), name=f"turn-{request.session_id[:8]}")


async def run_turn(request: TurnRequest) -> TurnOutcome:
    """Run the turn in the current task (it becomes the session's pipeline: a stop cancels it)."""
    from .pipeline_registry import register_pipeline, unregister_pipeline

    session_id = request.session_id
    task = asyncio.current_task()
    assert task is not None
    register_pipeline(session_id, task)
    turn_id = uuid.uuid4().hex
    status = "done"
    routing = _Routing()
    with session_scope(session_id):
        try:
            await _run(request, turn_id, routing)
        except TurnRejected as rejection:
            status = "rejected"
            debug(f"⚠️ {rejection.refusal}")
            session_events.publish(session_id, "turn_rejected",
                                   {"reason": rejection.reason, "message": rejection.refusal.message(get_language())})
        except asyncio.CancelledError:
            status = "cancelled"
            debug("⏹️ Turn cancelled")
            raise
        except Exception as error:  # noqa: BLE001 — boundary of the turn: the client sees the error, the log keeps the traceback
            status = "error"
            log_exception("Turn failed", error)
            debug(f"❌ Turn failed: {error}")
        finally:
            unregister_pipeline(session_id, task)
            session_events.publish(session_id, "turn_finished", {"turn_id": turn_id, "status": status})
            _start_next(session_id)
    return TurnOutcome(status, routing.agent, tuple(routing.replies))


def _start_next(session_id: str) -> None:
    """The next message waiting in the session's queue becomes the next turn, on the server's loop
    (a channel's turn ends on the message hub's). Only the chat queues (``submit_turn``, dictation,
    injection): a waiting message is one written in the chat."""
    from .session_storage import pop_pending_message

    pending = pop_pending_message(session_id)
    if pending is not None:
        session_events.on_home_loop(start_turn, TurnRequest(session_id, pending.text, images=pending.images))


@dataclass
class _Routing:
    """What the caller of a turn learns of it: who it went to and the replies, filled as it runs."""

    agent: str | None = None
    replies: list[TurnReply] = field(default_factory=list)


# ── The turn ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TurnContext:
    """What every model call of a turn shares."""

    session_id: str
    view: RuntimeSettings
    llm_client: Any
    user_text: str        # as written: memory recall
    llm_user_text: str    # stamped, the last llm_history entry
    intent: _Intent
    source: str
    mode: str             # the session's multi-agent mode (after a switch): labels the bubbles
    memory_enabled: bool  # the agents use their long-term memory (the session is not incognito)
    stamp: str            # the turn's time stamp (message_builder.user_turn_stamp)
    origin: TurnOrigin | None   # a message from outside the chat: its sender's rights for the tools
    replies: list[TurnReply]    # the turn's replies so far (finish_call), for the caller of run_turn
    # The images the answering agent sees itself in its request; empty when the turn has none or a
    # description of them joined the user turn (_look_at_images).
    seen_images: tuple[TurnImage, ...] = ()
    calls: list[AgentCall] = field(default_factory=list)   # the turn's model calls so far (stream_call)


async def _run(request: TurnRequest, turn_id: str, routing: _Routing) -> None:
    from . import turn_debate
    from .calibration_gate import is_calibration_active
    from .formatting import build_user_chat_entry
    from .llm_client import LLMClient
    from .message_builder import stamp_user_turn, user_turn_stamp
    from .runtime_settings import load_runtime_settings
    from .session_storage import get_session_config
    from .turn_images import bubble_images, bubble_text, history_text, session_images

    session_id = request.session_id
    user_text = request.text.strip()
    try:
        images = session_images(session_id, list(request.images))
    except Invalid as error:
        raise TurnRejected("images", error) from error
    if not user_text and not images:
        raise TurnRejected("empty", Invalid("nothing_to_send"))
    if is_calibration_active():
        raise TurnRejected("calibration", Conflict("calibration_running"))
    view = load_runtime_settings()
    if not view.agent_tuning["aifred"].model_id:
        raise TurnRejected("no_model", Conflict("no_model"))

    session_events.clear_replay(session_id, keep=LATEST_WINS_KINDS)
    origin = request.origin
    if origin is None:
        entry = build_user_chat_entry(bubble_text(user_text, images, view.ui_language), bubble_images(images))
    else:
        entry = origin.chat_entry
    stamp = user_turn_stamp()
    llm_user_text = stamp_user_turn(history_text(user_text, images) if origin is None else origin.llm_text, stamp)
    _append(session_id, chat=[entry], llm=[{"role": "user", "content": llm_user_text}])
    session_events.publish(session_id, "turn_started", {"turn_id": turn_id, "user_entry": entry})
    debug("📨 User request received")

    llm_client = LLMClient(backend_type=view.backend_type, base_url=view.backend_url)
    try:
        if origin is not None and origin.task:
            # The intent detection reads a person's words; a task's text is AIfred's own template, its
            # agent is pinned and it speaks the interface's language.
            debug(f"🎯 Intent skipped (a task of the system), Lang: {view.ui_language.upper()} (UI)")
            intent = _Intent(None, view.ui_language, {}, False, 0.0)
        else:
            intent = await _detect_intent(view, llm_client, user_text)
        config = get_session_config(session_id)
        if intent.mode_switch:
            config = _apply_mode_switch(session_id, config, intent)
            if intent.is_pure_command:
                routing.replies.append(_confirm_mode_switch(session_id, config, intent))
                return
        agent = _route(session_id, config, intent.addressed_to, origin.pinned_agent if origin else None)
        routing.agent = agent
        mode = config["multi_agent_mode"]
        plan = turn_debate.plan_turn(mode, agent, intent.addressed_to, config["symposion_agents"], session_seats(config))
        seen: tuple[TurnImage, ...] = ()
        if images:
            seen, llm_user_text = await _look_at_images(session_id, view, plan, images, user_text, llm_user_text, intent.language)
        memory_enabled = not config["incognito"]
        if not memory_enabled:
            debug("🔒 Incognito mode (no memory)")
        ctx = TurnContext(
            session_id, view, llm_client, user_text, llm_user_text, intent, request.source, mode, memory_enabled, stamp,
            origin, routing.replies, seen,
        )
        await _compress(ctx, plan.agents)
        if plan.responder is None:
            await turn_debate.run_symposion(ctx, plan)
        else:
            answer = await _respond(ctx, plan.responder)
            # The debate starts from the answer; without one there is nothing to critique.
            if answer is not None and plan.debate:
                await turn_debate.run_debate(ctx, plan, answer.text)
        last_call = ctx.calls[-1] if ctx.calls else None
    finally:
        await llm_client.close()
    if last_call is not None:
        _start_title(session_id, view, last_call)


# ── Intent, language, mode switch ────────────────────────────────────────


@dataclass(frozen=True)
class _Intent:
    addressed_to: str | None
    language: str
    mode_switch: dict[str, Any]
    is_pure_command: bool
    load_time: float


async def _detect_intent(view: RuntimeSettings, llm_client: Any, user_text: str) -> _Intent:
    """Addressee, language and a requested mode switch in one Automatik call.

    A URL-only message and one of images only skip the call: the UI language applies.
    """
    from .config import AUTOMATIK_LLM_NUM_CTX
    from .intent_detector import (
        detect_query_intent_and_addressee,
        format_intent_result,
        format_mode_switch_summary,
    )

    if not user_text or re.fullmatch(r"https?://\S+", user_text):
        debug(f"🎯 Intent skipped ({'URL' if user_text else 'image'}-only), Lang: {view.ui_language.upper()} (UI)")
        return _Intent(None, view.ui_language, {}, False, 0.0)

    automatik = effective_automatik_id(view)
    # Same model as AIfred: the same context as its preload, or the backend reloads it twice.
    same_model = automatik == effective_model_id(view, "aifred")
    num_ctx = (view.agent_tuning["aifred"].max_context or None) if same_model else AUTOMATIK_LLM_NUM_CTX
    report_phase("intent")
    await _announce_cold_start(view, automatik)
    addressed_to, language, mode_switch, pure, _raw, load_time = await detect_query_intent_and_addressee(
        user_text, automatik, llm_client, automatik_num_ctx=num_ctx,
    )
    report_phase("")
    switch = format_mode_switch_summary(mode_switch, lang="en") if mode_switch else "–"   # a debug line: English
    debug(f"🎯 {format_intent_result(addressed_to, language)}, Switch: {switch}, PureCmd: {'yes' if pure else 'no'}")
    return _Intent(addressed_to, language, mode_switch, pure, load_time)


async def _announce_cold_start(view: RuntimeSettings, model: str) -> None:
    """Tell the user why the first call is slow when llama-swap has to load the model."""
    if view.backend_type not in LLAMASWAP_BACKENDS:
        return
    from .service_status import LlamaSwapError, running_models

    base_url = view.backend_url.rstrip("/").removesuffix("/v1")
    try:
        loaded = [entry.name for entry in await asyncio.to_thread(running_models, base_url)]
    except LlamaSwapError as error:
        log_message(f"⚠️ Cold-start check: llama-swap does not answer: {error}", "warning")
        return
    if model not in loaded:
        debug(f"🔄 Model Cold Start ({model}) — loading into VRAM, this may take a while")


def _apply_mode_switch(session_id: str, config: dict[str, Any], intent: _Intent) -> dict[str, Any]:
    from .intent_detector import format_mode_switch_summary
    from .session_storage import update_session_config

    update_session_config(session_id, **intent.mode_switch)
    config = {**config, **intent.mode_switch}
    debug(f"🔧 Mode switch: {format_mode_switch_summary(intent.mode_switch, lang='en')}")
    session_events.publish(session_id, "session_config", _public_config(config))
    return config


def _confirm_mode_switch(session_id: str, config: dict[str, Any], intent: _Intent) -> TurnReply:
    """A message that only switched the mode gets a confirmation instead of an answer."""
    from .agent_config import get_agent_label
    from .formatting import build_assistant_chat_entry
    from .i18n import t
    from .intent_detector import format_mode_switch_summary

    parts = [format_mode_switch_summary(intent.mode_switch, lang=intent.language)]
    if "active_agent" not in intent.mode_switch:
        parts.append(f"{t('mode_switch_agent', lang=intent.language)}: {get_agent_label(config['active_agent'])}")
    text = f"✅ **{' · '.join(parts)}**"
    entry = build_assistant_chat_entry(text, "system")
    entry["mode"] = "mode_switch"
    _append(session_id, chat=[entry], llm=[{"role": "assistant", "content": text}])
    session_events.publish(session_id, "message_added", {"entry": entry})
    return TurnReply("system", text, silent=False, bubble=str(entry["timestamp"]))


def _route(session_id: str, config: dict[str, Any], addressed_to: str | None, pinned: str | None) -> str:
    """The agent that answers: the one a wake word or a job pinned, else the one addressed in the
    message, else the session's active agent.

    An explicit address sticks: it becomes the session's active agent for the next turns.
    """
    from .agent_config import get_agent_label
    from .session_storage import update_session_config

    if pinned and addressed_to and pinned != addressed_to:
        debug(f"🎯 Wake-Override: {get_agent_label(pinned)} (the message addressed {get_agent_label(addressed_to)})")
    chosen = pinned or addressed_to
    if chosen and chosen != config["active_agent"]:
        update_session_config(session_id, active_agent=chosen)
        config["active_agent"] = chosen
        session_events.publish(session_id, "session_config", _public_config(config))
    agent = chosen or config["active_agent"]
    if chosen:
        debug(f"{get_agent_label(agent)} Direct addressing")
    return agent


def _public_config(config: dict[str, Any]) -> dict[str, Any]:
    from .session_storage import DEFAULT_SESSION_CONFIG

    return {key: config[key] for key in DEFAULT_SESSION_CONFIG}


# ── Images ───────────────────────────────────────────────────────────────


async def _look_at_images(
    session_id: str,
    view: RuntimeSettings,
    plan: TurnPlan,
    images: list[TurnImage],
    user_text: str,
    llm_user_text: str,
    lang: str,
) -> tuple[tuple[TurnImage, ...], str]:
    """Who looks at the turn's images (rule A, ``vision_routing.chat_describer``).

    When one agent answers alone and its model can see, it gets the images in its request. Every
    other turn (a debate, the symposion, a model that cannot see) gets ONE description in the user
    turn, so all its agents work from the same basis. Returns the images the answering agent sees
    itself and the user turn as the history keeps it.
    """
    from .i18n import t
    from .remote_describers import endpoint
    from .turn_images import describe_images, description_block
    from .vision_routing import (
        Describer,
        NoVisionModelError,
        chat_describer,
        describe_with_second_attempt,
        eviction_notice,
    )

    main_model = agent_model(view, plan.responder or "aifred")

    def choose() -> Describer:
        describer = chat_describer(main_model)
        if describer.evicts_chat_model:
            debug(eviction_notice(describer.model))
        return describer

    try:
        describer = choose()
    except NoVisionModelError as error:
        debug(f"⚠️ Vision: {error} — the image goes on without a description")
        session_events.publish(session_id, "notice", {"level": "warning", "text": t("vision_no_describer", lang=view.ui_language)})
        return (), llm_user_text
    if plan.responder is not None and not plan.debate and describer.model == main_model:
        debug(f"📷 VL Direct ({len(images)} image(s)) → {main_model}")
        return tuple(images), llm_user_text
    report_phase("images")
    if endpoint(describer.model) is None:   # a describer of another machine loads there
        await _announce_cold_start(view, describer.model)
    debug(f"📷 Image description ({len(images)} image(s)) → {describer.model}")
    description = await describe_with_second_attempt(
        describer, choose, lambda chosen: describe_images(images, user_text, lang, chosen.model, view),
    )
    report_phase("")
    llm_user_text = f"{llm_user_text}\n\n{description_block(description, lang)}"
    _rewrite_user_turn(session_id, llm_user_text)
    return (), llm_user_text


# ── History compression ──────────────────────────────────────────────────


async def _compress(ctx: TurnContext, agents: list[str]) -> None:
    """Before the turn: compress when the history would crowd the smallest context of the turn's
    agents, measured with the largest of their system prompts (who speaks when is open yet)."""
    from .prompt_loader import get_direct_prompt_tokens

    context_limit = min(agent_context(ctx.view, agent)[0] for agent in agents)
    prompt_tokens = max(get_direct_prompt_tokens(agent, ctx.intent.language, memory=ctx.memory_enabled) for agent in agents)
    # The first speaker summarizes: its model is the one that runs next.
    await compress_history(ctx, agent_model(ctx.view, agents[0]), context_limit, prompt_tokens, 0, "Pre-Message Compression")


async def compress_history(
    ctx: TurnContext,
    model: str,
    context_limit: int,
    system_prompt_tokens: int,
    toolkit_tokens: int,
    label: str,
) -> None:
    """Compress the session's history when it would crowd ``context_limit``; ``model``, the one that
    speaks next, summarizes (no second model to load in between)."""
    from .context_manager import summarize_history_if_needed
    from .session_storage import load_session, session_rmw_lock, update_chat_data

    data = (load_session(ctx.session_id) or {}).get("data", {})
    compressing = False   # the summary can fail after it began: the phase ends after the loop
    async for event in summarize_history_if_needed(
        history=data["chat_history"],
        llm_client=ctx.llm_client,
        model_name=model,
        context_limit=context_limit,
        llm_history=data.get("llm_history", []),
        system_prompt_tokens=system_prompt_tokens,
        detected_language=ctx.intent.language,
        toolkit_tokens=toolkit_tokens,
    ):
        if event["type"] == "debug":
            debug(event["message"])
        elif event["type"] == "progress":
            compressing = True
            report_phase("compress")
        elif event["type"] == "history_update":
            with session_rmw_lock:
                current = (load_session(ctx.session_id) or {}).get("data", {})
                llm_history = event.get("llm_history") or current.get("llm_history", [])
                update_chat_data(
                    session_id=ctx.session_id,
                    chat_history=event["chat_history"],
                    llm_history=llm_history,
                    debug_messages=current.get("debug_messages", []),
                )
            session_events.publish(ctx.session_id, "history_replaced", {"chat_history": event["chat_history"]})
            debug(f"✅ {label}: {len(event['chat_history'])} UI / {len(llm_history)} LLM messages")
    if compressing:
        report_phase("")


# ── An agent's model call: build, stream, finish ─────────────────────────


@dataclass(frozen=True)
class AgentCall:
    """One model call of an agent, ready to stream."""

    agent: str
    label: str              # display name: bubble and events
    emoji: str
    model: str              # the effective model id the request goes to
    messages: list[dict[str, Any]]
    options: LLMOptions
    toolkit: Any
    prompt_tokens: int      # system prompt and messages, without the tool schemas
    tool_budget: int        # tokens a single tool result may take (tool_output_cap)
    log_label: str          # names the call in debug and log lines ("AIfred Refinement")


def agent_identity(agent: str) -> AgentConfig:
    from .agent_config import get_agent_config

    config = get_agent_config(agent)
    if config is None:
        raise TurnRejected("agent", Invalid("unknown_agent", agent=agent))
    return config


def agent_model(view: RuntimeSettings, agent: str) -> str:
    """The model the agent's requests go to: its own, else AIfred's (the inheritance rule)."""
    return effective_model_id(view, agent) or effective_model_id(view, "aifred")


def agent_context(view: RuntimeSettings, agent: str) -> tuple[int, str]:
    """The agent's context window and where it comes from (the lookup wants the base model id)."""
    from .agent_settings import get_agent_base_model_id
    from .research.context_utils import get_agent_num_ctx

    return get_agent_num_ctx(agent, view, get_agent_base_model_id(view, agent))


def measure_prompt(messages: list[dict[str, Any]], toolkit: Any, model: str) -> tuple[int, int, int]:
    """Tokens of the system prompt, of the other messages and of the tool schemas."""
    from .context_manager import estimate_tokens, estimate_toolkit_tokens

    system = estimate_tokens([m for m in messages if m["role"] == "system"], model_name=model)
    history = estimate_tokens([m for m in messages if m["role"] != "system"], model_name=model)
    return system, history, estimate_toolkit_tokens(toolkit, model_name=model)


async def agent_toolkit(ctx: TurnContext, agent: str) -> tuple[str, Any]:
    """The agent's recalled memories and tools for this turn, within the rights of the message's
    sender (a channel's tier and trust; the chat's own are the full ones). The tools get the session's
    conversation: the web search ranks its hits against it (until 08.11.2026 only the Reflex chat gave it)."""
    from .agent_memory import prepare_agent_toolkit

    origin = ctx.origin
    history = session_llm_history(ctx.session_id)
    if origin is None:
        return await prepare_agent_toolkit(
            agent, ctx.user_text, lang=ctx.intent.language, memory_enabled=ctx.memory_enabled,
            research_tools_enabled=True, session_id=ctx.session_id, llm_history=history, source=ctx.source,
        )
    return await prepare_agent_toolkit(
        agent, ctx.user_text, lang=ctx.intent.language, memory_enabled=ctx.memory_enabled,
        research_tools_enabled=True, session_id=ctx.session_id, llm_history=history, source=ctx.source,
        max_tier=origin.max_tier, metadata=origin.metadata, trust=origin.trust,
    )


def session_llm_history(session_id: str) -> list[dict[str, Any]]:
    from .session_storage import load_session

    history: list[dict[str, Any]] = (load_session(session_id) or {}).get("data", {}).get("llm_history", [])
    return history


async def _respond(ctx: TurnContext, agent: str) -> PipelineResult | None:
    """The agent answers the user directly with its full toolkit; the finished reply is saved."""
    call = await _direct_call(ctx, agent)
    result = await stream_call(ctx, call)
    if result is None:
        return None
    finish_call(ctx, call, result, mode="direct", load_time=ctx.intent.load_time)
    debug("────────────────────")
    return result


async def _direct_call(ctx: TurnContext, agent: str) -> AgentCall:
    """The direct answer's call: the agent's direct prompt, the history from its perspective, the
    memories right before the question; the turn's images in the question when the agent sees them."""
    from .agent_settings import (
        THINKING_OFF,
        resolve_agent_temperature,
        thinking_fields,
        thinking_mode,
    )
    from .context_manager import estimate_tokens
    from .formatting import format_number
    from .llm_client import build_llm_options
    from .message_builder import build_messages_from_llm_history, inject_before_question
    from .prompt_loader import get_agent_direct_prompt
    from .tool_output_cap import compute_budget
    from .turn_images import seeing_content, seeing_question, seeing_task

    view, lang = ctx.view, ctx.intent.language
    config = agent_identity(agent)
    model = agent_model(view, agent)
    debug(f"{config.emoji} {config.display_name}-LLM: {model} ({view.backend_type})")
    num_ctx, ctx_source = agent_context(view, agent)
    debug(f"   🎯 Context: {format_number(num_ctx)} ({ctx_source})")

    memory_ctx, toolkit = await agent_toolkit(ctx, agent)
    # The agent thinks as its own row says for the model it runs on (Peuqui 07.11.2026: AIfred's
    # choice does not apply to the other agents).
    thinking = thinking_mode(view, agent, model)
    system_prompt = get_agent_direct_prompt(agent, lang=lang, memory=ctx.memory_enabled)
    seen = list(ctx.seen_images)
    question = seeing_question(seen, ctx.user_text, ctx.stamp, lang) if seen else ctx.llm_user_text
    messages: list[dict[str, Any]] = [*build_messages_from_llm_history(
        session_llm_history(ctx.session_id)[:-1], question, perspective=agent, detected_language=lang,
    )]
    if seen:
        # The agent sees the images itself: they go into its request (in front of the question as
        # the history builder left it, with the persona's reminder), never into the history.
        system_prompt = f"{system_prompt}\n\n{seeing_task(ctx.user_text, lang)}"
        messages[-1] = {"role": "user", "content": seeing_content(seen, messages[-1]["content"], no_think=thinking == THINKING_OFF)}
    messages.insert(0, {"role": "system", "content": system_prompt})
    memory_tokens = 0
    if memory_ctx:
        # After the system prompt, right before the question: the prefix cache keeps the history.
        inject_before_question(messages, memory_ctx)
        memory_tokens = estimate_tokens([{"content": memory_ctx}])
        debug(f"🧠 Memory context injected ({format_number(memory_tokens)} tok, before question)")

    temperature = resolve_agent_temperature(view, agent)
    system_tokens, history_tokens, tool_tokens = measure_prompt(messages, toolkit, model)
    total = system_tokens + tool_tokens + history_tokens
    debug(
        f"📊 Prompt: System {format_number(system_tokens)} + Tools {format_number(tool_tokens)} + "
        f"History {format_number(history_tokens)} = {format_number(total)} / {format_number(num_ctx)} tok"
    )
    debug(f"🌡️ Temperature: {format_number(temperature, 1)}")
    enable_thinking, reasoning_effort = thinking_fields(thinking)
    options = replace(
        build_llm_options(view, agent, model, temperature, num_ctx),
        enable_thinking=enable_thinking, reasoning_effort=reasoning_effort,
    )
    return AgentCall(
        agent, config.display_name, config.emoji, model, messages, options, toolkit,
        prompt_tokens=system_tokens + history_tokens,
        tool_budget=compute_budget(num_ctx, system_tokens, history_tokens, memory_tokens, tool_tokens),
        log_label=config.display_name,
    )


async def stream_call(ctx: TurnContext, call: AgentCall) -> PipelineResult | None:
    """Stream the call to the clients; None when the pipeline ends without a result.

    A cancelled stream keeps what had arrived as an interrupted bubble, in every mode. A reply to the
    chat is read aloud while it streams when the settings want it (``reply_speech``); a channel speaks
    its reply itself.
    """
    from . import reply_speech
    from .context_manager import LoopCompression
    from .llm_pipeline import run_llm_stream
    from .tool_output_cap import budget_var

    ctx.calls.append(call)
    if call.toolkit:
        debug(f"🔧 Toolkit: {[tool.name for tool in call.toolkit.tools]} for {call.log_label}")
    budget_var.set(call.tool_budget)
    # Several agents speak in a multi-agent turn: their tool lines carry the speaker.
    tool_agent = call.log_label if ctx.mode != "standard" else ""
    speech = None
    if ctx.origin is None:
        speech = await reply_speech.begin(ctx.session_id, call, ctx.view, ctx.intent.language, ctx.view.ui_language)
    session_events.publish(
        ctx.session_id, "agent_started", {"agent": call.agent, "display_name": call.label, "emoji": call.emoji},
    )
    stream = _TokenStream(ctx.session_id, speech)
    result: PipelineResult | None = None
    streamed = False
    try:
        async for event in run_llm_stream(
            ctx.llm_client, call.model, call.messages, call.options, call.log_label,
            source=ctx.source, toolkit=call.toolkit, on_debug=debug,
            compression=LoopCompression(ctx.llm_client, call.model, ctx.intent.language),
        ):
            result = _handle_stream_event(ctx.session_id, event, stream, ctx.view.ui_language, tool_agent) or result
        streamed = True
    except asyncio.CancelledError:
        _save_partial(ctx.session_id, call.agent, stream.text, ctx.view.ui_language)
        raise
    finally:
        # A reply without a result becomes no bubble: its reading ends with it.
        if speech is not None:
            speech.end(complete=streamed and result is not None)
    stream.flush()
    if result is not None:
        debug(result.debug_msg)
    return result


def finish_call(
    ctx: TurnContext,
    call: AgentCall,
    result: PipelineResult,
    *,
    mode: str,
    round_num: int | None = None,
    load_time: float = 0.0,
) -> dict[str, Any]:
    """Save the reply as a bubble (marker, mode, round) and in the llm history, tell the clients;
    what is read of it goes onto the bubble when its reading is over.

    ``load_time`` is the intent call's model load of this turn: the first answer carries it.
    """
    from . import reply_speech
    from .bubble import render_bubble
    from .formatting import assistant_history_entry, build_agent_panel_entry

    content = render_bubble(result.text, result.artifacts, model_name=f"{call.label} ({call.model})")
    metadata = {**result.metadata_dict}
    if load_time and metadata:
        metadata["load_time"] = metadata.get("load_time", 0.0) + load_time
    entry = build_agent_panel_entry(
        call.agent, content, mode=mode, round_num=round_num, metadata=metadata,
        marker=_marker(ctx.mode, mode, round_num, ctx.view.ui_language), lang=ctx.intent.language,
    )
    history_entry = assistant_history_entry(call.agent, result.answer_text, result.history_notes)
    _append(ctx.session_id, chat=[entry], llm=[history_entry] if history_entry else [])
    session_events.publish(ctx.session_id, "message_added", {"entry": entry})
    reply_speech.keep_on(ctx.session_id, call, entry)
    ctx.replies.append(TurnReply(call.agent, result.text_clean, result.silent_reply, str(entry["timestamp"])))
    return entry


def _mode_label(mode: str, lang: str) -> str:
    """The label of a bubble's mode; empty for a direct answer."""
    from .i18n import t

    labels = {
        "auto_consensus": t("auto_consensus_label", lang=lang),
        "tribunal": t("multi_agent_tribunal", lang=lang),
        "refinement": t("refinement_label", lang=lang),
        "synthesis": t("salomo_synthesis_label", lang=lang),
        "verdict": t("salomo_verdict_label", lang=lang),
        "critical_review": t("critical_review_label", lang=lang),
        "recensio": t("recensio_label", lang=lang),
        "correctura": t("correctura_label", lang=lang),
        "symposion": t("multi_agent_symposion", lang=lang),
    }
    return labels[mode].rstrip(":") if mode in labels else ""


def _marker(turn_mode: str, mode: str, round_num: int | None, lang: str) -> str:
    """The line above a multi-agent step, e.g. "[Auto-Konsens: Synthese R1]"; empty for a direct
    answer. The turn's mode goes in front unless the step's label already names it. Text and look
    as the browser's (state/_chat_mixin._build_marker): both write the same session files."""
    label = _mode_label(mode, lang)
    if not label:
        return ""
    prefix = ""
    if turn_mode != "standard" and mode not in ("auto_consensus", "tribunal", "symposion") and mode != turn_mode:
        turn_label = _mode_label(turn_mode, lang)
        if turn_label:
            prefix = f"{turn_label}: "
    suffix = f" R{round_num}" if round_num else ""
    return (
        f"<span style='color: rgba(255, 255, 255, 0.6); font-style: italic; font-size: 12px;'>"
        f"[{prefix}{label}{suffix}]</span>\n\n"
    )


class _TokenStream:
    """Text pieces of the answer, sent as ``token`` events at most every TURN_TOKEN_EVENT_INTERVAL_S
    and, while the reply is read aloud, each at once to its reading."""

    def __init__(self, session_id: str, speech: ReplySpeech | None) -> None:
        self.session_id = session_id
        self.speech = speech
        self.text = ""
        self._pending = ""
        self._sent_at = 0.0

    def add(self, piece: str) -> None:
        self.text += piece
        if self.speech is not None:
            self.speech.feed(piece)
        self._pending += piece
        if time.monotonic() - self._sent_at >= TURN_TOKEN_EVENT_INTERVAL_S:
            self.flush()

    def flush(self) -> None:
        if self._pending:
            session_events.publish(self.session_id, "token", {"text": self._pending})
            self._pending = ""
        self._sent_at = time.monotonic()


def _handle_stream_event(
    session_id: str, event: dict[str, Any], stream: _TokenStream, lang: str, tool_agent: str,
) -> Any:
    """Turn one pipeline event into events and debug lines; returns the result when it arrives.

    ``tool_agent`` names the speaker in front of its tool calls (empty: the one agent of the turn).
    """
    from .context_manager import estimate_tokens
    from .debug_format import format_tool_call, format_tool_result
    from .formatting import format_number
    from .i18n import t

    kind = event["type"]
    if kind == "content":
        stream.add(event["text"])
    elif kind == "ttft":
        debug(f"⚡ TTFT: {format_number(event['value'], 2)}s")
    elif kind == "tool_call_start":
        stream.flush()
        _tool_status(session_id, _plugin_status(event.get("name", ""), {}, lang))
    elif kind == "tool_call":
        name, arguments = event.get("name", ""), event.get("arguments", "")
        debug(f"🔧 {format_tool_call(name, arguments, agent=tool_agent)}")
        memory_status = {
            "store_memory": "tool_memory", "update_memory": "tool_memory_update",
            "delete_memory": "tool_memory_delete", "read_memory": "tool_memory_read",
        }.get(name)
        _tool_status(session_id, t(memory_status, lang=lang) if memory_status else _plugin_status(name, _json(arguments), lang))
    elif kind == "tool_progress":
        if event.get("message"):
            debug(event["message"])
    elif kind == "tool_result":
        result = event.get("result", "") or ""
        tokens = estimate_tokens([{"content": result}]) if result else 0
        debug(f"   ↳ {format_tool_result(result, token_count=tokens)}")
        _tool_status(session_id, "")
    elif kind == "debug":
        if event.get("message"):
            debug(event["message"])
    elif kind == "progress":
        # The compression inside the tool loop: begun with its phase, ended with "".
        report_phase(event["phase"])
    elif kind == "pipeline_result":
        return event["result"]
    return None


def _tool_status(session_id: str, text: str) -> None:
    session_events.publish(session_id, "tool_status", {"text": text}, replaces=frozenset({"tool_status"}))


def _plugin_status(tool_name: str, arguments: dict[str, Any], lang: str) -> str:
    """The first plugin that describes the tool call gives the status line."""
    from .plugin_registry import discover_tools

    for plugin in discover_tools():
        status = plugin.get_ui_status(tool_name, arguments, lang)
        if status:
            return status
    return ""


def _json(arguments: str) -> dict[str, Any]:
    import json

    try:
        parsed = json.loads(arguments) if arguments else {}
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _save_partial(session_id: str, agent: str, text: str, lang: str) -> None:
    """A cancelled answer keeps what had streamed, marked as interrupted (as in the browser)."""
    from .i18n import t

    if not text.strip():
        return
    post_bubble(session_id, agent, f"{text}\n\n{t('generation_interrupted', lang=lang)}", lang, in_history=True)
    debug(f"⚠️ Partial response rescued ({len(text)} chars) after aborted generation")


def post_bubble(session_id: str, agent: str, content: str, lang: str, *, in_history: bool) -> dict[str, Any]:
    """An agent's bubble that no model call wrote (a rescued partial answer, a transcript, a note):
    saved, told to the clients, and in the llm history when the model should know it."""
    from .formatting import assistant_history_entry, build_agent_panel_entry

    entry = build_agent_panel_entry(agent, content, lang=lang)
    history_entry = assistant_history_entry(agent, content) if in_history else None
    _append(session_id, chat=[entry], llm=[history_entry] if history_entry else [])
    session_events.publish(session_id, "message_added", {"entry": entry})
    return entry


# ── Session file and title ───────────────────────────────────────────────


def _rewrite_user_turn(session_id: str, content: str) -> None:
    """The turn's user entry of the llm history gets ``content`` (the description of its images)."""
    from .session_storage import load_session, session_rmw_lock, update_chat_data

    with session_rmw_lock:
        data = (load_session(session_id) or {}).get("data", {})
        llm_history = list(data.get("llm_history", []))
        assert llm_history and llm_history[-1]["role"] == "user", "the turn's user entry is the last one"
        llm_history[-1] = {"role": "user", "content": content}
        update_chat_data(
            session_id=session_id,
            chat_history=data.get("chat_history", []),
            llm_history=llm_history,
            debug_messages=data.get("debug_messages", []),
        )


def _append(session_id: str, *, chat: list[dict[str, Any]], llm: list[dict[str, Any]]) -> None:
    """Append entries to the session's chat and llm history in one load-change-save."""
    from .session_storage import load_session, session_rmw_lock, update_chat_data

    with session_rmw_lock:
        data = (load_session(session_id) or {}).get("data", {})
        update_chat_data(
            session_id=session_id,
            chat_history=[*data.get("chat_history", []), *chat],
            llm_history=[*data.get("llm_history", []), *llm],
            debug_messages=data.get("debug_messages", []),
        )


def _start_title(session_id: str, view: RuntimeSettings, last_call: AgentCall) -> None:
    """After the first exchange the session gets a title, in the background, from the model of the
    turn's last call with its context: that model is the loaded one, nothing reloads for the title."""
    from .session_storage import get_session_title, load_session
    from .session_title import first_exchange

    if get_session_title(session_id):
        return
    exchange = first_exchange((load_session(session_id) or {}).get("data", {}).get("llm_history", []))
    if exchange is not None:
        asyncio.create_task(_title(session_id, view, last_call, *exchange), name=f"title-{session_id[:8]}")


async def _title(session_id: str, view: RuntimeSettings, call: AgentCall, user_text: str, reply: str) -> None:
    from .session_title import generate_session_title

    with session_scope(session_id):
        debug("Generating session title...")
        try:
            title = await generate_session_title(
                user_text, reply, session_id, lang=view.ui_language,
                model_override=call.model, num_ctx_override=call.options.num_ctx or 0,
            )
        except Exception as error:  # noqa: BLE001 — background task: a missing title is logged, the turn is done
            log_exception("Session title failed", error)
            return
    if title:
        session_events.publish(session_id, "session_title", {"title": title})
