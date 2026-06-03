"""Envelope types for the Message Hub.

Channel-agnostic message representations. Every inbound message from any
channel (email, Discord, Telegram, Signal) is normalised into an
InboundMessage before reaching the AIfred engine. Replies go out as
OutboundMessage — the channel plugin converts back to the native format.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class InboundMessage:
    """Normalised incoming message from any channel."""

    channel: str        # "email", "discord", "telegram", "signal"
    channel_id: str     # Thread-ID, Channel-ID, Conversation-ID
    sender: str         # E-Mail address, Discord user, Telegram user
    text: str           # Plain-text message content
    timestamp: datetime
    metadata: dict = field(default_factory=dict)   # Channel-specific extras
    target_agent: str = "aifred"                   # Which agent should respond?


@dataclass
class OutboundMessage:
    """Normalised outgoing reply to any channel."""

    channel: str        # Same channel as inbound
    channel_id: str     # Same thread / conversation
    recipient: str      # Original sender
    text: str           # Reply text
    media: str | None = None   # optional image (local path or URL) to attach
    metadata: dict = field(default_factory=dict)   # Channel-specific extras
