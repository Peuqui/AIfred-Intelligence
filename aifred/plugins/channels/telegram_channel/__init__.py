"""Telegram Channel Plugin — Bot API listener + reply.

Drop-in plugin for the Message Hub channel system.
Listens for messages via Telegram Bot API (long polling) and
sends replies back. Credentials via credential broker.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ....lib.plugin_base import BaseChannel, CredentialField, load_tool_description
from ....lib.credential_broker import broker
from ....lib.logging_utils import log_message
from ....lib.text_chunking import split_message

if TYPE_CHECKING:
    from telegram import Update

    from ....lib.envelope import InboundMessage, OutboundMessage
    from ....lib.function_calling import Tool
    from ....lib.plugin_base import PluginContext

# Telegram message length limit
_MAX_MESSAGE_LENGTH = 4096

# ── /clear message log (TD6) ─────────────────────────────────
# The Bot API has no way to LIST a chat's history — a bot can only delete
# messages whose ids it knows. So we track every message id we see or send,
# and /clear bulk-deletes them (deleteMessages skips ids it can't delete,
# e.g. older than Telegram's hard 48h limit). Persisted as JSON so a worker
# restart doesn't orphan the log; a small lock guards the read-modify-write
# because the PTB handler loop and the hub's send_reply run concurrently.
_MSGLOG_CAP = 500  # per chat — older ids are past the 48h limit anyway
_msglog_lock = threading.Lock()
_MSGLOG_FILE: "pathlib.Path | None" = None


def _msglog_file() -> pathlib.Path:
    global _MSGLOG_FILE
    if _MSGLOG_FILE is None:
        from ....lib.config import DATA_DIR
        _MSGLOG_FILE = DATA_DIR / "message_hub" / "telegram_msglog.json"
        _MSGLOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    return _MSGLOG_FILE


def _msglog_load() -> dict[str, list[int]]:
    try:
        data = json.loads(_msglog_file().read_text())
    except FileNotFoundError:
        return {}  # First run — normal
    except (OSError, json.JSONDecodeError) as exc:
        # Korruptes Log = /clear kann getrackte Nachrichten nicht mehr
        # löschen — sichtbar machen statt still zu verwerfen.
        log_message(f"Telegram Plugin: corrupt msglog, starting fresh: {exc}", "warning")
        return {}
    return data if isinstance(data, dict) else {}


def _msglog_add(chat_id: "str | int", *message_ids: int) -> None:
    """Track message ids for a chat (bounded to _MSGLOG_CAP per chat)."""
    if not message_ids:
        return
    with _msglog_lock:
        log = _msglog_load()
        ids = log.setdefault(str(chat_id), [])
        ids.extend(int(m) for m in message_ids)
        del ids[:-_MSGLOG_CAP]
        _msglog_file().write_text(json.dumps(log))


def _msglog_take(chat_id: "str | int") -> list[int]:
    """Return and remove all tracked ids for a chat."""
    with _msglog_lock:
        log = _msglog_load()
        ids = log.pop(str(chat_id), [])
        _msglog_file().write_text(json.dumps(log))
        return ids


class TelegramChannel(BaseChannel):
    """Telegram channel via Bot API (long polling)."""

    # ── Identity ──────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "telegram"

    @property
    def display_name(self) -> str:
        return "Telegram"

    @property
    def description(self) -> str:
        return "Telegram-Bot mit Long-Polling — Chat-Nachrichten von Usern in der Allowlist."

    @property
    def icon(self) -> str:
        return "send"  # Lucide icon

    @property
    def always_reply(self) -> bool:
        return True

    # ── Credentials ───────────────────────────────────────────

    @property
    def credential_fields(self) -> list[CredentialField]:
        return [
            CredentialField(
                env_key="TELEGRAM_BOT_TOKEN",
                label_key="telegram_cred_bot_token",
                is_password=True,
                width_ratio=3,
            ),
            # Bewusst OHNE placeholder: der würde zum gespeicherten Default
            # (__post_init__) — Beispiel-IDs beim ungeänderten Speichern
            # wären echte Allowlist-Einträge (erster = Owner!). Beispiel
            # steht im Tooltip (i18n.json).
            CredentialField(
                env_key="TELEGRAM_ALLOWED_USERS",
                label_key="telegram_cred_allowed_users",
                width_ratio=2,
            ),
        ]

    def is_configured(self) -> bool:
        return (
            broker.get("telegram", "enabled").lower() == "true"
            and broker.is_set("telegram", "bot_token")
        )

    def apply_credentials(self, values: dict[str, str]) -> None:
        """Update runtime credentials via the broker."""
        broker.set_runtime("telegram", "enabled", "true")
        token = values.get("TELEGRAM_BOT_TOKEN", "")
        if token:
            broker.set_runtime("telegram", "bot_token", token)
        allowed = values.get("TELEGRAM_ALLOWED_USERS", "")
        broker.set_runtime("telegram", "allowed_users", allowed)

    # ── Listener ──────────────────────────────────────────────

    async def listener_loop(self) -> None:
        """Telegram bot loop — long polling until cancelled."""
        from telegram.error import InvalidToken
        from telegram.ext import (
            Application,
            CommandHandler,
            MessageHandler,
            filters,
        )

        if not self.is_configured():
            self.channel_log("Telegram Plugin: not configured, not starting", "warning")
            return

        token = broker.get("telegram", "bot_token")
        _log = self.channel_log  # Capture for use in inner functions
        _log("Telegram Plugin: starting bot...")

        app = Application.builder().token(token).build()

        # /clear command — clear is clear (TD6): delete everything that CAN
        # be deleted. Resets AIfred's conversation (route) AND bulk-deletes
        # all tracked chat messages. Telegram's hard limits stay: only ids
        # we tracked, only messages younger than 48h, in groups only with
        # delete permission — deleteMessages silently skips the rest.
        async def _cmd_clear(update: Update, context: object) -> None:
            user = update.effective_user
            chat = update.effective_chat
            msg = update.message
            # Channel-Posts/Edits haben keinen User/keine Message — ignorieren.
            if user is None or chat is None or msg is None:
                return
            if not _is_user_allowed(user.id):
                return
            chat_id = str(chat.id)
            from ....lib.routing_table import routing_table
            routing_table.delete_route("telegram", chat_id)

            ids = _msglog_take(chat_id)
            ids.append(msg.message_id)  # the /clear command message itself
            bot = msg.get_bot()
            deleted = 0
            for i in range(0, len(ids), 100):  # API cap: 100 ids per call
                batch = ids[i:i + 100]
                try:
                    await bot.delete_messages(chat_id=chat.id, message_ids=batch)
                    deleted += len(batch)
                except Exception as exc:
                    _log(f"Telegram Plugin: /clear delete batch failed: {exc}")

            conf = await bot.send_message(
                chat_id=chat.id,
                # "up to": deleteMessages überspringt still, was es nicht
                # löschen darf (>48h, fehlende Rechte) — der Zähler ist eine
                # Obergrenze, kein Ist-Wert.
                text=f"Conversation cleared — context reset, up to {deleted} tracked messages deleted.",
            )
            # Track the confirmation too, so the NEXT /clear removes it.
            _msglog_add(chat_id, conf.message_id)
            _log(f"Telegram Plugin: /clear by user {user.id} — {deleted} messages deleted")

        # Message handler
        async def _on_message(update: Update, context: object) -> None:
            if not update.message or not update.message.text:
                return
            user = update.effective_user
            if user is None:
                return
            if not _is_user_allowed(user.id):
                _log(f"Telegram Plugin: blocked message from {user.id} (not in whitelist)")
                return

            inbound = _build_inbound(update)
            # TD6: track the incoming message id so /clear can delete it.
            if update.effective_chat is not None:
                _msglog_add(update.effective_chat.id, update.message.message_id)
            _log(f"Telegram Plugin: message from {user.first_name} ({user.id})")
            from ....lib.message_processor import dispatch_inbound
            await dispatch_inbound(inbound, "Telegram Plugin")

        app.add_handler(CommandHandler("clear", _cmd_clear))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))

        # Application.builder() erstellt immer einen Updater — reine Typverengung.
        assert app.updater is not None
        try:
            await app.initialize()
            await app.start()
            # TD9: catch up on messages received while AIfred was down —
            # the typical downtime here is a short dev/deploy restart, and a
            # silently dropped message is worse than a late answer. Telegram
            # buffers pending updates for max 24 h, and the sender allowlist
            # (TD8) caps who can queue anything at all.
            await app.updater.start_polling(drop_pending_updates=False)
            _log("Telegram Plugin: bot started, polling for messages (catching up on pending updates)")

            # Keep alive until cancelled
            while True:
                await asyncio.sleep(1)

        except asyncio.CancelledError:
            _log("Telegram Plugin: shutting down")
        except InvalidToken as exc:
            # Config-Fehler: ein Restart-Loop kann einen falschen Token nicht
            # heilen — loggen und OHNE Re-Raise beenden (kein Hub-Restart),
            # gleiches Muster wie discord.LoginFailure im Discord-Channel.
            _log(f"Telegram Plugin: invalid bot token — fix credentials, not restarting ({exc})", "error")
        finally:
            # Nur stoppen, was auch läuft: ein unconditional stop() nach einem
            # Boot-Fehler wirft "This Updater is not running!" und ersetzt im
            # Hub-Log die eigentliche Fehlerursache. shutdown() ist bei nicht
            # initialisierter App ein No-Op.
            if app.updater.running:
                await app.updater.stop()
            if app.running:
                await app.stop()
            await app.shutdown()

    # ── Reply ─────────────────────────────────────────────────

    def format_outbound(self, text: str) -> dict[str, str]:
        """Strip Markdown markers — Telegram's legacy Markdown is brittle
        (escaping minefield), MarkdownV2 even more so. Sending plain text
        without ``parse_mode`` is robust: bold/italic markers are removed,
        but tables/lists/links remain readable as plain text.
        """
        from ....lib.markdown_render import md_to_plain
        return {"text": md_to_plain(text)}

    async def _deliver(self, bot, chat_id: int, text: str, media: "str | None") -> None:
        """SSOT for the actual Telegram send: attachment+caption when ``media``
        is set (photo for images, document otherwise), else chunked text. Used
        by both the reply path and the telegram_send tool so attachment
        delivery + caption overflow (TD7) + msglog tracking (TD6) live in
        exactly one place. ``text`` is already formatted by the caller."""
        from ....lib.vision_utils import is_image_file

        from ....lib.vision_utils import local_media_path
        local = local_media_path(media)
        url = _photo_url(media)
        if media and not local and not url:
            # Kein stiller Attachment-Drop: der Pfad existiert nicht (mehr) —
            # Text wird trotzdem zugestellt, aber der Verlust steht im Log.
            self.channel_log(
                f"Telegram Plugin: attachment path missing — sending text only ({media})",
                "warning",
            )
        sent_ids: list[int] = []
        async with bot:
            if local or url:
                # Telegram hard-caps photo/document captions at 1024 chars.
                # Send the overflow as follow-up text messages instead of
                # silently dropping it (TD7).
                caption, overflow = text[:1024], text[1024:]
                if local:
                    is_img = is_image_file(pathlib.Path(local))
                    with open(local, "rb") as fh:
                        if is_img:
                            m = await bot.send_photo(chat_id=chat_id, photo=fh, caption=caption)
                        else:
                            m = await bot.send_document(chat_id=chat_id, document=fh, caption=caption)
                        sent_ids.append(m.message_id)
                elif url:
                    # Remote URL (reply path only) — Telegram fetches it; treat
                    # as photo (the reply path only ever sets image URLs).
                    m = await bot.send_photo(chat_id=chat_id, photo=url, caption=caption)
                    sent_ids.append(m.message_id)
                if overflow:
                    for chunk in split_message(overflow, _MAX_MESSAGE_LENGTH):
                        m = await bot.send_message(chat_id=chat_id, text=chunk)
                        sent_ids.append(m.message_id)
            else:
                for chunk in split_message(text, _MAX_MESSAGE_LENGTH):
                    m = await bot.send_message(chat_id=chat_id, text=chunk)
                    sent_ids.append(m.message_id)
        # TD6: track sent ids so /clear can delete our own sends too.
        _msglog_add(chat_id, *sent_ids)

    async def send_reply(self, outbound: "OutboundMessage", original: "InboundMessage") -> None:
        """Send a reply via Telegram Bot API. If ``outbound.media`` is set
        (local path or URL), send it as a photo with the text as caption;
        otherwise plain text."""
        from telegram import Bot

        token = broker.get("telegram", "bot_token")
        bot = Bot(token)

        chat_id = int(outbound.channel_id or original.channel_id)
        text = self.format_outbound(outbound.text)["text"]
        await self._deliver(bot, chat_id, text, outbound.media)

        from ....lib.debug_bus import debug
        debug(f"Reply sent to Telegram chat {chat_id}")

    # ── Context ───────────────────────────────────────────────

    def build_context(self, message: "InboundMessage") -> str:
        """Prepare Telegram message for LLM with sender context."""
        from ....lib.prompt_loader import load_prompt
        return load_prompt(
            "shared/channel_telegram",
            sender=message.sender,
            text=message.text,
        )

    # ── Tools ─────────────────────────────────────────────────

    def get_tools(self, ctx: "PluginContext") -> list["Tool"]:
        """Provide telegram_send tool for LLM function calling."""
        from ....lib.function_calling import Tool
        from ....lib.security import TIER_COMMUNICATE, sanitize_outbound

        async def _execute_telegram_send(message: str, chat_id: str = "", attachment: str = "") -> str:
            from telegram import Bot

            token = broker.get("telegram", "bot_token")
            if not token:
                return json.dumps({"error": "Telegram not configured"})

            if not chat_id:
                # Default to the owner (first allowlist entry) — "send me
                # this via Telegram" must work without the model knowing a
                # chat id (mirrors discord_send's default-channel behavior).
                owner = _owner_chat_id()
                if owner is None:
                    return json.dumps({"error": (
                        "No chat_id provided and no owner configured "
                        "(allowed_users allowlist is empty)"
                    )})
                target = owner
            else:
                try:
                    target = int(chat_id)
                except (TypeError, ValueError):
                    return json.dumps({"error": f"Invalid chat_id: {chat_id!r}"})

            # Recipient allowlist gate: only the browser (user present) may send
            # to a chat that is not on the allowlist. From an external channel an
            # injected prompt could otherwise exfiltrate the conversation to an
            # attacker's chat_id.
            if ctx.source != "browser" and not _is_user_allowed(target):
                return json.dumps({
                    "error": (
                        "refused: target chat is not on the allowlist "
                        "(external-channel exfiltration guard). Do this from the web UI."
                    )
                })

            # Optional attachment. Resolved via the cross-channel SSOT
            # (session-isolated, path-traversal safe, size-capped); the
            # allowlist gate above is the exfiltration guard.
            media: str | None = None
            if attachment:
                from ....lib.vision_utils import resolve_outbound_attachment
                path, err = resolve_outbound_attachment(attachment, ctx.session_id, ctx.source)
                if err:
                    return json.dumps({"error": err})
                media = str(path)

            bot = Bot(token)
            # Same rendering as the reply path (SSOT): strip Markdown and send
            # plain text WITHOUT parse_mode. sanitize_outbound first: redact
            # secrets / block image-exfil URLs on the tool path.
            text = self.format_outbound(sanitize_outbound(message))["text"]
            await self._deliver(bot, target, text, media)

            # target, nicht chat_id: beim Owner-Default ist chat_id leer —
            # Log und Tool-Result müssen das echte Ziel nennen.
            log_message(f"Telegram Plugin: message sent to chat {target}")
            return json.dumps({"success": True, "chat_id": str(target), "attachment_sent": bool(media)})

        return [
            Tool(
                name="telegram_send",
                tier=TIER_COMMUNICATE,
                description=load_tool_description(__file__, "telegram_send"),
                parameters={
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "The message text to send",
                        },
                        "chat_id": {
                            "type": "string",
                            "description": (
                                "Optional: Telegram chat ID to send to. Leave EMPTY to send "
                                "to the user/owner — you do NOT need to know their chat id."
                            ),
                        },
                        "attachment": {
                            "type": "string",
                            "description": (
                                "Optional: URL of a file from THIS conversation to attach "
                                "(an uploaded image, or generated sandbox output like a PDF — "
                                "its /_upload/... URL). Images send as photo, other files as "
                                "document. The message text becomes the caption."
                            ),
                        },
                    },
                    "required": ["message"],
                },
                executor=_execute_telegram_send,
            ),
        ]

# ── Helpers ──────────────────────────────────────────────────

def _photo_url(media: "str | None") -> "str | None":
    """``media`` if it is an http(s) URL Telegram can fetch, else None."""
    return media if media and media.startswith(("http://", "https://")) else None


def _owner_chat_id() -> "int | None":
    """The owner's chat id — Konvention lebt in der lib-SSOT
    security.first_allowlist_entry. Default target for telegram_send when
    the model doesn't know a chat id ("schick mir das per Telegram").
    None if the allowlist is empty/unusable."""
    from ....lib.security import first_allowlist_entry
    first = first_allowlist_entry("telegram", "allowed_users")
    return int(first) if first.isdigit() else None


def _is_user_allowed(user_id: int) -> bool:
    """Telegram-Sender-Allowlist — Logik lebt als lib-SSOT in
    ``security.is_sender_allowed`` (geteilt mit discord): leer = niemand
    (fail-closed), '*' seit TD8 geblockt."""
    from ....lib.security import is_sender_allowed
    return is_sender_allowed("telegram", "allowed_users", user_id)


def _build_inbound(update: "Update") -> "InboundMessage":
    """Convert a Telegram Update to InboundMessage."""
    from ....lib.envelope import InboundMessage

    user = update.effective_user
    chat = update.effective_chat
    msg = update.message
    # Der Message-Handler filtert Updates ohne User/Message bereits vorab.
    if user is None or chat is None or msg is None:
        raise ValueError("Telegram update without user/chat/message")

    sender = user.first_name or str(user.id)
    if user.last_name:
        sender = f"{sender} {user.last_name}"

    return InboundMessage(
        channel="telegram",
        channel_id=str(chat.id),
        sender=sender,
        text=msg.text or "",
        timestamp=msg.date or datetime.now(timezone.utc),
        metadata={
            "user_id": user.id,
            "username": user.username or "",
            "chat_type": chat.type,
        },
    )


# Module-level instance — discovered by registry
TelegramChannel_instance = TelegramChannel()
