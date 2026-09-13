"""Template: Channel Plugin for AIfred.

Copy this DIRECTORY to aifred/plugins/channels/my_channel/ and customize.
It will be auto-discovered on next AIfred restart.

Directory structure:
    aifred/plugins/channels/my_channel/
        __init__.py     # This file (plugin code)
        i18n.json       # REQUIRED: plugin_display_name + plugin_description (DE and EN), credential labels
        settings.json   # Auto-generated: non-secret settings

This example shows the minimal structure for a message channel.
Replace the placeholder implementations with your service's API.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ....lib.plugin_base import BaseChannel, CredentialField
from ....lib.logging_utils import log_message

if TYPE_CHECKING:
    from ....lib.envelope import InboundMessage, OutboundMessage
    from ....lib.function_calling import Tool
    from ....lib.plugin_base import PluginContext


class MyChannel(BaseChannel):
    """Template channel plugin."""

    # ── Identity ──────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "my_channel"

    @property
    def icon(self) -> str:
        return "message-circle"  # Lucide icon name

    # ── Credentials & Settings ────────────────────────────────
    #
    # is_secret=True  → stored in .env (passwords, tokens, API keys)
    # is_secret=False → stored in plugin's settings.json (default)
    # is_password=True implies is_secret=True automatically.

    @property
    def credential_fields(self) -> list[CredentialField]:
        return [
            CredentialField(
                env_key="MY_CHANNEL_TOKEN",
                label_key="my_channel_token",  # Key in i18n.json
                placeholder="your-api-token",
                is_password=True,  # → is_secret=True, stored in .env
            ),
            CredentialField(
                env_key="MY_CHANNEL_PORT",
                label_key="my_channel_port",  # Key in i18n.json
                placeholder="8080",
                # is_secret=False (default) → stored in settings.json
            ),
        ]

    def is_configured(self) -> bool:
        from ....lib.credential_broker import broker
        return broker.is_set("my_channel", "token")

    def apply_credentials(self, values: dict[str, str]) -> None:
        from ....lib.credential_broker import broker
        broker.set_runtime("my_channel", "enabled", "true")
        broker.set_runtime("my_channel", "token", values.get("MY_CHANNEL_TOKEN", ""))

    # ── Listener ──────────────────────────────────────────────

    async def listener_loop(self) -> None:
        """Long-running listener. Replace with your service's event loop."""
        if not self.is_configured():
            self.channel_log(f"{self.name}: not configured", "warning")
            return

        self.channel_log(f"{self.name}: starting listener...")

        try:
            while True:
                # TODO: Replace with your service's message polling/websocket
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.channel_log(f"{self.name}: shutting down")

    # ── Reply ─────────────────────────────────────────────────

    async def send_reply(self, outbound: "OutboundMessage", original: "InboundMessage") -> None:
        """Send a reply via your service's API."""
        # TODO: Implement sending
        self.channel_log(f"{self.name}: reply to {outbound.recipient}: {outbound.text[:50]}...")

    # ── Context ───────────────────────────────────────────────

    def build_context(self, message: "InboundMessage") -> str:
        """Build LLM context from incoming message.

        Use prompt files from prompts/de/ and prompts/en/ instead of hardcoded text.
        """
        from ....lib.prompt_loader import load_prompt
        return load_prompt(
            "shared/channel_my_channel",  # Create this prompt file
            sender=message.sender,
            text=message.text,
        )

    # ── Optional: Tools ───────────────────────────────────────

    def get_tools(self, ctx: "PluginContext") -> list["Tool"]:
        """Optional: Provide tools for active sending via this channel."""
        return []  # Override to add send tools


# Module-level instance — REQUIRED for auto-discovery
MyChannel_instance = MyChannel()
