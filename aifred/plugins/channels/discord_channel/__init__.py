"""Discord Channel Plugin — Bot listener + reply via discord.py.

Drop-in plugin for the Message Hub channel system.
Connects as a Discord bot and listens for messages in configured channels.
"""

from __future__ import annotations

import asyncio
from datetime import timezone
from typing import TYPE_CHECKING

import discord

from ....lib.plugin_base import BaseChannel, CredentialField, load_tool_description
from ....lib.logging_utils import log_message

if TYPE_CHECKING:
    from ....lib.envelope import InboundMessage, OutboundMessage
    from ....lib.function_calling import Tool
    from ....lib.plugin_base import PluginContext

# Module-level reference to the running Discord client.
# Needed so the reply path can send messages back.
_discord_client: discord.Client | None = None


def _parse_channel_ids(ids_str: str) -> set[int]:
    """Parse comma-separated channel IDs from config string."""
    if not ids_str:
        return set()
    ids: set[int] = set()
    for part in ids_str.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


def _local_file_path(media: "str | None") -> "str | None":
    """Local file path if ``media`` points at an existing file, else None.
    Discord attaches via discord.File, which needs a local path (it cannot
    fetch a remote URL like Telegram's send_photo can)."""
    from pathlib import Path
    if not media or media.startswith(("http://", "https://")):
        return None
    return media if Path(media).exists() else None


def _is_discord_user_allowed(user_id: int) -> bool:
    """Check a Discord user ID against the allowlist (same model as Telegram).

    Empty allowlist = nobody (safe default — Discord had NO sender filter before,
    so DMs let any user drive the agent). TD8: the "*" wildcard is NOT
    supported (anymore) — same decision as Telegram; a world-open bot lets
    anyone on a shared server burn GPU inference. Explicit numeric IDs only;
    blocked senders' ids appear in the log for easy onboarding.
    """
    from ....lib.credential_broker import broker
    raw = broker.get("discord", "allowed_users").strip()
    if not raw:
        return False
    if raw == "*":
        log_message(
            "Discord Plugin: '*' wildcard in allowed_users is no longer "
            "supported (TD8) — list explicit user ids. Blocking everyone."
        )
        return False
    return any(p.strip().isdigit() and int(p.strip()) == user_id for p in raw.split(","))


class DiscordChannel(BaseChannel):
    """Discord channel via discord.py bot."""

    # ── Identity ──────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "discord"

    @property
    def display_name(self) -> str:
        return "Discord"

    @property
    def description(self) -> str:
        return "Discord-Bot für Server-Kanäle — Watching, Auto-Reply und Slash-Commands."

    @property
    def icon(self) -> str:
        return "message-circle"

    @property
    def always_reply(self) -> bool:
        return True

    # ── Credentials ───────────────────────────────────────────

    @property
    def credential_fields(self) -> list[CredentialField]:
        return [
            CredentialField(
                env_key="DISCORD_BOT_TOKEN",
                label_key="discord_cred_bot_token",
                placeholder="MTIzNDU2Nzg5...",
                is_password=True,
            ),
            CredentialField(
                env_key="DISCORD_CHANNEL_IDS",
                label_key="discord_cred_channel_ids",
                placeholder="123456789,987654321",
            ),
            CredentialField(
                env_key="DISCORD_ALLOWED_USERS",
                label_key="discord_cred_allowed_users",
                placeholder="123456789012345678, * für alle",
            ),
        ]

    def is_configured(self) -> bool:
        from ....lib.credential_broker import broker
        return (
            broker.get("discord", "enabled").lower() == "true"
            and broker.is_set("discord", "bot_token")
        )

    def apply_credentials(self, values: dict[str, str]) -> None:
        """Update runtime credentials via the broker."""
        from ....lib.credential_broker import broker

        broker.set_runtime("discord", "enabled", "true")

        token = values.get("DISCORD_BOT_TOKEN", "")
        if token:
            broker.set_runtime("discord", "bot_token", token)

        channel_ids = values.get("DISCORD_CHANNEL_IDS", "")
        broker.set_runtime("discord", "channel_ids", channel_ids)

        allowed_users = values.get("DISCORD_ALLOWED_USERS", "")
        broker.set_runtime("discord", "allowed_users", allowed_users)

    # ── Listener ──────────────────────────────────────────────

    async def listener_loop(self) -> None:
        """Discord bot loop — runs until cancelled."""
        global _discord_client

        from ....lib.credential_broker import broker

        if not self.is_configured():
            self.channel_log("Discord Plugin: not configured, not starting", "warning")
            return

        bot_token = broker.get("discord", "bot_token")
        allowed_channels = _parse_channel_ids(broker.get("discord", "channel_ids"))
        _log = self.channel_log  # Capture for use in inner functions

        _log("Discord Plugin: starting bot...")
        if allowed_channels:
            _log(f"Discord Plugin: watching channels {allowed_channels}")
        else:
            _log("Discord Plugin: no channel IDs configured, listening on all channels")

        intents = discord.Intents.default()
        intents.message_content = True
        intents.dm_messages = True

        client = discord.Client(intents=intents)
        tree = discord.app_commands.CommandTree(client)
        _discord_client = client

        @tree.command(name="clear", description="Reset the conversation and delete all messages in this channel")
        async def slash_clear(interaction: discord.Interaction) -> None:
            # TD6: clear is clear — delete everything that CAN be deleted.
            # Always resets AIfred's conversation (route); in server channels
            # additionally purges all messages (needs manage_messages). In
            # DMs Discord bots cannot bulk-delete → context reset only.
            if not interaction.channel:
                await interaction.response.send_message("No channel context.", ephemeral=True)
                return
            from ....lib.routing_table import routing_table
            routing_table.delete_route("discord", str(interaction.channel.id))
            if not interaction.guild:
                await interaction.response.send_message(
                    "Conversation context reset. (Messages in DMs can't be bulk-deleted by bots.)",
                    ephemeral=True,
                )
                _log("Discord Plugin: /clear in DM — context reset only")
                return
            perms = interaction.channel.permissions_for(interaction.user)  # type: ignore[union-attr, arg-type]
            if not perms.manage_messages:
                await interaction.response.send_message(
                    "Conversation context reset. (No manage_messages permission — messages not deleted.)",
                    ephemeral=True,
                )
                _log("Discord Plugin: /clear — context reset, purge skipped (no permission)")
                return
            await interaction.response.send_message("Clearing conversation and deleting messages...", ephemeral=True)
            deleted = await interaction.channel.purge()  # type: ignore[union-attr]
            _log(f"Discord Plugin: /clear — context reset + purged {len(deleted)} messages in #{getattr(interaction.channel, 'name', '?')}")

        @client.event
        async def on_ready() -> None:
            await tree.sync()
            _log(f"Discord Plugin: connected as {client.user}, slash commands synced")

        @client.event
        async def on_message(message: discord.Message) -> None:
            # Ignore own messages and other bots
            if message.author == client.user or message.author.bot:
                return

            # Sender allowlist FIRST — applies to DMs and guild channels alike.
            # Without it (the previous behaviour) any user who could DM the bot
            # drove the full pipeline at COMMUNICATE tier. Fail-closed: empty
            # allowlist = nobody; explicit ids only (no "*" wildcard, TD8).
            if not _is_discord_user_allowed(message.author.id):
                _log(f"Discord Plugin: blocked message from user {message.author.id} (not in allowlist)")
                return

            # Filter by configured channels (empty = all)
            # Always allow DMs (no guild = direct message)
            is_dm = message.guild is None
            if not is_dm and allowed_channels and message.channel.id not in allowed_channels:
                return

            sender = f"{message.author.display_name} ({message.author.name})"
            timestamp = message.created_at.replace(tzinfo=timezone.utc)

            from ....lib.envelope import InboundMessage

            inbound = InboundMessage(
                channel="discord",
                channel_id=str(message.channel.id),
                sender=sender,
                text=message.content,
                timestamp=timestamp,
                metadata={
                    "guild_id": str(message.guild.id) if message.guild else "",
                    "guild_name": message.guild.name if message.guild else "DM",
                    "channel_name": getattr(message.channel, "name", "DM"),
                    "author_id": str(message.author.id),
                    "message_id": str(message.id),
                },
            )

            _log(
                f"Discord Plugin: message from {sender} "
                f"in #{inbound.metadata.get('channel_name', '?')}"
            )
            await _dispatch_inbound(inbound)

        try:
            await client.start(bot_token)
        except asyncio.CancelledError:
            _log("Discord Plugin: shutting down")
            await client.close()
            _discord_client = None
        except discord.LoginFailure:
            # Config error — retrying won't help, so exit normally (no restart).
            _log("Discord Plugin: invalid bot token", "error")
            _discord_client = None
        except Exception as exc:
            # Any other error: clean up and RE-RAISE so the message hub's
            # auto-restart (exponential backoff, capped) brings the bot back.
            # Swallowing it here would look like a normal exit → worker stays dead.
            _log(f"Discord Plugin: error — {exc}, will be restarted by hub", "error")
            try:
                await client.close()
            except Exception:
                pass
            _discord_client = None
            raise

    # ── Reply ─────────────────────────────────────────────────

    async def send_reply(self, outbound: "OutboundMessage", original: "InboundMessage") -> None:
        """Send a reply message to the Discord channel."""
        if not _discord_client:
            self.channel_log("Discord Plugin: no client connected, cannot send reply", "error")
            return

        channel_id = int(outbound.channel_id)
        channel = _discord_client.get_channel(channel_id)
        if not channel:
            # DM channels may not be in cache — fetch from API
            try:
                channel = await _discord_client.fetch_channel(channel_id)
            except Exception as exc:
                self.channel_log(f"Discord Plugin: channel {channel_id} not found — {exc}", "error")
                return

        # Discord renders Markdown natively (bold/italic/code/links) —
        # the default format_outbound() passthrough is exactly right.
        text = self.format_outbound(outbound.text)["text"]
        await self._deliver(channel, text, _local_file_path(outbound.media))

        from ....lib.debug_bus import debug
        channel_name = getattr(channel, 'name', channel_id)
        debug(f"📤 Reply sent to {outbound.recipient} (#{channel_name})")

    async def _deliver(self, channel, text: str, media: "str | None") -> None:
        """SSOT for the actual Discord send: text in 2000-char chunks plus an
        optional file attachment on the first message. Used by both the reply
        path and the discord_send tool. Discord renders any file type as an
        attachment, so no photo/document distinction is needed."""
        import discord

        file = discord.File(media) if media else None
        chunks = [text[i:i + 2000] for i in range(0, len(text), 2000)] or [""]
        for i, chunk in enumerate(chunks):
            kwargs = {"file": file} if (i == 0 and file) else {}
            await channel.send(chunk or None, **kwargs)  # type: ignore[union-attr]

    # ── Context ───────────────────────────────────────────────

    def build_context(self, message: "InboundMessage") -> str:
        """Prepare Discord message for LLM."""
        from ....lib.prompt_loader import load_prompt

        return load_prompt(
            "shared/channel_discord",
            sender=message.sender,
            guild_name=message.metadata.get("guild_name", "?"),
            channel_name=message.metadata.get("channel_name", "?"),
            text=message.text,
        )


    # ── Tools ─────────────────────────────────────────────────

    def get_tools(self, ctx: "PluginContext") -> list["Tool"]:
        """Provide discord_send tool for LLM function calling."""
        from ....lib.function_calling import Tool
        from ....lib.security import TIER_COMMUNICATE, sanitize_outbound
        from ....lib.credential_broker import broker
        import json

        async def _execute_discord_send(message: str, channel_id: str = "", attachment: str = "") -> str:
            """Send a message to a Discord channel."""
            if not _discord_client:
                return json.dumps({"error": "Discord not connected"})

            # Default to first configured channel
            target_id = channel_id
            if not target_id:
                ids = _parse_channel_ids(broker.get("discord", "channel_ids"))
                if ids:
                    target_id = str(next(iter(ids)))
                else:
                    return json.dumps({"error": "No Discord channel configured"})

            # Redact secrets / block image-exfil URLs on the tool path.
            message = sanitize_outbound(message)

            # Optional attachment. Resolved via the cross-channel SSOT
            # (session-isolated, path-traversal safe, size-capped); the
            # allowlist gate below is the exfiltration guard.
            media: str | None = None
            if attachment:
                from ....lib.vision_utils import resolve_outbound_attachment
                path, err = resolve_outbound_attachment(attachment, ctx.session_id, ctx.source)
                if err:
                    return json.dumps({"error": err})
                media = str(path)

            try:
                # Recipient allowlist gate: only the browser (user present) may
                # target a channel that is not on the configured allowlist. From
                # an external channel an injected prompt could otherwise exfiltrate
                # the conversation to an arbitrary channel the bot can reach.
                if ctx.source != "browser":
                    allowed = _parse_channel_ids(broker.get("discord", "channel_ids"))
                    if not allowed or int(target_id) not in allowed:
                        return json.dumps({
                            "error": (
                                "refused: target channel is not on the allowlist "
                                "(external-channel exfiltration guard). Do this from the web UI."
                            )
                        })

                ch = _discord_client.get_channel(int(target_id))
                if not ch:
                    ch = await _discord_client.fetch_channel(int(target_id))

                await self._deliver(ch, message, media)

                channel_name = getattr(ch, 'name', target_id)
                log_message(f"Discord Plugin: message sent to #{channel_name}")
                return json.dumps({"success": True, "channel": channel_name, "attachment_sent": bool(media)})
            except Exception as exc:
                return json.dumps({"error": str(exc)})

        return [
            Tool(
                name="discord_send",
                tier=TIER_COMMUNICATE,
                description=load_tool_description(__file__, "discord_send"),
                parameters={
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "The message text to send",
                        },
                        "channel_id": {
                            "type": "string",
                            "description": "Discord channel ID (optional, uses default channel if empty)",
                        },
                        "attachment": {
                            "type": "string",
                            "description": (
                                "Optional: URL of a file from THIS conversation to attach "
                                "(an uploaded image, or generated sandbox output like a PDF — "
                                "its /_upload/... URL)."
                            ),
                        },
                    },
                    "required": ["message"],
                },
                executor=_execute_discord_send,
            ),
        ]


async def _dispatch_inbound(message: "InboundMessage") -> None:
    """Hand an inbound message to the message processor."""
    from ....lib.message_processor import process_inbound

    outbound = await process_inbound(message)

    if outbound:
        log_message(
            f"Discord Plugin: processed — reply "
            f"{'sent' if outbound.metadata.get('sent') else 'ready'} "
            f"for {outbound.recipient}"
        )


# Module-level instance — discovered by registry
DiscordChannel_instance = DiscordChannel()
