"""Message Processor — bridge between Message Hub and AIfred Engine.

Handles the complete flow for inbound messages:
1. Find or create a session (via routing table)
2. Call the AIfred engine (call_llm)
3. Collect the response
4. Optionally send a reply (auto-reply)
5. Update the session with the conversation
"""

import secrets
from pathlib import Path
from typing import Optional

from .config import MESSAGE_HUB_OWNER
from .envelope import InboundMessage, OutboundMessage
from .logging_utils import log_message
from .routing_table import routing_table
from .session_storage import create_empty_session, update_chat_data, set_update_flag

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

    status: "received" (message incoming) or "done" (reply sent)
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


async def detect_target_agent_via_llm(text: str) -> tuple[str, str, str]:
    """Detect target agent, intent and language via LLM — same pipeline as browser.

    Uses detect_query_intent_and_addressee() — single source of truth.
    Model is resolved via get_effective_model_from_settings() so that
    TTS-calibrated profiles (e.g. "-tts-moss") are used when a GPU TTS
    engine is running. Otherwise llama-swap would try to load the base
    profile which segfaults due to insufficient VRAM.

    Returns:
        (agent_id, intent, detected_language)
    """
    from .intent_detector import detect_query_intent_and_addressee
    from .llm_client import LLMClient
    from .settings import load_settings
    from .config import get_effective_model_from_settings

    settings = load_settings() or {}
    backend_type = settings.get("backend_type", "llamacpp")

    # Resolve effective model (respects TTS/speed variants — same as _call_engine)
    automatik_model = get_effective_model_from_settings("aifred")

    if not automatik_model:
        log_message("Message Processor: no model for intent detection, defaulting to aifred")
        return "aifred", "FAKTISCH", "de"

    client = LLMClient(backend_type)

    intent, addressee, lang, _raw = await detect_query_intent_and_addressee(
        user_query=text,
        automatik_model=automatik_model,
        llm_client=client,
        automatik_num_ctx=None,
    )
    from .intent_detector import format_intent_result
    agent = addressee or "aifred"
    log_message(f"🎯 {format_intent_result(intent, addressee, lang)}")
    return agent, intent, lang


async def process_inbound(message: InboundMessage) -> Optional[OutboundMessage]:
    """Process an inbound message through the full pipeline.

    Uses the Debug Bus (session_scope) so all debug() calls from any
    depth (tools, research, plugins) automatically go to the session.
    """
    from .debug_bus import debug, session_scope
    from .session_storage import get_session_title
    from .plugin_registry import get_channel

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
        create_empty_session(session_id, owner=MESSAGE_HUB_OWNER)
        routing_table.set_route(message.channel, message.channel_id, session_id)
        log_message(f"Message Processor: new session {session_id[:8]} for {message.sender}")

    # Resolve plugin and channel label
    plugin = get_channel(message.channel)
    channel_label = plugin.display_name if plugin else message.channel.capitalize()

    def _notify(status: str) -> None:
        title = get_session_title(session_id) or subject
        write_hub_notification(session_id, title, channel_label, message.sender, status=status)

    # ── All phases run inside session_scope ────────────────────
    # Intent detection runs INSIDE scope so its debug messages reach the UI.
    with session_scope(session_id):

        # ── Phase 0: Show incoming message immediately ────────
        debug(f"📨 {channel_label}: message from {message.sender}")
        if subject and subject != "?":
            debug(f"📧 Subject: {subject}")
        _notify("received")

        # ── Phase 1: Detect target agent via LLM ─────────────
        try:
            agent, intent, detected_lang = await detect_target_agent_via_llm(message.text)
        except Exception as e:
            log_message(f"❌ Intent detection FAILED (no fallback): {e}", "error")
            debug(f"❌ Intent detection FAILED: {e}")
            raise
        message.target_agent = agent

        from .agent_config import get_agent_config as _get_agent_cfg
        from .intent_detector import format_intent_result
        _cfg = _get_agent_cfg(message.target_agent)
        agent_display_name = _cfg.display_name if _cfg else message.target_agent.capitalize()

        debug(f"🎯 {format_intent_result(intent, agent if agent != 'aifred' else None, detected_lang)}")

        # Save incoming message to session (chat + llm history)
        _save_to_session(session_id, message, "")

        # ── Phase 2: Call AIfred engine ───────────────────────
        _notify("processing")

        if plugin:
            llm_context = plugin.build_context(message)
        else:
            sender_info = message.sender
            if original_sender != message.sender:
                sender_info = f"{message.sender} (via {original_sender})"
            llm_context = f"[{channel_label} from {sender_info}]\n\n{message.text}"

        # Wrap external messages in security delimiters
        from .security import wrap_external_message
        trust = "owner" if message.sender == MESSAGE_HUB_OWNER else "external"
        llm_context = wrap_external_message(
            llm_context, message.sender, message.channel, trust,
        )

        response_text, result_metadata = await _call_engine(
            user_text=llm_context,
            session_id=session_id,
            agent=message.target_agent,
            max_tier=max_tier,
            source=message.channel,
        )

        if not response_text:
            log_message("Message Processor: engine returned no response", "warning")
            debug("❌ Engine: no response")
            _notify("error")
            return None

        debug(f"✅ Response generated ({len(response_text)} chars)")

        # ── Phase 3: Save response to session ─────────────────
        _append_response(session_id, response_text, metadata=result_metadata, agent=message.target_agent)

        # ── Phase 3b: Sanitize output for external channels ───
        from .security import sanitize_outbound
        outbound_text = sanitize_outbound(response_text)

        # Prefix with agent name if not AIfred (so user knows who answered)
        if message.target_agent != "aifred":
            outbound_text = f"— {agent_display_name} —\n\n{outbound_text}"

        # ── Phase 4: Auto-reply if enabled ────────────────────
        reply_metadata = plugin.build_reply_metadata(message) if plugin else {}
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

        # ── Phase 6: Notify UI that processing is complete ────
        _notify("done")

    # session_scope exit
    return outbound


async def _call_engine(
    user_text: str,
    session_id: str,
    agent: str = "aifred",
    max_tier: int = 4,
    source: str = "browser",
) -> tuple[str, dict]:
    """Call the AIfred engine with full toolkit (memory + plugins).

    Returns (response_text, metadata_dict).
    Debug messages go through the Debug Bus (session_scope must be active).
    """
    from .debug_bus import debug
    from .llm_engine import call_llm
    from .session_storage import load_session
    from .settings import load_settings
    from .config import (
        DEFAULT_SETTINGS, BACKEND_URLS,
        MAIN_LLM_FALLBACK_CONTEXT,
    )


    # Load current settings
    settings = load_settings() or {}
    backend_type = settings.get("backend_type", DEFAULT_SETTINGS["backend_type"])
    temperature_mode = settings.get("temperature_mode", "auto")
    temperature = settings.get("temperature", 0.7)
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


    # Resolve calibrated context (no State needed)
    from .research.context_utils import get_model_native_context
    num_ctx = get_model_native_context(model, backend_type)
    ctx_label = "native"
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
            rag_context=memory_ctx if memory_ctx else None,
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


def _save_to_session(
    session_id: str,
    message: InboundMessage,
    response_text: str,
) -> None:
    """Append the inbound message (and optional response) to the session.

    Debug messages are handled by the Debug Bus (not passed here).
    """
    from .session_storage import load_session

    from .plugin_registry import get_channel as _get_ch
    _ch = _get_ch(message.channel)
    channel_label = _ch.display_name if _ch else message.channel.capitalize()
    subject = message.metadata.get("subject", "")
    header = f"[{channel_label}] {message.sender}"
    if subject:
        header += f" — {subject}"

    session = load_session(session_id)
    data = session.get("data", {}) if session else {}
    existing_chat = data.get("chat_history", [])
    existing_llm = data.get("llm_history", [])

    existing_chat.append({"role": "user", "content": header + "\n\n" + message.text})
    existing_llm.append({"role": "user", "content": message.text})

    if response_text:
        existing_chat.append({"role": "assistant", "content": response_text})
        existing_llm.append({"role": "assistant", "content": response_text})

    update_chat_data(
        session_id=session_id,
        chat_history=existing_chat,
        llm_history=existing_llm,
        debug_messages=data.get("debug_messages", []),
        owner=MESSAGE_HUB_OWNER,
    )
    set_update_flag(session_id)


def _append_response(
    session_id: str,
    response_text: str,
    metadata: dict | None = None,
    agent: str = "aifred",
) -> None:
    """Append the assistant response to an existing session.

    If metadata is provided, appends a performance footer (TTFT, tok/s, etc.)
    to the chat content — same format as browser-path add_agent_panel.
    """
    from .session_storage import load_session
    from .formatting import format_performance_footer, build_assistant_chat_entry

    session = load_session(session_id)
    data = session.get("data", {}) if session else {}
    existing_chat = data.get("chat_history", [])
    existing_llm = data.get("llm_history", [])

    # Build metadata footer (shared with browser-path add_agent_panel)
    display_content = response_text
    if metadata:
        meta_footer = format_performance_footer(metadata)
        if meta_footer:
            display_content = f"{response_text}\n\n{meta_footer}"

    # SSOT: same dict shape as browser-path add_agent_panel
    existing_chat.append(build_assistant_chat_entry(display_content, agent, metadata))
    existing_llm.append({"role": "assistant", "content": response_text})

    update_chat_data(
        session_id=session_id,
        chat_history=existing_chat,
        llm_history=existing_llm,
        owner=MESSAGE_HUB_OWNER,
    )
    set_update_flag(session_id)




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
