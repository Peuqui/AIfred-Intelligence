"""Discord Channel Plugin — Bot listener + reply via discord.py.

Drop-in plugin for the Message Hub channel system.
Connects as a Discord bot and listens for messages in configured channels.
"""

from __future__ import annotations

import asyncio
from datetime import timezone
from typing import TYPE_CHECKING

import discord

from ....lib.plugin_base import BaseChannel, CredentialField
from ....lib.logging_utils import log_message

if TYPE_CHECKING:
    from ....lib.envelope import InboundMessage, OutboundMessage
    from ....lib.function_calling import Tool
    from ....lib.plugin_base import PluginContext

# After a connection error, wait before reconnecting
_RECONNECT_DELAY_SECONDS = 30

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

        @tree.command(name="clear", description="Delete all messages in this channel")
        async def slash_clear(interaction: discord.Interaction) -> None:
            if not interaction.channel or not interaction.guild:
                await interaction.response.send_message("Only works in server channels.", ephemeral=True)
                return
            perms = interaction.channel.permissions_for(interaction.user)  # type: ignore[union-attr]
            if not perms.manage_messages:
                await interaction.response.send_message("No permission.", ephemeral=True)
                return
            await interaction.response.send_message("Deleting messages...", ephemeral=True)
            deleted = await interaction.channel.purge()  # type: ignore[union-attr]
            _log(f"Discord Plugin: /clear — purged {len(deleted)} messages in #{getattr(interaction.channel, 'name', '?')}")

        @client.event
        async def on_ready() -> None:
            await tree.sync()
            _log(f"Discord Plugin: connected as {client.user}, slash commands synced")

        @client.event
        async def on_message(message: discord.Message) -> None:
            # Ignore own messages and other bots
            if message.author == client.user or message.author.bot:
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
            _log("Discord Plugin: invalid bot token", "error")
            _discord_client = None
        except Exception as exc:
            _log(f"Discord Plugin: error — {exc}", "error")
            _discord_client = None

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

        # Discord has a 2000 char limit per message
        text = outbound.text
        if len(text) > 2000:
            # Split into chunks
            chunks = [text[i:i + 2000] for i in range(0, len(text), 2000)]
            for chunk in chunks:
                await channel.send(chunk)  # type: ignore[union-attr]
        else:
            await channel.send(text)  # type: ignore[union-attr]

        from ....lib.debug_bus import debug
        channel_name = getattr(channel, 'name', channel_id)
        debug(f"📤 Reply sent to {outbound.recipient} (#{channel_name})")

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
        from ....lib.security import TIER_COMMUNICATE
        from ....lib.credential_broker import broker
        import json

        async def _execute_discord_send(message: str, channel_id: str = "") -> str:
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

            try:
                ch = _discord_client.get_channel(int(target_id))
                if not ch:
                    ch = await _discord_client.fetch_channel(int(target_id))

                # Discord 2000 char limit
                if len(message) > 2000:
                    chunks = [message[i:i + 2000] for i in range(0, len(message), 2000)]
                    for chunk in chunks:
                        await ch.send(chunk)  # type: ignore[union-attr]
                else:
                    await ch.send(message)  # type: ignore[union-attr]

                channel_name = getattr(ch, 'name', target_id)
                log_message(f"Discord Plugin: message sent to #{channel_name}")
                return json.dumps({"success": True, "channel": channel_name})
            except Exception as exc:
                return json.dumps({"error": str(exc)})

        return [
            Tool(
                name="discord_send",
                tier=TIER_COMMUNICATE,
                description="Send a message to a Discord channel. Use this when the user asks to send a message via Discord.",
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
