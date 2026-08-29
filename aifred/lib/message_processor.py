"""Message Processor — bridge between Message Hub and AIfred Engine.

Handles the complete flow for inbound messages:
1. Find or create a session (via routing table)
2. Call the AIfred engine (call_llm)
3. Collect the response
4. Optionally send a reply (auto-reply)
5. Update the session with the conversation
"""

import contextvars
import secrets
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from .config import MESSAGE_HUB_OWNER
from .envelope import InboundMessage, OutboundMessage
from .logging_utils import log_message
from .routing_table import routing_table
from .session_storage import create_empty_session, update_chat_data

# Global notification file for Message Hub events (read by UI timer)
_NOTIFICATION_FILE = None


def _get_notification_path() -> Path:
    global _NOTIFICATION_FILE
    if _NOTIFICATION_FILE is None:
        from .config import DATA_DIR
        _NOTIFICATION_FILE = DATA_DIR / "message_hub" / "notification.json"
        _NOTIFICATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    return _NOTIFICATION_FILE  # type: ignore[no-any-return]


def write_hub_notification(
    session_id: str, session_title: str, channel: str, sender: str,
    status: str = "received",
) -> None:
    """Write a notification for the UI to pick up.

    status: ``received`` | ``processing`` | ``done`` | ``error``.
    All notifications carry the same toast id (``hub`` in the UI),
    so a later status overwrites the previous toast — including the
    long-lived ``received``/``processing`` toasts which would otherwise
    linger for two minutes (their hard duration cap).
    """
    import json
    path = _get_notification_path()
    notification = {
        "session_id": session_id,
        "session_title": session_title,
        "channel": channel,
        "sender": sender,
        "status": status,
    }
    path.write_text(json.dumps(notification), encoding="utf-8")


def read_and_clear_hub_notification() -> dict | None:
    """Read and delete the notification file. Returns dict or None."""
    import json
    path = _get_notification_path()
    if not path.exists():
        return None
    try:
        data: dict = json.loads(path.read_text(encoding="utf-8"))
        path.unlink()
        return data
    except (json.JSONDecodeError, OSError):
        return None


# ============================================================
# Hub Notification Scope — SSoT for received → done/error lifecycle
# ============================================================

# Inheritance flag for nested hub_notification_scope.
# Set by HubNotifier.delegate() and consumed by the next scope's __enter__
# in the same async task. Async-safe via contextvars (ContextVar copies on
# task spawn, so concurrent pipelines never see each other's state).
_pending_inherit: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_hub_scope_pending_inherit", default=False,
)


class HubNotifier:
    """Mid-pipeline status updater + final-state override.

    Yielded by :func:`hub_notification_scope`. Use:

    * ``notifier.update("processing")`` — write any intermediate phase
      (overwrites the prior toast via shared id).
    * ``notifier.fail()`` — mark the final exit as ``error`` instead of
      ``done``, without raising. For non-exception failures (e.g. engine
      returned no response). If an exception bubbles out of the ``with``
      block, the scope writes ``error`` automatically — calling
      ``.fail()`` first is harmless but unnecessary.
    * ``notifier.delegate()`` — hand off the closing notification to a
      nested pipeline (e.g. ``process_inbound`` is called immediately
      after this scope exits). The current scope writes no final toast,
      and the next scope in the same task inherits the lifecycle: it
      skips its own ``received`` write so the toast keeps the current
      ``processing`` state. The inner scope still writes the final
      ``done``/``error`` on its own exit.
    """

    def __init__(self, write_fn):  # type: ignore[no-untyped-def]
        self._write = write_fn
        self.failed = False
        self.delegated = False

    def update(self, status: str) -> None:
        self._write(status)

    def fail(self) -> None:
        self.failed = True

    def delegate(self) -> None:
        self.delegated = True
        # Tell the next scope in this task to inherit (skip its "received").
        _pending_inherit.set(True)


@contextmanager
def hub_notification_scope(
    session_id: str, session_title: str, channel: str, sender: str,
) -> Iterator[HubNotifier]:
    """Single source of truth for an inbound message's notification lifecycle.

    On entry, writes ``received`` (unless inheriting from a delegated
    parent scope — see ``HubNotifier.delegate``). On normal exit, writes
    ``done`` (or ``error`` if the caller invoked ``notifier.fail()``).
    On an exception, writes ``error`` and re-raises. If the caller
    invoked ``notifier.delegate()``, the scope writes nothing on exit —
    the next scope in the same task takes over.
    """
    def _write(status: str) -> None:
        write_hub_notification(session_id, session_title, channel, sender, status=status)

    inherited = _pending_inherit.get()
    if inherited:
        _pending_inherit.set(False)  # consume the inheritance flag
    else:
        _write("received")

    notifier = HubNotifier(_write)
    raised = False
    try:
        yield notifier
    except BaseException:
        # BaseException covers asyncio.CancelledError (pipeline cancellation
        # via _stop wake-word, browser stop button), KeyboardInterrupt and
        # the normal Exception tree. Without this catch the toast would stay
        # on "processing" forever after a cancelled run.
        raised = True
        if not notifier.delegated:
            _write("error")
        raise
    finally:
        if not raised and not notifier.delegated:
            _write("error" if notifier.failed else "done")


def resolve_user_name(channel: str, channel_id: str, sender: str) -> str:
    """Resolve external identity to AIfred user name via user_mapping.json.

    Checks if the channel_id (e.g. telegram user ID, email address)
    is mapped to a known AIfred user. Returns the mapped name or
    the original sender name if no mapping exists.
    """
    import json
    from .config import DATA_DIR

    mapping_path = DATA_DIR / "user_mapping.json"
    if not mapping_path.exists():
        return sender

    try:
        mappings = json.loads(mapping_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return sender

    for user_name, channels in mappings.items():
        ids = channels.get(channel, [])
        if channel_id in ids or sender in ids:
            return str(user_name)

    return sender


async def detect_target_agent_via_llm(text: str) -> tuple[str, str, str, dict]:
    """Detect target agent, intent, language and mode-switch via LLM.

    Uses detect_query_intent_and_addressee() — single source of truth, same
    pipeline as the browser. Model is resolved via
    get_effective_model_from_settings() so that TTS-calibrated profiles
    (e.g. "-tts-moss") are used when a GPU TTS engine is running. Otherwise
    llama-swap would try to load the base profile which segfaults due to
    insufficient VRAM.

    Returns:
        (agent_id, intent, detected_language, mode_switch_updates)

        mode_switch_updates is a dict with optional keys ``active_agent``,
        ``multi_agent_mode``, ``research_mode``, ``symposion_agents`` —
        empty dict if the user did not request a mode change.
    """
    from .intent_detector import detect_query_intent_and_addressee
    from .llm_client import LLMClient
    from .settings import load_settings
    from .config import get_effective_model_from_settings

    settings = load_settings() or {}
    backend_type = settings.get("backend_type", "llamacpp")

    # Intent-Detection ist eine Automatik-Hilfsaufgabe — Settings lesen,
    # nicht hartcodiert auf AIfred. Bei "Automatik = (wie Alfred-LLM)" ist
    # automatik_model leer und get_effective_model_from_settings() faellt
    # intern auf aifred_model zurueck (siehe config.py).
    automatik_model = get_effective_model_from_settings("automatik")

    if not automatik_model:
        log_message("Message Processor: no model for intent detection, defaulting to aifred")
        return "aifred", "FAKTISCH", "de", {}

    client = LLMClient(backend_type)

    intent, addressee, lang, mode_switch, _is_pure_cmd, _raw = await detect_query_intent_and_addressee(
        user_query=text,
        automatik_model=automatik_model,
        llm_client=client,
        automatik_num_ctx=None,
    )
    from .intent_detector import format_intent_result
    agent = addressee or "aifred"
    log_message(f"🎯 {format_intent_result(intent, addressee, lang)}")
    return agent, intent, lang, mode_switch


async def dispatch_inbound(message: InboundMessage, channel_label: str) -> None:
    """Standard-Dispatch für Channel-Listener: ``process_inbound`` plus
    einheitliches Ergebnis-Log (SSOT — existierte vorher als Kopie in
    discord/email und verkürzt in telegram, das den Ausgang verschluckte).
    """
    outbound = await process_inbound(message)
    if outbound:
        log_message(
            f"{channel_label}: processed — reply "
            f"{'sent' if outbound.metadata.get('sent') else 'ready'} "
            f"for {outbound.recipient}"
        )


async def process_inbound(message: InboundMessage, user_saved: bool = False) -> Optional[OutboundMessage]:
    """Process an inbound message through the full pipeline.

    Args:
        message: The inbound message to process.
        user_saved: If True, the user question is already saved to the session
                    (both chat_history and llm_history). Skips _save_to_session.
                    Used by FreeEcho.2 for early browser flush after STT.

    Uses the Debug Bus (session_scope) so all debug() calls from any
    depth (tools, research, plugins) automatically go to the session.
    """
    from .debug_bus import debug, session_scope
    from .session_storage import get_session_title
    from .plugin_registry import get_channel

    # Kalibrier-Gate: Während einer GPU-Kalibrierung gehört das VRAM
    # exklusiv der Messung — eine Inferenz würde llama-swap neu starten
    # und die Messwerte verderben. Die Hub-Kanäle laufen an Reflex vorbei,
    # deshalb das prozessweite Flag statt is_calibrating (Session-State).
    # Höfliche Text-Absage statt stillem Verschlucken (der Rückgabewert
    # wird von den Aufrufern nur geloggt — gesendet wird hier). Ausnahme
    # freeecho2: eine Voice-Reply würde die TTS-Synthese anwerfen — genau
    # die GPU-Kollision, die das Gate verhindert; dort bleibt es beim Log.
    from .calibration_gate import is_calibration_active
    if is_calibration_active():
        log_message(
            f"🛑 Inbound via {message.channel} rejected: calibration in progress"
        )
        gate_reply = OutboundMessage(
            channel=message.channel,
            channel_id=message.channel_id,
            recipient=message.sender,
            text=(
                "AIfred is currently calibrating GPU profiles and cannot "
                "process requests. Please try again in a few minutes."
            ),
            # Marker für Kanäle mit eigenem Absage-Signal: der Puck spielt
            # darauf seinen LOKALEN Notification-Sound (kein TTS nötig).
            metadata={"calibration_gate": True},
        )
        gate_plugin = get_channel(message.channel)
        if gate_plugin is not None and message.channel != "freeecho2":
            if hasattr(gate_plugin, "build_reply_metadata"):
                gate_reply.metadata.update(gate_plugin.build_reply_metadata(message))
            try:
                await gate_plugin.send_reply(gate_reply, message)
                gate_reply.metadata["sent"] = True
            except Exception as e:  # noqa: BLE001
                log_message(
                    f"⚠️ Calibration-gate reply via {message.channel} failed: {e}"
                )
        return gate_reply

    # 0. Determine security tier for this channel
    from .security import resolve_tier_for_sender
    max_tier = resolve_tier_for_sender(message.channel, message.sender, message.metadata)

    # 1. Sanitize inbound text (strip HTML, zero-width chars, normalize)
    from .security import sanitize_inbound
    message.text = sanitize_inbound(message.text)

    # 1b. Resolve external identity to AIfred user name
    original_sender = message.sender
    resolved_name = resolve_user_name(message.channel, message.channel_id, message.sender)
    if resolved_name != message.sender:
        log_message(f"User mapping: {message.sender} -> {resolved_name}")
        message.sender = resolved_name

    # 2. Find or create session via routing table (BEFORE intent detection
    #    so session_scope is active during LLM calls → debug messages go to UI)
    route = routing_table.get_route(message.channel, message.channel_id)
    subject = message.metadata.get("subject", "?")

    if route:
        session_id = route.session_id
        log_message(f"Message Processor: existing session {session_id[:8]}")
    else:
        session_id = secrets.token_hex(16)
        # Tag with the origin channel so the session's provenance stays
        # visible — the browser's auto-load deliberately picks it up too when
        # it is the most recent one (see _load_latest_session()).
        create_empty_session(session_id, owner=MESSAGE_HUB_OWNER, channel=message.channel)
        routing_table.set_route(message.channel, message.channel_id, session_id)
        log_message(f"Message Processor: new session {session_id[:8]} for {message.sender}")

    # Resolve plugin and channel label
    plugin = get_channel(message.channel)
    channel_label = plugin.display_name if plugin else message.channel.capitalize()
    notification_title = get_session_title(session_id) or subject

    # Register this coroutine so external stop commands (FreeEcho.2 _stop wake-word,
    # browser stop button, ...) can cancel it via cancel_pipeline().
    # The pipeline_scope context manager handles register + unregister even on
    # exceptions/cancellation.
    import asyncio as _asyncio
    from .pipeline_registry import pipeline_scope
    _current_task = _asyncio.current_task()

    # ── All phases run inside session_scope ────────────────────
    # Intent detection runs INSIDE scope so its debug messages reach the UI.
    # hub_notification_scope writes "received" on entry and guarantees a final
    # done/error on exit — covers every return path including exceptions in
    # _call_engine, auto_reply, generate_session_title, etc.
    with pipeline_scope(session_id, _current_task), session_scope(session_id), \
            hub_notification_scope(
                session_id, notification_title, channel_label, message.sender,
            ) as hub:

        # ── Phase 0: Show incoming message immediately ────────
        debug(f"📨 {channel_label}: message from {message.sender}")
        if subject and subject != "?":
            debug(f"📧 Subject: {subject}")

        # ── Phase 1: Detect target agent via LLM ─────────────
        try:
            agent, intent, detected_lang, mode_switch_updates = await detect_target_agent_via_llm(message.text)
        except Exception as e:
            log_message(f"❌ Intent detection FAILED (no fallback): {e}", "error")
            debug(f"❌ Intent detection FAILED: {e}")
            raise

        from .agent_config import get_agent_config as _get_agent_cfg, get_agent_label
        from .intent_detector import format_intent_result, format_mode_switch_summary
        from .session_storage import get_session_config, update_session_config

        # Apply mode-switch updates (multi_agent_mode, research_mode,
        # symposion_agents, active_agent) before routing — so any active_agent
        # override from a voice command takes effect immediately.
        if mode_switch_updates:
            update_session_config(session_id, **mode_switch_updates)
            summary = format_mode_switch_summary(mode_switch_updates, lang=detected_lang)
            debug(f"🔧 Mode switch: {summary}")
            # Voice mode-switch via "agent=..." overrides whatever the LLM
            # picked as addressee for this turn.
            if "active_agent" in mode_switch_updates:
                agent = mode_switch_updates["active_agent"]

        # Routing priority (highest wins):
        #   1. Wake-Override (channel hint, e.g. FreeEcho.2 wake-word)
        #   2. LLM-detected addressee from current query
        #   3. Session active_agent (sticky from previous turn)
        #   4. Default "aifred"
        wake_agent = message.metadata.get("wake_agent")
        llm_addressee = agent if agent != "aifred" else None
        session_active_raw = get_session_config(session_id).get("active_agent", "aifred")
        # For sticky-fallback logic below, "aifred" is treated as "no preference"
        # so the routing pipeline doesn't loop back to the default agent.
        session_active = None if session_active_raw == "aifred" else session_active_raw

        if wake_agent and _get_agent_cfg(wake_agent):
            if wake_agent != agent:
                debug(
                    f"🎯 Wake-Override: {get_agent_label(wake_agent)} "
                    f"(LLM would have chosen {get_agent_label(agent)})"
                )
            agent = wake_agent
        elif wake_agent:
            debug(f"⚠️  Wake-Agent '{wake_agent}' not configured — keeping LLM decision")
            # llm_addressee or session_active will take effect below
            if llm_addressee is None and session_active:
                agent = session_active
                debug(f"🎯 Sticky agent: {get_agent_label(agent)} (from session)")
        elif llm_addressee is None and session_active:
            # No wake hint, no explicit addressee in this query — use sticky agent
            agent = session_active
            debug(f"🎯 Sticky agent: {get_agent_label(agent)} (from session)")

        # Persist any explicit address (wake-override or inline) as the new
        # active_agent so subsequent unaddressed turns route to the same agent
        # (sticky). Includes switches back to "aifred" — an explicit switch is
        # an explicit switch, regardless of which agent is the target.
        if agent != session_active_raw:
            from .session_storage import set_session_active_agent
            set_session_active_agent(session_id, agent)

        message.target_agent = agent

        _cfg = _get_agent_cfg(message.target_agent)
        agent_display_name = _cfg.display_name if _cfg else message.target_agent.capitalize()

        debug(f"🎯 {format_intent_result(intent, agent if agent != 'aifred' else None, detected_lang)}")

        # Save incoming message to session (chat + llm history)
        if not user_saved:
            save_user_to_session(session_id, message)

        # ── Phase 2: Call AIfred engine ───────────────────────
        hub.update("processing")

        if plugin:
            llm_context = plugin.build_context(message)
        else:
            sender_info = message.sender
            if original_sender != message.sender:
                sender_info = f"{message.sender} (via {original_sender})"
            llm_context = f"[{channel_label} from {sender_info}]\n\n{message.text}"

        # Wrap external messages in security delimiters. Trust label from
        # the authenticated owner verdict (N5) — the raw sender string is
        # forgeable (email From), the mapped name is derived from it.
        from .security import wrap_external_message, resolve_trust_label
        trust = resolve_trust_label(message.channel, original_sender, message.metadata)
        llm_context = wrap_external_message(
            llm_context, message.sender, message.channel, trust,
        )

        response_text, result_metadata = await _call_engine(
            user_text=llm_context,
            session_id=session_id,
            agent=message.target_agent,
            max_tier=max_tier,
            source=message.channel,
            metadata=dict(message.metadata or {}),
        )

        if not response_text:
            log_message("Message Processor: engine returned no response", "warning")
            debug("❌ Engine: no response")
            hub.fail()  # scope writes "error" on exit
            return None

        debug(f"✅ Response generated ({len(response_text)} chars)")

        # ── Phase 3: Save response to session ─────────────────
        # M3: the user turn goes into llm_history HERE (wrapped) — see
        # _append_response/save_user_to_session docstrings.
        _append_response(
            session_id, response_text,
            metadata=result_metadata, agent=message.target_agent,
            user_llm_text=llm_context,
        )

        # ── Phase 3b: Sanitize output for external channels ───
        from .security import sanitize_outbound
        outbound_text = sanitize_outbound(response_text)

        # Prefix with agent name if not AIfred (so user knows who answered)
        if message.target_agent != "aifred":
            outbound_text = f"— {agent_display_name} —\n\n{outbound_text}"

        # ── Phase 4: Auto-reply if enabled ────────────────────
        reply_metadata = plugin.build_reply_metadata(message) if plugin else {}
        # silent_reply: vom audio_player gesetzt wenn Audio-Wiedergabe
        # gestartet wurde — der Channel skippt dann die TTS-Bestaetigung,
        # damit Music sofort spielt ohne dass der Butler dazwischenredet.
        if result_metadata.get("silent_reply"):
            reply_metadata["silent_reply"] = True
        # Internal triggers (scheduler, webhook) need the resolved session_id
        # to hand off to their delivery layer. Plugins ignore this field.
        reply_metadata["session_id"] = session_id
        outbound = OutboundMessage(
            channel=message.channel,
            channel_id=message.channel_id,
            recipient=message.sender,
            text=outbound_text,
            metadata=reply_metadata,
        )

        auto_reply_enabled = _is_auto_reply_enabled(message.channel)
        if plugin and auto_reply_enabled:
            await plugin.send_reply(outbound, message)
        else:
            debug("💬 Response ready (auto-reply off)")

        debug("────────────────────")

        # ── Phase 5: Generate title if missing ────────────────
        from .llm_engine import generate_session_title
        title = get_session_title(session_id)
        if not title:
            await generate_session_title(message.text, response_text, session_id)

        # ── Phase 6: Done ─────────────────────────────────────
        # No explicit notification call — hub_notification_scope writes
        # "done" on normal exit (and "error" on exception or hub.fail()).

    # session_scope exit
    return outbound


async def _call_engine(
    user_text: str,
    session_id: str,
    agent: str = "aifred",
    max_tier: int = 4,
    source: str = "browser",
    metadata: Optional[dict] = None,
) -> tuple[str, dict]:
    """Call the AIfred engine with full toolkit (memory + plugins).

    Returns (response_text, metadata_dict).
    Debug messages go through the Debug Bus (session_scope must be active).
    """
    from .debug_bus import debug
    from .llm_engine import call_llm
    from .session_storage import load_session
    from .settings import load_settings
    from .agent_settings import get_persisted_tuning
    from .config import (
        DEFAULT_SETTINGS, DEFAULT_TEMPERATURE, BACKEND_URLS,
        MAIN_LLM_FALLBACK_CONTEXT,
    )


    # Load current settings
    settings = load_settings() or {}
    backend_type = settings.get("backend_type", DEFAULT_SETTINGS["backend_type"])
    temperature_mode = settings.get("temperature_mode", "auto")
    temperature = get_persisted_tuning(settings, agent, "temperature", DEFAULT_TEMPERATURE)
    enable_thinking = settings.get("enable_thinking", False)

    # Get effective model for the agent (respects TTS/speed variants)
    from .config import get_effective_model_from_settings
    model = get_effective_model_from_settings(agent)
    backend_url = BACKEND_URLS.get(backend_type, "")


    if not model:
        log_message(f"Message Processor: no model configured for {agent}/{backend_type}", "error")
        return "", {}

    # Load existing LLM history from session
    session = load_session(session_id)
    llm_history = session.get("data", {}).get("llm_history", []) if session else []


    # Resolve calibrated context (no State needed). ``model`` is already the
    # effective, variant-resolved id (base + suffix from
    # get_effective_model_from_settings), so the context read matches the
    # profile that actually loads. Label the variant honestly instead of
    # blindly "native" — otherwise a loaded -vlm-/-speed-/-tts- profile is
    # indistinguishable from the bare base in the FreeEcho debug log.
    from .research.context_utils import get_model_native_context
    num_ctx = get_model_native_context(model, backend_type)
    _marker_idx = [model.find(m) for m in ("-tts-", "-vlm-", "-speed")]
    _marker_idx = [i for i in _marker_idx if i > 0]
    ctx_label = model[min(_marker_idx):].lstrip("-") if _marker_idx else "native"
    if num_ctx <= 0:
        num_ctx = MAIN_LLM_FALLBACK_CONTEXT
        ctx_label = "fallback"


    from .agent_config import get_agent_config
    _agent_cfg = get_agent_config(agent)
    agent_display = _agent_cfg.display_name if _agent_cfg else agent.capitalize()

    from .agent_config import get_agent_emoji
    debug(f"{get_agent_emoji(agent)} {agent_display}-LLM: {model} ({backend_type})")
    debug(f"📜 History: {len(llm_history)} messages")


    # Prepare full toolkit (memory + all plugin tools)
    lang = settings.get("ui_language", "de")
    memory_enabled = settings.get("agent_memory_enabled", True)

    toolkit = None
    memory_ctx = ""
    from .agent_memory import prepare_agent_toolkit

    memory_ctx, toolkit = await prepare_agent_toolkit(
        agent, user_text, lang=lang,
        memory_enabled=memory_enabled,
        research_tools_enabled=True,
        session_id=session_id,
        max_tier=max_tier,
        source=source,
        metadata=metadata,
    )

    if toolkit:
        debug(f"🔧 Toolkit: {[t.name for t in toolkit.tools]} for {agent_display}")

    # Collect response — debug chunks go through the Bus automatically
    response_parts: list[str] = []
    try:
        async for chunk in call_llm(
            user_text=user_text,
            model_choice=model,
            history=[],
            llm_history=llm_history,
            detected_intent="ALLGEMEIN",
            detected_language=lang,
            temperature_mode=temperature_mode,
            temperature=temperature,
            backend_type=backend_type,
            backend_url=backend_url,
            enable_thinking=enable_thinking,
            num_ctx_manual_enabled=True,
            num_ctx_manual_value=num_ctx,
            num_ctx_source_label=ctx_label,
            agent=agent,
            external_toolkit=toolkit,
            memory_ctx=memory_ctx if memory_ctx else None,
            source=source,
        ):
            if chunk.get("type") == "content":
                response_parts.append(chunk.get("text", ""))
            elif chunk.get("type") == "debug":
                debug(chunk.get("message", ""))
            elif chunk.get("type") == "result":
                data = chunk.get("data", {})
                if "response_clean" in data:
                    result_meta = data.get("metadata_dict", {})
                    return data["response_clean"], result_meta
    except Exception as exc:
        log_message(f"Message Processor: engine error — {exc}", "error")
        debug(f"❌ Engine error: {exc}")
        return "", {}

    return "".join(response_parts), {}


def build_user_chat_content(message: InboundMessage) -> str:
    """Build the chat_history content string for a user message.

    Single source of truth for the "[Channel] Sender" header format.
    Used by both the normal save path and FreeEcho.2's early flush.
    """
    from .plugin_registry import get_channel as _get_ch
    _ch = _get_ch(message.channel)
    channel_label = _ch.display_name if _ch else message.channel.capitalize()
    subject = message.metadata.get("subject", "")
    header = f"[{channel_label}] {message.sender}"
    if subject:
        header += f" — {subject}"
    return f"{header}\n\n{message.text}"


def save_user_to_session(session_id: str, message: InboundMessage) -> None:
    """Save user message to the session CHAT history (UI) and set update flag.

    Single source of truth for persisting inbound user messages.
    Called by process_inbound (normal path) or directly by channels
    that need early browser flush (FreeEcho.2 after STT).

    M3: Deliberately does NOT touch llm_history. The LLM-facing entry is
    appended WRAPPED (<external_message> fence) together with the response
    after the engine call (_append_response) — the fence must persist
    across turns, and saving it before the call would put the current
    message twice into the prompt (raw from history + wrapped as
    current_user_text).
    """
    from .session_storage import load_session, session_rmw_lock

    # M4: load→append→save as ONE unit — the browser thread writes the
    # same file (see session_rmw_lock in session_storage).
    with session_rmw_lock:
        session = load_session(session_id)
        data = session.get("data", {}) if session else {}
        existing_chat = data.get("chat_history", [])

        existing_chat.append({"role": "user", "content": build_user_chat_content(message)})

        # Browser detects via session file mtime-watch (SSOT)
        update_chat_data(
            session_id=session_id,
            chat_history=existing_chat,
            debug_messages=data.get("debug_messages", []),
            owner=MESSAGE_HUB_OWNER,
        )


def _append_response(
    session_id: str,
    response_text: str,
    metadata: dict | None = None,
    agent: str = "aifred",
    user_llm_text: str | None = None,
) -> None:
    """Append the assistant response to an existing session.

    If metadata is provided, appends a performance footer (TTFT, tok/s, etc.)
    to the chat content — same format as browser-path add_agent_panel.

    M3: ``user_llm_text`` is the LLM-facing user message (wrapped in
    <external_message> security delimiters), appended to llm_history right
    before the response. Persisting the WRAPPED form keeps the injection
    fence around external content in every future turn; appending it only
    after the engine call keeps the current message from appearing twice
    in the prompt. On engine failure neither entry is written — the LLM
    never saw an answer, so the exchange stays out of its history.
    """
    from .session_storage import load_session, session_rmw_lock
    from .formatting import format_performance_footer, build_assistant_chat_entry

    # Build metadata footer (shared with browser-path add_agent_panel)
    display_content = response_text
    if metadata:
        meta_footer = format_performance_footer(metadata)
        if meta_footer:
            display_content = f"{response_text}\n\n{meta_footer}"

    # M4: load→append→save as ONE unit (see session_rmw_lock).
    with session_rmw_lock:
        session = load_session(session_id)
        data = session.get("data", {}) if session else {}
        existing_chat = data.get("chat_history", [])
        existing_llm = data.get("llm_history", [])

        if user_llm_text:
            existing_llm.append({"role": "user", "content": user_llm_text})

        # SSOT: same dict shape as browser-path add_agent_panel
        existing_chat.append(build_assistant_chat_entry(display_content, agent, metadata))
        existing_llm.append({"role": "assistant", "content": response_text})

        # Browser detects via session file mtime-watch (SSOT)
        update_chat_data(
            session_id=session_id,
            chat_history=existing_chat,
            llm_history=existing_llm,
            owner=MESSAGE_HUB_OWNER,
        )




def _is_auto_reply_enabled(channel: str) -> bool:
    """Check if auto-reply is enabled for a given channel.

    If the channel plugin declares always_reply=True, auto-reply is
    always on regardless of the toggle setting.
    """
    from .plugin_registry import get_channel
    plugin = get_channel(channel)
    if plugin and plugin.always_reply:
        return True
    from .settings import load_settings
    settings = load_settings() or {}
    channel_toggles = settings.get("channel_toggles", {})
    return bool(channel_toggles.get(channel, {}).get("auto_reply", False))


# ============================================================
# AUTONOMOUS DELIVERY (SSoT) — used by scheduler + alert pipeline
# ============================================================
# Sending something AIfred initiated (a scheduled result, a proactive alert)
# to a channel and/or surfacing it as a normal browser session. One way to do
# each, reused by every autonomous producer — no parallel send paths.

def _resolve_channel_recipient(channel: str, recipient: str) -> str:
    """Resolve a recipient for an autonomous channel send.

    A given name → channel-specific ID via user_mapping.json; a raw ID passes
    through. Empty → auto-resolve (first user's channel id / email_out), then
    fall back to the channel allowlist (owner = first entry)."""
    import json as _json
    from .config import DATA_DIR

    mapping_path = DATA_DIR / "user_mapping.json"
    mappings: dict = {}
    if mapping_path.exists():
        try:
            mappings = _json.loads(mapping_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            mappings = {}

    def _from_mapping(name: str) -> str:
        for user_name, channels in mappings.items():
            if user_name.lower() == name.lower():
                if channel == "email" and channels.get("email_out"):
                    out = channels["email_out"]
                    return out[0] if isinstance(out, list) else str(out)
                ids = channels.get(channel, [])
                if ids:
                    return ids[0] if isinstance(ids, list) else str(ids)
        return ""

    if recipient:
        resolved = _from_mapping(recipient)
        return resolved or recipient

    # Auto: first user's address for this channel
    for user_name, channels in mappings.items():
        if channel == "email" and channels.get("email_out"):
            out = channels["email_out"]
            return out[0] if isinstance(out, list) else str(out)
        ids = channels.get(channel, [])
        if ids:
            return ids[0] if isinstance(ids, list) else str(ids)

    # Fallback: channel allowlist (owner = first entry)
    from .credential_broker import broker
    allowlist_keys = {
        "telegram": ("telegram", "allowed_users"),
        "email": ("email", "allowed_senders"),
        "discord": ("discord", "channel_ids"),
    }
    key = allowlist_keys.get(channel)
    if key:
        allowlist = broker.get(*key)
        if allowlist and allowlist != "*":
            return allowlist.split(",")[0].strip()

    # FreeEcho.2 hat keine Allowlist (lokale Hardware, kein Sender-
    # Filter — siehe has_allowlist=False im Plugin). Stattdessen
    # aufloesen auf den ersten gerade verbundenen Geraete-Room.
    # Push-Targets bleiben damit ohne Konfig "der einzige Puck im LAN"
    # oder bei Multi-Puck "der zuerst verbundene". Wer gezielter pushen
    # will, setzt recipient explizit auf den Room-Namen.
    if channel == "freeecho2":
        try:
            from ..plugins.channels.freeecho2_channel import _devices
        except ImportError:
            return ""
        if _devices:
            return next(iter(_devices.keys()))
    return ""


def _freeecho2_groups() -> dict[str, list[str]]:
    """Lädt Puck-Gruppen aus ``data/freeecho2_groups.json`` (group_name →
    [room, …]). Fehlt/ungültig → keine Gruppen. Rein serverseitig; die
    Firmware kennt nur ``room``."""
    import json as _json
    from .config import DATA_DIR

    path = DATA_DIR / "freeecho2_groups.json"
    if not path.exists():
        return {}
    try:
        raw = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(k): [str(r) for r in v]
        for k, v in raw.items()
        if isinstance(v, list)
    }


def resolve_announce_targets(channel: str, target: str) -> list[str]:
    """Expandiert ein Sink-Ziel in konkrete Empfänger.

    Für ``freeecho2``: ``"*"`` → alle gerade verbundenen Rooms, ``"@gruppe"``
    → konfigurierte Rooms der Gruppe, sonst der einzelne (aufgelöste) Room.
    Andere Kanäle: immer genau ein aufgelöster Empfänger. So bleibt das
    Broadcast/Gruppen-Wissen serverseitig — die Firmware bleibt room-only."""
    if channel == "freeecho2":
        try:
            from ..plugins.channels.freeecho2_channel import _devices
        except ImportError:
            _devices = {}
        t = (target or "").strip()
        # Manche LLMs packen den Kanal-Präfix mit in den target-Param
        # ("freeecho2:wohnzimmer") — defensiv abstreifen.
        if t.startswith("freeecho2:"):
            t = t[len("freeecho2:"):]
        # Broadcast / leer → alle gerade verbundenen Pucks.
        if t in ("", "*"):
            return list(_devices.keys())
        # Gruppe → konfigurierte Rooms, gefiltert auf gerade verbundene.
        if t.startswith("@"):
            return [r for r in _freeecho2_groups().get(t[1:], []) if r in _devices]
        # Expliziter Room: NUR wenn auch verbunden. Ein halluzinierter/
        # unbekannter Room ergibt [] → der Caller meldet sauberen Fehlschlag
        # statt ins Leere zu senden und fälschlich "success" zu melden.
        return [t] if t in _devices else []
    single = _resolve_channel_recipient(channel, target)
    return [single] if single else []


async def announce_to_channel(
    channel: str, recipient: str, text: str, *,
    media: str | None = None, metadata: dict | None = None,
) -> bool:
    """SSoT for sending an autonomous (non-reply) message to a channel.
    Resolves the recipient, builds an OutboundMessage (+ media) and a minimal
    inbound, and calls the channel's ``send_reply`` (the one delivery path)."""
    from datetime import datetime
    from .plugin_registry import get_channel

    plugin = get_channel(channel)
    if not plugin:
        log_message(f"announce: channel '{channel}' not found", "error")
        return False
    target = _resolve_channel_recipient(channel, recipient)
    if not target:
        log_message(f"announce: no recipient/allowlist for '{channel}'", "warning")
        return False
    outbound = OutboundMessage(
        channel=channel, channel_id=target, recipient=target,
        text=text, media=media, metadata=metadata or {},
    )
    dummy = InboundMessage(
        channel=channel, channel_id=target, sender="system",
        text="", timestamp=datetime.now(),
    )
    try:
        await plugin.send_reply(outbound, dummy)
        return True
    except Exception as e:  # noqa: BLE001
        log_message(f"announce: send via '{channel}' failed: {e}", "warning")
        return False


def record_autonomous_turn(
    channel: str, channel_id: str, title: str, text: str, *,
    media: str | None = None,
    media_gallery: list[str] | None = None,
    owner: str = MESSAGE_HUB_OWNER,
) -> str:
    """SSoT for surfacing an autonomous event as a normal browser session.
    Routes to a (stable) session, appends an assistant chat turn, and writes a
    hub notification — same primitives process_inbound persists with, just
    without the LLM. ``media`` (an on-disk frame path) is embedded as a
    Markdown image so the browser session shows exactly what a channel like
    Telegram received. Returns the session_id."""
    from .session_storage import load_session

    route = routing_table.get_route(channel, channel_id)
    if route:
        session_id = route.session_id
    else:
        session_id = secrets.token_hex(16)
        # Tag with the origin channel so the session's provenance stays
        # visible. Same contract as process_inbound: the browser's auto-load
        # picks up a fresh alert when it is the most recent session — that is
        # wanted, the user restarts to see what came in.
        create_empty_session(session_id, owner=owner, channel=channel)
        routing_table.set_route(channel, channel_id, session_id)

    content = text
    if media_gallery:
        # All views already as URLs (wide + zoom + crops) — embed each so the
        # browser session shows the full picture, not just one frame. Frames
        # get one paragraph each (full width); the crops share ONE paragraph
        # so they flow side by side and wrap — a column of head shots pushed
        # every following message off the screen.
        from .face_crop_store import FaceCropStore

        frames = [
            u for u in media_gallery
            if u and not u.startswith(FaceCropStore.URL_PREFIX)
        ]
        crops = [
            u for u in media_gallery
            if u and u.startswith(FaceCropStore.URL_PREFIX)
        ]
        parts = [f"\n\n![{title}]({u})" for u in frames]
        if crops:
            parts.append(
                "\n\n" + " ".join(f"![{title}]({u})" for u in crops)
            )
        content = text + "".join(parts)
    elif media:
        from pathlib import Path
        from .vision_utils import get_image_url
        url = get_image_url(Path(media))
        if url:
            content = f"{text}\n\n![{title}]({url})"

    # M4: load→append→save as ONE unit (see session_rmw_lock).
    from .session_storage import session_rmw_lock
    with session_rmw_lock:
        session = load_session(session_id)
        chat_history = list((session or {}).get("data", {}).get("chat_history", []))
        chat_history.append({"role": "assistant", "content": content})
        update_chat_data(session_id, chat_history, owner=owner)

    write_hub_notification(session_id, title, channel, "system", status="done")
    return session_id
