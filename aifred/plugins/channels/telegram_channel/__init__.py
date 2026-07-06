"""Telegram Channel Plugin — Bot API listener + reply.

Drop-in plugin for the Message Hub channel system.
Listens for messages via Telegram Bot API (long polling) and
sends replies back. Credentials via credential broker.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ....lib.plugin_base import BaseChannel, CredentialField, load_tool_description
from ....lib.credential_broker import broker
from ....lib.logging_utils import log_message

if TYPE_CHECKING:
    from telegram import Update

    from ....lib.envelope import InboundMessage, OutboundMessage
    from ....lib.function_calling import Tool
    from ....lib.plugin_base import PluginContext

# Telegram message length limit
_MAX_MESSAGE_LENGTH = 4096


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
            CredentialField(
                env_key="TELEGRAM_ALLOWED_USERS",
                label_key="telegram_cred_allowed_users",
                placeholder="123456789, 987654321",
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

        # /clear command — reset conversation
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
            await msg.reply_text("Conversation cleared.")
            _log(f"Telegram Plugin: /clear by user {user.id}")

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
            _log(f"Telegram Plugin: message from {user.first_name} ({user.id})")
            await _dispatch_inbound(inbound)

        app.add_handler(CommandHandler("clear", _cmd_clear))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))

        # Application.builder() erstellt immer einen Updater — reine Typverengung.
        assert app.updater is not None
        try:
            await app.initialize()
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            _log("Telegram Plugin: bot started, polling for messages")

            # Keep alive until cancelled
            while True:
                await asyncio.sleep(1)

        except asyncio.CancelledError:
            _log("Telegram Plugin: shutting down")
        finally:
            await app.updater.stop()
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

    async def send_reply(self, outbound: "OutboundMessage", original: "InboundMessage") -> None:
        """Send a reply via Telegram Bot API. If ``outbound.media`` is set
        (local path or URL), send it as a photo with the text as caption;
        otherwise plain text."""
        from telegram import Bot

        token = broker.get("telegram", "bot_token")
        bot = Bot(token)

        chat_id = outbound.channel_id or original.channel_id
        text = self.format_outbound(outbound.text)["text"]
        local = _local_photo_path(outbound.media)
        url = _photo_url(outbound.media)

        async with bot:
            if local or url:
                # Telegram hard-caps photo captions at 1024 chars. Send the
                # overflow as follow-up text messages instead of silently
                # dropping it (TD7).
                caption, overflow = text[:1024], text[1024:]
                if local:
                    with open(local, "rb") as fh:
                        await bot.send_photo(chat_id=int(chat_id), photo=fh, caption=caption)
                elif url:
                    await bot.send_photo(chat_id=int(chat_id), photo=url, caption=caption)
                if overflow:
                    for chunk in _split_message(overflow, _MAX_MESSAGE_LENGTH):
                        await bot.send_message(chat_id=int(chat_id), text=chunk)
            else:
                for chunk in _split_message(text, _MAX_MESSAGE_LENGTH):
                    await bot.send_message(chat_id=int(chat_id), text=chunk)

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
        import json

        async def _execute_telegram_send(message: str, chat_id: str = "") -> str:
            from telegram import Bot

            token = broker.get("telegram", "bot_token")
            if not token:
                return json.dumps({"error": "Telegram not configured"})

            if not chat_id:
                return json.dumps({"error": "No chat_id provided"})

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

            bot = Bot(token)
            # Same rendering as the reply path (SSOT): strip Markdown and send
            # plain text WITHOUT parse_mode. Telegram's legacy Markdown parser
            # rejects unbalanced markers with HTTP 400, and a chunk split can
            # cut an entity mid-token — both make the send fail on normal output.
            # sanitize_outbound first: redact secrets / block image-exfil URLs on
            # the tool path (the reply path already runs it, this one did not).
            text = self.format_outbound(sanitize_outbound(message))["text"]
            chunks = _split_message(text, _MAX_MESSAGE_LENGTH)
            async with bot:
                for chunk in chunks:
                    await bot.send_message(chat_id=target, text=chunk)

            log_message(f"Telegram Plugin: message sent to chat {chat_id}")
            return json.dumps({"success": True, "chat_id": chat_id})

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
                            "description": "Telegram chat ID to send to",
                        },
                    },
                    "required": ["message", "chat_id"],
                },
                executor=_execute_telegram_send,
            ),
        ]

    def build_reply_metadata(self, message: "InboundMessage") -> dict:
        return {}


# ── Helpers ──────────────────────────────────────────────────

def _local_photo_path(media: "str | None") -> "str | None":
    """Local file path if ``media`` points at an existing file, else None."""
    if not media or media.startswith(("http://", "https://")):
        return None
    from pathlib import Path
    return media if Path(media).exists() else None


def _photo_url(media: "str | None") -> "str | None":
    """``media`` if it is an http(s) URL Telegram can fetch, else None."""
    return media if media and media.startswith(("http://", "https://")) else None


def _is_user_allowed(user_id: int) -> bool:
    """Check if a Telegram user ID is in the whitelist.

    Empty whitelist = nobody allowed (safe default).
    "*" = everyone allowed.
    """
    whitelist_raw = broker.get("telegram", "allowed_users").strip()

    if not whitelist_raw:
        return False
    if whitelist_raw == "*":
        return True

    allowed_ids = set()
    for entry in whitelist_raw.split(","):
        entry = entry.strip()
        if entry.isdigit():
            allowed_ids.add(int(entry))
    return user_id in allowed_ids


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


def _split_message(text: str, max_length: int) -> list[str]:
    """Split a message into chunks that fit Telegram's limit."""
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        # Split at last newline before limit
        split_at = text.rfind("\n", 0, max_length)
        if split_at == -1:
            split_at = max_length
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


async def _dispatch_inbound(message: "InboundMessage") -> None:
    """Hand an inbound message to the message processor."""
    from ....lib.message_processor import process_inbound
    await process_inbound(message)


# Module-level instance — discovered by registry
TelegramChannel_instance = TelegramChannel()
