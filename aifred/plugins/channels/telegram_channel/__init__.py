"""Telegram Channel Plugin — Bot API listener + reply.

Drop-in plugin for the Message Hub channel system.
Listens for messages via Telegram Bot API (long polling) and
sends replies back. Credentials via credential broker.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ....lib.plugin_base import BaseChannel, CredentialField
from ....lib.credential_broker import broker
from ....lib.logging_utils import log_message

if TYPE_CHECKING:
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
        from telegram import Update
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
            if not _is_user_allowed(update.effective_user.id):
                return
            chat_id = str(update.effective_chat.id)
            from ....lib.routing_table import routing_table
            routing_table.delete_route("telegram", chat_id)
            await update.message.reply_text("Conversation cleared.")
            _log(f"Telegram Plugin: /clear by user {update.effective_user.id}")

        # Message handler
        async def _on_message(update: Update, context: object) -> None:
            if not update.message or not update.message.text:
                return
            user = update.effective_user
            if not _is_user_allowed(user.id):
                _log(f"Telegram Plugin: blocked message from {user.id} (not in whitelist)")
                return

            inbound = _build_inbound(update)
            _log(f"Telegram Plugin: message from {user.first_name} ({user.id})")
            await _dispatch_inbound(inbound)

        app.add_handler(CommandHandler("clear", _cmd_clear))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))

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

    async def send_reply(self, outbound: "OutboundMessage", original: "InboundMessage") -> None:
        """Send a text reply via Telegram Bot API."""
        from telegram import Bot

        token = broker.get("telegram", "bot_token")
        bot = Bot(token)

        chat_id = outbound.channel_id or original.channel_id
        text = outbound.text

        # Split long messages
        chunks = _split_message(text, _MAX_MESSAGE_LENGTH)
        async with bot:
            for chunk in chunks:
                await bot.send_message(
                    chat_id=int(chat_id),
                    text=chunk,
                    parse_mode="Markdown",
                )

        from ....lib.debug_bus import debug
        debug(f"Auto-reply sent to Telegram chat {chat_id}")

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
        from ....lib.security import TIER_COMMUNICATE
        import json

        async def _execute_telegram_send(message: str, chat_id: str = "") -> str:
            from telegram import Bot

            token = broker.get("telegram", "bot_token")
            if not token:
                return json.dumps({"error": "Telegram not configured"})

            if not chat_id:
                return json.dumps({"error": "No chat_id provided"})

            bot = Bot(token)
            chunks = _split_message(message, _MAX_MESSAGE_LENGTH)
            async with bot:
                for chunk in chunks:
                    await bot.send_message(
                        chat_id=int(chat_id),
                        text=chunk,
                        parse_mode="Markdown",
                    )

            log_message(f"Telegram Plugin: message sent to chat {chat_id}")
            return json.dumps({"success": True, "chat_id": chat_id})

        return [
            Tool(
                name="telegram_send",
                tier=TIER_COMMUNICATE,
                description="Send a message to a Telegram chat. Use this when the user asks to send a message via Telegram.",
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


def _build_inbound(update: object) -> "InboundMessage":
    """Convert a Telegram Update to InboundMessage."""
    from ....lib.envelope import InboundMessage

    user = update.effective_user
    chat = update.effective_chat
    msg = update.message

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
