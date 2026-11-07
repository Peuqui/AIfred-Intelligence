"""A turn on the server: one message of a session and everything up to the finished reply.

The turn runs as a task of the server's event loop, independent of any open tab; the clients of
the session follow it through its event stream (``lib/session_events.py``). Each change goes to
the session file at once (load, change, save under ``session_rmw_lock``), so debug lines the
tools write meanwhile (``debug_bus``) are never overwritten.

Events of a turn, in order: ``turn_started`` (the user's entry), ``session_config`` (after a
mode switch or a sticky address), ``agent_started``, ``token`` (text pieces, bundled),
``tool_status``, ``message_added`` (the finished reply), ``turn_finished``; ``turn_rejected``
instead when the turn cannot start, ``message_queued`` for a message that waits behind it. ``debug`` lines arrive throughout, ``session_title`` after
the first exchange.

Covered so far: the direct answer of one agent with tools and memory, intent and language,
mode switch, history compression, sticky addressing, title. Multi-agent modes, images, speech
and dictation follow (docs/de/architecture/vue-migration/README.md).
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any

from . import session_events
from .config import LLAMASWAP_BACKENDS, TURN_TOKEN_EVENT_INTERVAL_S
from .debug_bus import debug, session_scope
from .logging_utils import log_exception, log_message
from .runtime_settings import (
    RuntimeSettings,
    effective_automatik_id,
    effective_model_id,
)


class TurnRejected(Exception):
    """The turn cannot start; ``reason`` is a stable key for the client, ``message`` the reason in words."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class TurnRequest:
    session_id: str
    text: str
    source: str = "browser"


def submit_turn(request: TurnRequest) -> bool:
    """Start the turn now, or queue it behind the session's running turn; True when it waits.

    The queue is the session's pending messages (session_storage), the same one dictation and
    injection use: a turn that ends starts the next waiting message, with or without a tab.
    """
    from .pipeline_registry import is_pipeline_running
    from .session_storage import queue_pending_message

    if is_pipeline_running(request.session_id):
        queue_pending_message(request.session_id, request.text)
        session_events.publish(request.session_id, "message_queued", {"text": request.text})
        return True
    start_turn(request)
    return False


def start_turn(request: TurnRequest) -> asyncio.Task[None]:
    """Run the turn as a task of the current loop; an earlier turn of the session is cancelled."""
    return asyncio.create_task(run_turn(request), name=f"turn-{request.session_id[:8]}")


async def run_turn(request: TurnRequest) -> None:
    from .pipeline_registry import register_pipeline, unregister_pipeline

    session_id = request.session_id
    task = asyncio.current_task()
    assert task is not None
    register_pipeline(session_id, task)
    turn_id = uuid.uuid4().hex
    status = "done"
    with session_scope(session_id):
        try:
            await _run(request, turn_id)
        except TurnRejected as rejection:
            status = "rejected"
            debug(f"⚠️ {rejection.message}")
            session_events.publish(session_id, "turn_rejected", {"reason": rejection.reason, "message": rejection.message})
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
            _start_next(session_id, request.source)


def _start_next(session_id: str, source: str) -> None:
    """The next message waiting in the session's queue becomes the next turn."""
    from .session_storage import pop_pending_message

    text = pop_pending_message(session_id)
    if text:
        start_turn(TurnRequest(session_id, text, source))


# ── The turn ─────────────────────────────────────────────────────────────


async def _run(request: TurnRequest, turn_id: str) -> None:
    from .calibration_gate import is_calibration_active
    from .formatting import build_user_chat_entry
    from .llm_client import LLMClient
    from .message_builder import stamp_user_turn, user_turn_stamp
    from .runtime_settings import load_runtime_settings
    from .session_storage import get_session_config

    session_id = request.session_id
    user_text = request.text.strip()
    if not user_text:
        raise TurnRejected("empty", "Nothing to send")
    if is_calibration_active():
        raise TurnRejected("calibration", "Inference blocked: calibration in progress")
    view = load_runtime_settings()
    if not view.agent_tuning["aifred"].model_id:
        raise TurnRejected("no_model", "No model selected — pick one in the settings")

    session_events.clear_replay(session_id, keep=frozenset({"service_status"}))
    entry = build_user_chat_entry(user_text)
    llm_user_text = stamp_user_turn(user_text, user_turn_stamp())
    _append(session_id, chat=[entry], llm=[{"role": "user", "content": llm_user_text}])
    session_events.publish(session_id, "turn_started", {"turn_id": turn_id, "user_entry": entry})
    debug("📨 User request received")

    llm_client = LLMClient(backend_type=view.backend_type, base_url=view.backend_url)
    try:
        intent = await _detect_intent(view, llm_client, user_text)
        config = get_session_config(session_id)
        if intent.mode_switch:
            config = _apply_mode_switch(session_id, config, intent)
            if intent.is_pure_command:
                _confirm_mode_switch(session_id, config, intent)
                return
        agent = _route(session_id, config, intent.addressed_to)
        if config["multi_agent_mode"] != "standard":
            raise TurnRejected(
                "mode", f"Multi-agent mode '{config['multi_agent_mode']}' does not run on the server yet",
            )
        await _compress(session_id, view, llm_client, [agent], intent.language)
        await _respond(session_id, view, llm_client, agent, user_text, llm_user_text, intent, request.source)
    finally:
        await llm_client.close()
    _start_title(session_id, view)


# ── Intent, language, mode switch ────────────────────────────────────────


@dataclass(frozen=True)
class _Intent:
    intent: str
    addressed_to: str | None
    language: str
    mode_switch: dict[str, Any]
    is_pure_command: bool
    load_time: float


async def _detect_intent(view: RuntimeSettings, llm_client: Any, user_text: str) -> _Intent:
    """Intent, addressee, language and a requested mode switch in one Automatik call.

    A URL-only message skips the call: the UI language applies.
    """
    from .config import AUTOMATIK_LLM_NUM_CTX
    from .intent_detector import (
        detect_query_intent_and_addressee,
        format_intent_result,
        format_mode_switch_summary,
    )

    if re.fullmatch(r"https?://\S+", user_text):
        debug(f"🎯 Intent: FAKTISCH (URL-only), Lang: {view.ui_language.upper()} (UI)")
        return _Intent("FAKTISCH", None, view.ui_language, {}, False, 0.0)

    automatik = effective_automatik_id(view)
    # Same model as AIfred: the same context as its preload, or the backend reloads it twice.
    same_model = automatik == effective_model_id(view, "aifred")
    num_ctx = (view.agent_tuning["aifred"].max_context or None) if same_model else AUTOMATIK_LLM_NUM_CTX
    await _announce_cold_start(view, automatik)
    intent, addressed_to, language, mode_switch, pure, _raw, load_time = await detect_query_intent_and_addressee(
        user_text, automatik, llm_client, automatik_num_ctx=num_ctx,
    )
    switch = format_mode_switch_summary(mode_switch) if mode_switch else "–"
    debug(f"🎯 {format_intent_result(intent, addressed_to, language)}, Switch: {switch}, PureCmd: {'yes' if pure else 'no'}")
    return _Intent(intent, addressed_to, language, mode_switch, pure, load_time)


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
    debug(f"🔧 Mode switch: {format_mode_switch_summary(intent.mode_switch, lang=intent.language)}")
    session_events.publish(session_id, "session_config", _public_config(config))
    return config


def _confirm_mode_switch(session_id: str, config: dict[str, Any], intent: _Intent) -> None:
    """A message that only switched the mode gets a confirmation instead of an answer."""
    from .agent_config import get_agent_label
    from .formatting import build_assistant_chat_entry
    from .intent_detector import format_mode_switch_summary

    parts = [format_mode_switch_summary(intent.mode_switch, lang=intent.language)]
    if "active_agent" not in intent.mode_switch:
        parts.append(f"Agent: {get_agent_label(config['active_agent'])}")
    text = f"✅ **{' · '.join(parts)}**"
    entry = build_assistant_chat_entry(text, "system")
    entry["mode"] = "mode_switch"
    _append(session_id, chat=[entry], llm=[{"role": "assistant", "content": text}])
    session_events.publish(session_id, "message_added", {"entry": entry})


def _route(session_id: str, config: dict[str, Any], addressed_to: str | None) -> str:
    """The agent that answers: the one addressed in the message, else the session's active agent.

    An explicit address sticks: it becomes the session's active agent for the next turns.
    """
    from .agent_config import get_agent_label
    from .session_storage import update_session_config

    if addressed_to and addressed_to != config["active_agent"]:
        update_session_config(session_id, active_agent=addressed_to)
        config["active_agent"] = addressed_to
        session_events.publish(session_id, "session_config", _public_config(config))
    agent = addressed_to or config["active_agent"]
    if addressed_to:
        debug(f"{get_agent_label(agent)} Direct addressing")
    return agent


def _public_config(config: dict[str, Any]) -> dict[str, Any]:
    return {key: config[key] for key in ("active_agent", "multi_agent_mode", "symposion_agents")}


# ── History compression ──────────────────────────────────────────────────


async def _compress(
    session_id: str, view: RuntimeSettings, llm_client: Any, agents: list[str], language: str,
) -> None:
    """Compress the history when it would crowd the context of the turn's smallest agent."""
    from .agent_settings import get_agent_base_model_id
    from .context_manager import largest_compression_model, summarize_history_if_needed
    from .prompt_loader import get_max_direct_prompt_tokens
    from .research.context_utils import get_agent_num_ctx
    from .session_storage import load_session, session_rmw_lock, update_chat_data

    session = load_session(session_id) or {}
    data = session.get("data", {})
    if not data.get("chat_history"):
        return
    context_limit = min(get_agent_num_ctx(agent, view, get_agent_base_model_id(view, agent))[0] for agent in agents)
    model = largest_compression_model(
        effective_model_id(view, agent) or effective_model_id(view, "aifred") for agent in agents
    )
    async for event in summarize_history_if_needed(
        history=data["chat_history"],
        llm_client=llm_client,
        model_name=model,
        context_limit=context_limit,
        llm_history=data.get("llm_history", []),
        system_prompt_tokens=get_max_direct_prompt_tokens("standard", language, memory=True),
        detected_language=language,
    ):
        if event["type"] == "debug":
            debug(event["message"])
        elif event["type"] == "history_update":
            with session_rmw_lock:
                current = (load_session(session_id) or {}).get("data", {})
                update_chat_data(
                    session_id=session_id,
                    chat_history=event["chat_history"],
                    llm_history=event.get("llm_history") or current.get("llm_history", []),
                    debug_messages=current.get("debug_messages", []),
                )
            session_events.publish(session_id, "history_replaced", {"chat_history": event["chat_history"]})
            debug(f"✅ Pre-Message Compression: {len(event['chat_history'])} UI messages")


# ── The agent's answer ───────────────────────────────────────────────────


async def _respond(
    session_id: str,
    view: RuntimeSettings,
    llm_client: Any,
    agent: str,
    user_text: str,
    llm_user_text: str,
    intent: _Intent,
    source: str,
) -> None:
    """The agent answers with its full toolkit; text streams to the clients, the finished reply is saved."""
    from .agent_config import get_agent_config
    from .agent_memory import prepare_agent_toolkit
    from .agent_settings import (
        get_agent_base_model_id,
        resolve_agent_temperature,
        thinking_fields,
        thinking_mode,
    )
    from .bubble import render_bubble
    from .context_manager import estimate_tokens, estimate_toolkit_tokens
    from .formatting import (
        assistant_history_entry,
        build_agent_panel_entry,
        format_number,
    )
    from .llm_client import build_llm_options
    from .llm_pipeline import PipelineResult, run_llm_stream
    from .message_builder import build_messages_from_llm_history, inject_before_question
    from .prompt_loader import get_agent_direct_prompt
    from .research.context_utils import get_agent_num_ctx
    from .session_storage import load_session
    from .tool_output_cap import budget_var, compute_budget

    config = get_agent_config(agent)
    if config is None:
        raise TurnRejected("agent", f"Unknown agent: {agent}")
    label, emoji = config.display_name, config.emoji
    model = effective_model_id(view, agent) or effective_model_id(view, "aifred")
    debug(f"{emoji} {label}-LLM: {model} ({view.backend_type})")
    num_ctx, ctx_source = get_agent_num_ctx(agent, view, get_agent_base_model_id(view, agent))
    debug(f"   🎯 Context: {format_number(num_ctx)} ({ctx_source})")

    memory_ctx, toolkit = await prepare_agent_toolkit(
        agent, user_text, lang=intent.language, memory_enabled=True, research_tools_enabled=True,
        session_id=session_id, source=source,
    )
    system_prompt = get_agent_direct_prompt(agent, lang=intent.language, memory=True)
    history = (load_session(session_id) or {}).get("data", {}).get("llm_history", [])
    messages: list[dict[str, Any]] = build_messages_from_llm_history(
        history[:-1], llm_user_text, perspective=agent, detected_language=intent.language,
    )
    messages.insert(0, {"role": "system", "content": system_prompt})
    memory_tokens = 0
    if memory_ctx:
        # After the system prompt, right before the question: the prefix cache keeps the history.
        inject_before_question(messages, memory_ctx)
        memory_tokens = estimate_tokens([{"content": memory_ctx}])
        debug(f"🧠 Memory context injected ({format_number(memory_tokens)} tok, before question)")

    temperature = resolve_agent_temperature(view, agent)
    system_tokens = estimate_tokens([{"content": system_prompt}], model_name=model)
    history_tokens = estimate_tokens([m for m in messages if m["role"] != "system"], model_name=model)
    tool_tokens = estimate_toolkit_tokens(toolkit, model_name=model)
    total = system_tokens + tool_tokens + history_tokens
    debug(
        f"📊 Prompt: System {format_number(system_tokens)} + Tools {format_number(tool_tokens)} + "
        f"History {format_number(history_tokens)} = {format_number(total)} / {format_number(num_ctx)} tok"
    )
    debug(f"🌡️ Temperature: {format_number(temperature, 1)}")
    budget_var.set(compute_budget(num_ctx, system_tokens, history_tokens, memory_tokens, tool_tokens))
    # The direct chat thinks as AIfred's row says for the model the answering agent runs on.
    enable_thinking, reasoning_effort = thinking_fields(thinking_mode(view, "aifred", model))
    options = replace(
        build_llm_options(view, agent, model, temperature, num_ctx),
        enable_thinking=enable_thinking, reasoning_effort=reasoning_effort,
    )
    if toolkit:
        debug(f"🔧 Toolkit: {[tool.name for tool in toolkit.tools]} for {label}")

    session_events.publish(session_id, "agent_started", {"agent": agent, "display_name": label, "emoji": emoji})
    stream = _TokenStream(session_id)
    result: PipelineResult | None = None
    try:
        async for event in run_llm_stream(
            llm_client, model, messages, options, label, source=source, toolkit=toolkit, on_debug=debug,
        ):
            result = _handle_stream_event(session_id, event, stream, view.ui_language) or result
    except asyncio.CancelledError:
        _save_partial(session_id, agent, stream.text, view.ui_language)
        raise
    stream.flush()
    if result is None:
        return
    debug(result.debug_msg)
    content = render_bubble(result.text, result.artifacts, model_name=f"{label} ({model})")
    metadata = {**result.metadata_dict}
    if intent.load_time and metadata:
        metadata["load_time"] = metadata.get("load_time", 0.0) + intent.load_time
    entry = build_agent_panel_entry(agent, content, mode="direct", metadata=metadata, lang=intent.language)
    history_entry = assistant_history_entry(agent, result.answer_text, result.history_notes)
    _append(session_id, chat=[entry], llm=[history_entry] if history_entry else [])
    session_events.publish(session_id, "message_added", {"entry": entry})
    debug("────────────────────")


class _TokenStream:
    """Text pieces of the answer, sent as ``token`` events at most every TURN_TOKEN_EVENT_INTERVAL_S."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.text = ""
        self._pending = ""
        self._sent_at = 0.0

    def add(self, piece: str) -> None:
        self.text += piece
        self._pending += piece
        if time.monotonic() - self._sent_at >= TURN_TOKEN_EVENT_INTERVAL_S:
            self.flush()

    def flush(self) -> None:
        if self._pending:
            session_events.publish(self.session_id, "token", {"text": self._pending})
            self._pending = ""
        self._sent_at = time.monotonic()


def _handle_stream_event(session_id: str, event: dict[str, Any], stream: _TokenStream, lang: str) -> Any:
    """Turn one pipeline event into events and debug lines; returns the result when it arrives."""
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
        debug(f"🔧 {format_tool_call(name, arguments)}")
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
    from .formatting import assistant_history_entry, build_agent_panel_entry
    from .i18n import t

    if not text.strip():
        return
    content = f"{text}\n\n{t('generation_interrupted', lang=lang)}"
    entry = build_agent_panel_entry(agent, content, lang=lang)
    history_entry = assistant_history_entry(agent, content)
    _append(session_id, chat=[entry], llm=[history_entry] if history_entry else [])
    session_events.publish(session_id, "message_added", {"entry": entry})
    debug(f"⚠️ Partial response rescued ({len(text)} chars) after aborted generation")


# ── Session file and title ───────────────────────────────────────────────


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


def _start_title(session_id: str, view: RuntimeSettings) -> None:
    """After the first exchange the session gets a title, in the background."""
    from .llm_engine import first_exchange
    from .session_storage import get_session_title, load_session

    if get_session_title(session_id):
        return
    exchange = first_exchange((load_session(session_id) or {}).get("data", {}).get("llm_history", []))
    if exchange is not None:
        asyncio.create_task(_title(session_id, view, *exchange), name=f"title-{session_id[:8]}")


async def _title(session_id: str, view: RuntimeSettings, user_text: str, reply: str) -> None:
    from .llm_engine import generate_session_title

    with session_scope(session_id):
        debug("Generating session title...")
        try:
            # AIfred's model with its context: a title call must not make the backend reload it.
            title = await generate_session_title(
                user_text, reply, session_id, lang=view.ui_language,
                model_override=effective_model_id(view, "aifred"),
                num_ctx_override=view.agent_tuning["aifred"].max_context,
            )
        except Exception as error:  # noqa: BLE001 — background task: a missing title is logged, the turn is done
            log_exception("Session title failed", error)
            return
    if title:
        session_events.publish(session_id, "session_title", {"title": title})
