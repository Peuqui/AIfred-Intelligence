"""Message Processor — bridge between the Message Hub and AIfred's turns.

Handles the complete flow for inbound messages (channels, scheduled jobs, alerts):
1. The sender's rights, identity and session (via routing table)
2. The turn (``turn_service.run_turn``, the same one the chat runs), with the message wrapped
   as an external one
3. The replies back to the channel (auto-reply)
"""

import contextvars
import secrets
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from .agent_config import BASE_AGENT
from .config import MESSAGE_HUB_OWNER
from .envelope import InboundMessage, OutboundMessage, ReplyPart
from .formatting import build_channel_chat_entry
from .logging_utils import log_exception, log_message
from .plugin_base import BaseChannel
from .routing_table import routing_table
from .session_storage import create_empty_session, update_chat_data
from .turn_service import TurnOrigin, TurnReply, TurnRequest, run_turn

# Global notification file for Message Hub events (read by UI timer)
# The phases of a message of a channel (or a job) the open pages show as a hint: what came in from whom, that it is
# being answered, that it is done or failed; and that the session list has a new or changed session.
HUB_NOTIFICATION_KIND = "hub_notification"


def push_hub_notification(
    session_id: str, session_title: str, channel: str, sender: str,
    status: str = "received",
) -> None:
    """Show every open page the phase of a channel's message (``received`` | ``processing`` | ``done`` | ``error``).

    Goes to the stream of every session a page follows, the newest replacing the older ones (one hint, not a
    history); a later phase replaces the earlier one on the page too. Safe from the hub's thread (session_events
    hands a publish over to the server's loop).
    """
    from . import session_events

    notification = {
        "session_id": session_id,
        "session_title": session_title,
        "channel": channel,
        "sender": sender,
        "status": status,
    }
    for followed in session_events.sessions_with_subscribers():
        session_events.publish(followed, HUB_NOTIFICATION_KIND, notification, replaces=frozenset({HUB_NOTIFICATION_KIND}))


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

    def __init__(self, write_fn):
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
        push_hub_notification(session_id, session_title, channel, sender, status=status)

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


def _mapped_user_name(channel: str, channel_id: str, sender: str) -> str | None:
    """AIfred user name for an external identity (user_mapping.json), None if unmapped.

    Checks if the channel_id (e.g. telegram user ID, email address)
    is mapped to a known AIfred user.
    """
    import json

    from .config import DATA_DIR

    mapping_path = DATA_DIR / "user_mapping.json"
    if not mapping_path.exists():
        return None

    try:
        mappings = json.loads(mapping_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    for user_name, channels in mappings.items():
        ids = channels.get(channel, [])
        if channel_id in ids or sender in ids:
            return str(user_name)

    return None


def resolve_user_name(channel: str, channel_id: str, sender: str) -> str:
    """Mapped AIfred user name, or the original sender name if no mapping exists."""
    return _mapped_user_name(channel, channel_id, sender) or sender


def message_from_user(channel: str, channel_id: str, sender: str) -> bool:
    """SSOT: is this inbound message the user speaking (bubble on the right)?

    Yes for a mapped identity, and for channels whose speaker is always the user
    (BaseChannel.speaker_is_user). Everyone else — peers, jobs, alerts — is
    shown on the left under their own name.
    """
    from .plugin_registry import get_channel
    plugin = get_channel(channel)
    if plugin is not None and plugin.speaker_is_user:
        return True
    return _mapped_user_name(channel, channel_id, sender) is not None


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


async def process_inbound(message: InboundMessage) -> OutboundMessage | None:
    """Process an inbound message as a turn of its channel's session; the reply goes back through
    the channel when it answers automatically. None when no agent answered.

    Uses the Debug Bus (session_scope) so all debug() calls from any
    depth (tools, research, plugins) automatically go to the session.
    """
    # Kalibrier-Gate: Während einer GPU-Kalibrierung gehört das VRAM
    # exklusiv der Messung — eine Inferenz würde llama-swap neu starten
    # und die Messwerte verderben. Die Hub-Kanäle laufen an Reflex vorbei,
    # deshalb das prozessweite Flag statt is_calibrating (Session-State).
    # Höfliche Text-Absage statt stillem Verschlucken (der Rückgabewert
    # wird von den Aufrufern nur geloggt — gesendet wird hier). Ausnahme
    # freeecho2: eine Voice-Reply würde die TTS-Synthese anwerfen — genau
    # die GPU-Kollision, die das Gate verhindert; dort bleibt es beim Log.
    from .calibration_gate import is_calibration_active
    from .debug_bus import debug, session_scope
    from .plugin_registry import get_channel
    from .session_storage import get_session_title
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
            # No session: the gate answers before any session is resolved.
            metadata={"calibration_gate": True, "session_id": None},
        )
        gate_plugin = get_channel(message.channel)
        if gate_plugin is not None and message.channel != "freeecho2":
            if hasattr(gate_plugin, "build_reply_metadata"):
                gate_reply.metadata.update(gate_plugin.build_reply_metadata(message))
            try:
                await gate_plugin.send_reply(gate_reply, message)
                gate_reply.metadata["sent"] = True
            except Exception as e:  # noqa: BLE001 — channel plugins fail in their own ways; the gate holds anyway, logged
                log_exception(f"⚠️ Calibration-gate reply via {message.channel} failed", e)
        return gate_reply

    # 0. Determine security tier for this channel
    from .security import resolve_tier_for_sender
    max_tier = resolve_tier_for_sender(message.channel, message.sender, message.metadata)

    # 1. Sanitize inbound text (strip HTML, zero-width chars, normalize)
    from .security import sanitize_inbound
    message.text = sanitize_inbound(message.text)

    # 1b. Resolve external identity to AIfred user name
    original_sender = message.sender
    from_user = message_from_user(message.channel, message.channel_id, message.sender)
    resolved_name = resolve_user_name(message.channel, message.channel_id, message.sender)
    if resolved_name != message.sender:
        log_message(f"User mapping: {message.sender} -> {resolved_name}")
        message.sender = resolved_name

    # 2. Find or create the session via the routing table: the turn's debug lines go to it.
    # A reply to one specific bot message goes to that message's session (e.g.
    # the scheduler job that announced it); everything else to the chat's.
    from .routing_table import REPLY_TO_MESSAGE_KEY
    reply_key = message.metadata.get(REPLY_TO_MESSAGE_KEY)
    reply_route = routing_table.get_route(message.channel, reply_key) if reply_key else None
    route = reply_route or routing_table.get_route(message.channel, message.channel_id)
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

    plugin = get_channel(message.channel)
    channel_label = channel_display_label(message.channel)
    notification_title = get_session_title(session_id) or subject

    # hub_notification_scope writes "received" on entry and guarantees a final done/error on exit —
    # every return path, exceptions and cancellation included.
    with session_scope(session_id), hub_notification_scope(
        session_id, notification_title, channel_label, message.sender,
    ) as hub:
        debug(f"📨 {channel_label}: message from {message.sender}")
        if subject and subject != "?":
            debug(f"📧 Subject: {subject}")
        hub.update("processing")
        # The turn registers itself as the session's pipeline: a stop (FreeEcho.2's _stop, the
        # browser's button) cancels this task.
        outcome = await run_turn(TurnRequest(
            session_id, message.text, source=message.channel,
            origin=_turn_origin(message, plugin, channel_label, original_sender, from_user, max_tier),
        ))
        if outcome.agent is not None:
            message.target_agent = outcome.agent   # the channel speaks with its voice (FreeEcho.2)
        if outcome.status != "done" or not any(reply.text.strip() for reply in outcome.replies):
            log_message(f"{channel_label}: no reply (turn {outcome.status})", "warning")
            hub.fail()  # scope writes "error" on exit
            return None

        reply_metadata = plugin.build_reply_metadata(message) if plugin else {}
        # silent_reply: the audio player started playing — the channel skips its spoken
        # confirmation so the music starts without the butler talking over it.
        if any(reply.silent for reply in outcome.replies):
            reply_metadata["silent_reply"] = True
        # The session this reply belongs to: internal triggers (scheduler,
        # webhook) hand it to their delivery layer, threading channels (email)
        # register it so the next answer returns to this session.
        reply_metadata["session_id"] = session_id
        outbound = OutboundMessage(
            channel=message.channel,
            channel_id=message.channel_id,
            recipient=message.sender,
            text=outbound_text(outcome.replies),
            metadata=reply_metadata,
            parts=outbound_parts(outcome.replies, message.target_agent),
        )
        if plugin and _is_auto_reply_enabled(message.channel):
            await plugin.send_reply(outbound, message)
        else:
            debug("💬 Response ready (auto-reply off)")
    return outbound


def _turn_origin(
    message: InboundMessage, plugin: BaseChannel | None, channel_label: str, original_sender: str, from_user: bool,
    max_tier: int,
) -> TurnOrigin:
    """The turn's view of an inbound message: its bubble, what the model reads (wrapped in the
    external-message fence, which stays in the history for every later turn) and the sender's rights."""
    from .agent_config import get_agent_config
    from .debug_bus import debug
    from .security import resolve_trust_label, wrap_external_message

    if plugin:
        llm_context = plugin.build_context(message)
    else:
        sender_info = message.sender
        if original_sender != message.sender:
            sender_info = f"{message.sender} (via {original_sender})"
        llm_context = f"[{channel_label} from {sender_info}]\n\n{message.text}"
    # Trust label from the authenticated owner verdict (N5) — the raw sender string is forgeable
    # (email From), the mapped name is derived from it.
    trust = resolve_trust_label(message.channel, original_sender, message.metadata)
    # A wake word or a job names the agent; one not configured leaves the choice to the turn.
    wake_agent = message.metadata.get("wake_agent")
    pinned = wake_agent if wake_agent and get_agent_config(wake_agent) else None
    if wake_agent and pinned is None:
        debug(f"⚠️ Wake-Agent '{wake_agent}' not configured — the message decides")
    return TurnOrigin(
        chat_entry=build_inbound_chat_entry(message, from_user),
        llm_text=wrap_external_message(llm_context, message.sender, message.channel, trust),
        max_tier=max_tier,
        trust=trust,
        metadata=dict(message.metadata or {}),
        pinned_agent=pinned,
        task=message.task,
    )


def outbound_text(replies: Sequence[TurnReply]) -> str:
    """What goes back to the channel: every reply of the turn in order, cleaned for outside. A reply
    names its speaker when another agent than AIfred answered, or when several spoke (a debate)."""
    from .agent_config import agent_display_name
    from .security import sanitize_outbound

    named = len(replies) > 1 or replies[0].agent != BASE_AGENT
    parts = []
    for reply in replies:
        text = sanitize_outbound(reply.text)
        if named and reply.agent != "system":   # a mode switch's confirmation speaks for nobody
            text = f"— {agent_display_name(reply.agent)} —\n\n{text}"
        parts.append(text)
    return "\n\n".join(parts)


def outbound_parts(replies: Sequence[TurnReply], voice: str) -> list[ReplyPart]:
    """Who says what, for a channel that speaks: every reply with text in its speaker's voice and its bubble,
    cleaned for outside. A mode switch's confirmation speaks for nobody: ``voice`` reads it (the agent the
    message went to)."""
    from .security import sanitize_outbound

    return [
        ReplyPart(reply.agent if reply.agent != "system" else voice, sanitize_outbound(reply.text), reply.bubble)
        for reply in replies if reply.text.strip()
    ]


def channel_display_label(channel: str) -> str:
    """User-facing channel name in the UI language — push title, LLM context
    and the persisted "[Channel] Sender" chat header all use this."""
    from .plugin_base import plugin_display_name
    from .plugin_registry import get_channel as _get_ch
    from .settings import load_settings
    plugin = _get_ch(channel)
    if plugin is None:
        return channel.capitalize()
    return plugin_display_name(plugin, (load_settings() or {}).get("ui_language", "de"))


def channel_emoji(channel: str) -> str:
    """Bubble emoji of a channel: the plugin's own, else NON_PLUGIN_CHANNEL_EMOJIS."""
    from .config import NON_PLUGIN_CHANNEL_EMOJIS
    from .plugin_registry import get_channel as _get_ch
    plugin = _get_ch(channel)
    return plugin.emoji if plugin is not None else NON_PLUGIN_CHANNEL_EMOJIS[channel]


def build_user_chat_content(message: InboundMessage) -> str:
    """Build the chat_history content string for a user message.

    Single source of truth for the "[Channel] Sender → Agent" header format.
    Used by both the normal save path and FreeEcho.2's early flush. The arrow
    names the agent the channel addressed (wake word), if it is configured.
    """
    from .agent_config import get_agent_config, get_agent_label

    channel_label = channel_display_label(message.channel)
    subject = message.metadata.get("subject", "")
    header = f"[{channel_emoji(message.channel)} {channel_label}] {message.sender}"
    wake_agent = message.metadata.get("wake_agent")
    if wake_agent and get_agent_config(wake_agent):
        header += f" → {get_agent_label(wake_agent)}"
    if subject:
        header += f" — {subject}"
    return f"{header}\n\n{message.text}"


def build_inbound_chat_entry(message: InboundMessage, from_user: bool) -> dict:
    """chat_history entry of an inbound message: the user's own words on the right
    ("[Channel] Sender" header), everyone else on the left under their own name."""
    if from_user:
        return {"role": "user", "content": build_user_chat_content(message)}
    subject = message.metadata.get("subject", "")
    content = f"**{subject}**\n\n{message.text}" if subject else message.text
    return build_channel_chat_entry(content, message.channel, message.sender)


def save_inbound_to_session(session_id: str, message: InboundMessage, from_user: bool) -> None:
    """Save an inbound message to the session's CHAT history only (UI): a spoken exchange AIfred
    only routed on (record_routed_voice_turn), which writes its llm_history entry itself, wrapped in
    the external-message fence. A message an agent answers is saved by its turn (turn_service).
    """
    from .session_storage import load_session, session_rmw_lock

    # M4: load→append→save as ONE unit — the browser thread writes the
    # same file (see session_rmw_lock in session_storage).
    with session_rmw_lock:
        session = load_session(session_id)
        data = session.get("data", {}) if session else {}
        existing_chat = data.get("chat_history", [])

        existing_chat.append(build_inbound_chat_entry(message, from_user))

        # Browser detects via session file mtime-watch (SSOT)
        update_chat_data(
            session_id=session_id,
            chat_history=existing_chat,
            debug_messages=data.get("debug_messages", []),
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
    for channels in mappings.values():
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


def announces_in_rooms(channel: str) -> bool:
    """Whether an announcement on a channel is spoken in rooms rather than sent to
    someone: it then needs ``resolve_announce_targets`` and ``announce_into_rooms``."""
    return channel == "freeecho2"


def resolve_announce_targets(channel: str, target: str) -> list[str]:
    """Expandiert ein Sink-Ziel in konkrete Empfänger.

    Für ``freeecho2``: ``"*"`` → alle gerade verbundenen Rooms, ``"@gruppe"``
    → konfigurierte Rooms der Gruppe, sonst der einzelne (aufgelöste) Room.
    Andere Kanäle: immer genau ein aufgelöster Empfänger. So bleibt das
    Broadcast/Gruppen-Wissen serverseitig — die Firmware bleibt room-only."""
    if announces_in_rooms(channel):
        try:
            from ..plugins.channels.freeecho2_channel import _devices
        except ImportError:
            _devices = {}
        t = (target or "").strip()
        # Manche LLMs packen den Kanal-Präfix mit in den target-Param
        # ("freeecho2:wohnzimmer") — defensiv abstreifen.
        t = t.removeprefix("freeecho2:")
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
    session_id: str | None,
    media: str | None = None, metadata: dict | None = None,
) -> bool:
    """SSoT for sending an autonomous (non-reply) message to a channel.
    Resolves the recipient, builds an OutboundMessage (+ media) and a minimal
    inbound, and calls the channel's ``send_reply`` (the one delivery path).

    ``session_id`` is the session the message belongs to (the scheduler job's
    or the alert's), carried in the outbound metadata exactly like a reply's:
    a channel that threads replies (email) registers it, so an answer to the
    announcement lands in that same session instead of opening a new one.
    ``None`` when the message belongs to no session."""
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
    # An announcement speaks in AIfred's voice; its bubble is the newest one without audio (announce_into_rooms
    # records it right before).
    outbound = OutboundMessage(
        channel=channel, channel_id=target, recipient=target,
        text=text, media=media, metadata={**(metadata or {}), "session_id": session_id},
        parts=[ReplyPart(BASE_AGENT, text)],
    )
    dummy = InboundMessage(
        channel=channel, channel_id=target, sender="system",
        text="", timestamp=datetime.now(),
    )
    try:
        await plugin.send_reply(outbound, dummy)
        return True
    except Exception as e:  # noqa: BLE001 — channel plugins fail in their own ways; the caller sees False, logged
        log_exception(f"announce: send via '{channel}' failed", e)
        return False


def _session_for_channel(channel: str, channel_id: str, owner: str = MESSAGE_HUB_OWNER) -> str:
    """The session of a channel conversation: the routed one, or a fresh one that gets a
    route. SSoT for every autonomous or routed write into a channel's session."""
    route = routing_table.get_route(channel, channel_id)
    if route:
        return route.session_id
    session_id = secrets.token_hex(16)
    # Tag with the origin channel so the session's provenance stays
    # visible. Same contract as process_inbound: the browser's auto-load
    # picks up a fresh alert when it is the most recent session — that is
    # wanted, the user restarts to see what came in.
    create_empty_session(session_id, owner=owner, channel=channel)
    routing_table.set_route(channel, channel_id, session_id)
    return session_id


def record_routed_voice_turn(
    channel: str, channel_id: str, *, sender: str, text: str, route_name: str, outcome: str,
) -> str:
    """Documents a spoken exchange that AIfred only routed on (no LLM answer): the user's
    words and the outcome go into the channel's session — chat history AND llm_history, the
    same shapes as every other channel (user message wrapped in the external-message fence,
    the outcome as an autonomous event). Returns the session_id.
    """
    from datetime import datetime

    from .message_builder import stamp_user_turn, user_turn_stamp
    from .security import resolve_trust_label, wrap_external_message
    from .session_storage import load_session, session_rmw_lock

    session_id = _session_for_channel(channel, channel_id)
    # "[channel] sender — → route": the subject carries where the words went
    message = InboundMessage(
        channel=channel, channel_id=channel_id, sender=sender, text=text,
        timestamp=datetime.now(), metadata={"subject": f"→ {route_name}"},
    )
    save_inbound_to_session(
        session_id, message, message_from_user(channel, channel_id, sender),
    )

    trust = resolve_trust_label(channel, sender, message.metadata)
    wrapped = wrap_external_message(
        f"[{channel_display_label(channel)} from {sender}, routed to {route_name}]\n\n{text}",
        sender, channel, trust,
    )
    with session_rmw_lock:
        session = load_session(session_id)
        data = (session or {}).get("data", {})
        llm_history = [
            *data.get("llm_history", []),
            {"role": "user", "content": stamp_user_turn(wrapped, user_turn_stamp())},
        ]
        update_chat_data(
            session_id, list(data.get("chat_history", [])),
            llm_history=llm_history, owner=MESSAGE_HUB_OWNER,
        )
    record_autonomous_turn(channel, channel_id, route_name, outcome)
    return session_id


def record_autonomous_turn(
    channel: str, channel_id: str, title: str, text: str, *,
    media: str | None = None,
    media_gallery: list[str] | None = None,
    owner: str = MESSAGE_HUB_OWNER,
    speaker: str | None = None,
) -> str:
    """SSoT for surfacing an autonomous event as a normal browser session.
    Routes to a (stable) session, appends an assistant chat turn, and writes a
    hub notification — same primitives process_inbound persists with, just
    without the LLM. ``media`` (an on-disk frame path) is embedded as a
    Markdown image so the browser session shows exactly what a channel like
    Telegram received. Returns the session_id.

    ``media_gallery`` ist die SSoT für die Bubble, sobald der Aufrufer sie
    mitgibt — auch als LEERE Liste. Die heißt „zu dieser Meldung gibt es
    nichts Neues zu zeigen" (die Bilanz eines Vorkommnisses filtert bereits
    Gezeigtes heraus) und darf nicht auf ``media`` zurückfallen, sonst stünde
    genau das wiederholte Bild wieder in der Bubble. ``None`` = der Aufrufer
    kennt keine Galerie, dann trägt ``media`` das Bild.

    ``speaker``: ein anderer Agent spricht (Name) — das Modell sieht dann eine
    „Nachricht von <speaker>“ statt einer automatischen Meldung. Die Bubble
    zeigt den Absender (``title``) in der Kopfzeile, der Text bleibt ohne Namen."""
    from .session_storage import load_session

    session_id = _session_for_channel(channel, channel_id, owner)

    content = text
    image_urls: list[str] = []
    if media_gallery is not None:
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
        image_urls = frames + crops
    elif media:
        from pathlib import Path

        from .vision_utils import get_image_url
        url = get_image_url(Path(media))
        if url:
            content = f"{text}\n\n![{title}]({url})"
            image_urls = [url]

    # The model sees llm_history, not the bubble: without this entry it knows
    # nothing of the event and denies that any image was ever shown.
    from .message_builder import build_autonomous_history_entry
    llm_entry = build_autonomous_history_entry(channel, text, image_urls, speaker)

    # M4: load→append→save as ONE unit (see session_rmw_lock).
    from .session_storage import session_rmw_lock
    with session_rmw_lock:
        session = load_session(session_id)
        data = (session or {}).get("data", {})
        chat_history = list(data.get("chat_history", []))
        chat_history.append(build_channel_chat_entry(content, channel, title))
        llm_history = [*data.get("llm_history", []), llm_entry]
        update_chat_data(session_id, chat_history, llm_history=llm_history, owner=owner)

    push_hub_notification(session_id, title, channel, "system", status="done")
    return session_id


async def announce_into_rooms(
    channel: str, rooms: list[str], text: str, speaker: str, metadata: dict,
) -> list[str]:
    """SSoT for an announcement someone makes out loud in rooms (an agent's
    ``freeecho2_announce``, the announce API): per room the bubble first —
    ``record_autonomous_turn`` in the room's session, ``speaker`` as sender —
    then the announcement into that session, so what the puck speaks is kept
    on that bubble (speech_synthesis.keep_spoken_audio). Recorded after, a
    short announcement would be spoken before its bubble existed.

    Returns the rooms reached. A room not reached keeps its bubble unspoken;
    that is logged, not hidden."""
    reached: list[str] = []
    for room in rooms:
        session_id = record_autonomous_turn(channel, room, speaker, text, speaker=speaker)
        if await announce_to_channel(channel, room, text, session_id=session_id, metadata=metadata):
            reached.append(room)
        else:
            log_message(f"Announcement by {speaker}: {channel}:{room} not reached — its bubble was not spoken", "warning")
    return reached
