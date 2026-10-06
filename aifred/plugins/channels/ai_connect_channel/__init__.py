"""AI-Connect Channel Plugin — AIfred as a peer in the AI-Connect network.

Joins the Bridge under its own peer name, answers messages of allowlisted
peers through the normal message pipeline and offers tools to list peers,
read the conversation with a peer and write to one (on request only).
Credentials and settings via credential broker / settings.json.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, TypeVar

from ....lib.credential_broker import broker
from ....lib.logging_utils import log_message
from ....lib.plugin_base import BaseChannel, CredentialField, load_tool_description
from .client import BRIDGE_SENDER, BridgeClient, BridgeFatalError

if TYPE_CHECKING:
    from ....lib.envelope import InboundMessage, OutboundMessage
    from ....lib.function_calling import Tool
    from ....lib.plugin_base import PluginContext

T = TypeVar("T")

# The running client and the event loop it lives in (the message hub's worker
# loop). The peer tools run in the web worker's loop, so they hand their
# coroutine over to this loop — same pattern as the Discord plugin.
_bridge: BridgeClient | None = None
_bridge_loop: asyncio.AbstractEventLoop | None = None

# Start times of the turns answered per peer, for the loop guard
_turn_times: dict[str, deque[float]] = defaultdict(deque)


async def _on_bridge_loop(coro: Awaitable[T]) -> T:
    """Await ``coro`` on the loop that owns the Bridge connection."""
    loop = _bridge_loop
    if loop is None:
        raise ConnectionError("the AI-Connect listener is not running")
    try:
        current: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        current = None
    if loop is current:
        return await coro
    return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, loop))  # type: ignore[arg-type]


def _is_peer_allowed(peer: str) -> bool:
    """Peer allowlist: comma-separated names or glob patterns (``Mini:*``).
    Empty = nobody (fail-closed); a bare ``*`` is refused like in the other
    channels, because every allowed peer can make AIfred run inference."""
    raw = broker.get("ai_connect", "allowed_peers").strip()
    if not raw:
        return False
    patterns = [part.strip() for part in raw.split(",") if part.strip()]
    if "*" in patterns:
        log_message("AI-Connect Plugin: '*' wildcard in allowed peers is not supported — list names or patterns like 'Mini:*'. Blocking everyone.", "warning")
        return False
    return any(fnmatch.fnmatchcase(peer, pattern) for pattern in patterns)


def _reply_limit_reached(peer: str) -> bool:
    """Loop guard: two assistants that answer each other never stop on their
    own. Allows ``reply_limit`` turns per peer within ``reply_window_minutes``;
    a turn that passes is counted."""
    limit = int(broker.get("ai_connect", "reply_limit"))
    window = float(broker.get("ai_connect", "reply_window_minutes")) * 60
    now = time.monotonic()
    times = _turn_times[peer]
    while times and now - times[0] > window:
        times.popleft()
    if len(times) >= limit:
        return True
    times.append(now)
    return False


def _with_context(content: str, context: dict[str, Any] | None) -> str:
    """The shared file excerpt of a message, appended as quoted text."""
    if not context:
        return content
    location = context["file"] + (f" lines {context['lines']}" if context.get("lines") else "")
    return f"{content}\n\n📎 {location}\n```\n{context['excerpt']}\n```"


class AIConnectChannel(BaseChannel):
    """AIfred as a peer in the AI-Connect network."""

    # ── Identity ──────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "ai_connect"

    @property
    def icon(self) -> str:
        return "network"  # Lucide icon

    @property
    def always_reply(self) -> bool:
        return True

    # ── Credentials ───────────────────────────────────────────

    @property
    def credential_fields(self) -> list[CredentialField]:
        return [
            CredentialField(
                env_key="AI_CONNECT_BRIDGE_HOST",
                label_key="ai_connect_cred_bridge_host",
                default="127.0.0.1",
                group="bridge",
                width_ratio=3,
            ),
            CredentialField(
                env_key="AI_CONNECT_BRIDGE_PORT",
                label_key="ai_connect_cred_bridge_port",
                default="9999",
                group="bridge",
                width_ratio=1,
            ),
            CredentialField(
                env_key="AI_CONNECT_BRIDGE_TOKEN",
                label_key="ai_connect_cred_bridge_token",
                is_password=True,
            ),
            # Placeholder only as a hint: the peer name must be chosen on purpose,
            # because a second session under the same name pushes the first one out.
            CredentialField(
                env_key="AI_CONNECT_PEER_NAME",
                label_key="ai_connect_cred_peer_name",
                placeholder="Host:AIfred",
            ),
            # Bewusst OHNE placeholder (siehe Telegram): ein Beispiel würde beim
            # ungeänderten Speichern zu einem echten Allowlist-Eintrag.
            CredentialField(
                env_key="AI_CONNECT_ALLOWED_PEERS",
                label_key="ai_connect_cred_allowed_peers",
            ),
            CredentialField(
                env_key="AI_CONNECT_REPLY_LIMIT",
                label_key="ai_connect_cred_reply_limit",
                default="6",
                group="loop_guard",
                width_ratio=1,
            ),
            CredentialField(
                env_key="AI_CONNECT_REPLY_WINDOW_MINUTES",
                label_key="ai_connect_cred_reply_window",
                default="10",
                group="loop_guard",
                width_ratio=1,
            ),
        ]

    def is_configured(self) -> bool:
        return (
            broker.get("ai_connect", "enabled").lower() == "true"
            and broker.is_set("ai_connect", "bridge_host")
            and broker.is_set("ai_connect", "bridge_token")
            and broker.is_set("ai_connect", "peer_name")
        )

    def apply_credentials(self, values: dict[str, str]) -> None:
        """Update runtime credentials via the broker."""
        broker.set_runtime("ai_connect", "enabled", "true")
        for field in self.credential_fields:
            value = values.get(field.env_key, "")
            if value:
                service_key = field.env_key.removeprefix("AI_CONNECT_").lower()
                broker.set_runtime("ai_connect", service_key, value)

    # ── Listener ──────────────────────────────────────────────

    async def listener_loop(self) -> None:
        """Keep the Bridge connection and answer peer messages until cancelled."""
        global _bridge, _bridge_loop

        if not self.is_configured():
            self.channel_log("AI-Connect Plugin: not configured, not starting", "warning")
            return

        client = BridgeClient(
            host=broker.get("ai_connect", "bridge_host"),
            port=int(broker.get("ai_connect", "bridge_port")),
            token=broker.get("ai_connect", "bridge_token"),
            peer_name=broker.get("ai_connect", "peer_name"),
        )
        _bridge, _bridge_loop = client, asyncio.get_running_loop()
        self.channel_log(f"AI-Connect Plugin: joining the Bridge as '{client.peer_name}'...")
        try:
            await client.run(self._on_peer_message)
        except BridgeFatalError as exc:
            # Config problem or name conflict: a restart loop cannot heal it —
            # log and end without re-raising (no hub restart), like Telegram's
            # invalid token.
            self.channel_log(f"AI-Connect Plugin: {exc} — fix the config, not restarting", "error")
        except asyncio.CancelledError:
            self.channel_log("AI-Connect Plugin: shutting down")
            raise
        finally:
            _bridge, _bridge_loop = None, None

    async def _on_peer_message(self, data: dict[str, Any]) -> None:
        sender = data["from"]
        content = data["content"]
        if sender == BRIDGE_SENDER:
            self.channel_log(f"AI-Connect Plugin: notice from the Bridge: {content}")
            return
        if not _is_peer_allowed(sender):
            self.channel_log(f"AI-Connect Plugin: blocked message from '{sender}' (not on the allowlist)")
            return
        # A closing [LGTM] ends the exchange; answering it would restart it.
        if content.lstrip().startswith("[LGTM]"):
            self.channel_log(f"AI-Connect Plugin: [LGTM] from '{sender}', not answering")
            return
        if _reply_limit_reached(sender):
            self.channel_log(f"AI-Connect Plugin: reply limit for '{sender}' reached, message dropped", "warning")
            return

        from ....lib.envelope import InboundMessage
        from ....lib.message_processor import dispatch_inbound

        inbound = InboundMessage(
            channel="ai_connect",
            channel_id=sender,
            sender=sender,
            text=_with_context(content, data.get("context")),
            timestamp=datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00")),
            metadata={"peer": sender},
        )
        self.channel_log(f"AI-Connect Plugin: message from '{sender}'")
        await dispatch_inbound(inbound, "AI-Connect Plugin")

    # ── Reply ─────────────────────────────────────────────────

    async def send_reply(self, outbound: "OutboundMessage", original: "InboundMessage") -> None:
        """Answer the peer; peers are assistants, so the Markdown stays as it is."""
        peer = outbound.recipient or original.channel_id
        bridge = _bridge
        if bridge is None:
            raise ConnectionError("the AI-Connect listener is not running")
        await _on_bridge_loop(bridge.send_message(peer, outbound.text))

        from ....lib.debug_bus import debug
        debug(f"Reply sent to AI-Connect peer {peer}")

    # ── Context ───────────────────────────────────────────────

    def build_context(self, message: "InboundMessage") -> str:
        """Prepare a peer message for the LLM with sender context."""
        from ....lib.prompt_loader import load_prompt
        return load_prompt(
            "shared/channel_ai_connect",
            sender=message.sender,
            text=message.text,
        )

    # ── Tools ─────────────────────────────────────────────────

    def get_tools(self, ctx: "PluginContext") -> list["Tool"]:
        """Provide the peer tools for LLM function calling."""
        from ....lib.function_calling import Tool
        from ....lib.security import TIER_COMMUNICATE, TIER_READONLY, sanitize_outbound

        def _bridge_or_error() -> BridgeClient | None:
            return _bridge

        async def _execute_peer_list() -> str:
            bridge = _bridge_or_error()
            if bridge is None:
                return json.dumps({"error": "AI-Connect listener is not running (channel off?)"})
            peers = await _on_bridge_loop(bridge.list_peers())
            return json.dumps({"peers": [
                {key: peer[key] for key in ("name", "state", "state_detail", "status")}
                for peer in peers
            ]}, ensure_ascii=False)

        async def _execute_peer_history(peer: str, limit: int = 20) -> str:
            bridge = _bridge_or_error()
            if bridge is None:
                return json.dumps({"error": "AI-Connect listener is not running (channel off?)"})
            messages = await _on_bridge_loop(bridge.history(peer, limit))
            return json.dumps({"messages": [
                {"from": m["from"], "to": m["to"], "time": m["timestamp"], "content": m["content"]}
                for m in messages
            ]}, ensure_ascii=False)

        async def _execute_peer_send(to: str, message: str) -> str:
            bridge = _bridge_or_error()
            if bridge is None:
                return json.dumps({"error": "AI-Connect listener is not running (channel off?)"})
            # Recipient allowlist gate, same idea as telegram_send: only the
            # browser (user present) may write to a peer that is not allowed.
            # An injected prompt from an external channel could otherwise
            # exfiltrate the conversation to any peer.
            if ctx.source != "browser" and not _is_peer_allowed(to):
                return json.dumps({"error": (
                    "refused: recipient is not on the allowlist "
                    "(external-channel exfiltration guard). Do this from the web UI."
                )})
            await _on_bridge_loop(bridge.send_message(to, sanitize_outbound(message)))
            log_message(f"AI-Connect Plugin: message sent to '{to}'")
            return json.dumps({"success": True, "to": to})

        return [
            Tool(
                name="ai_connect_peer_list",
                tier=TIER_READONLY,
                description=load_tool_description(__file__, "ai_connect_peer_list"),
                parameters={"type": "object", "properties": {}},
                executor=_execute_peer_list,
            ),
            Tool(
                name="ai_connect_peer_history",
                tier=TIER_READONLY,
                description=load_tool_description(__file__, "ai_connect_peer_history"),
                parameters={
                    "type": "object",
                    "properties": {
                        "peer": {"type": "string", "description": "Full peer name, e.g. 'Mini:vllm-research'"},
                        "limit": {"type": "integer", "description": "How many of the latest messages to return (default 20)"},
                    },
                    "required": ["peer"],
                },
                executor=_execute_peer_history,
            ),
            Tool(
                name="ai_connect_peer_send",
                tier=TIER_COMMUNICATE,
                outbound=True,
                description=load_tool_description(__file__, "ai_connect_peer_send"),
                parameters={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Full peer name, e.g. 'Mini:vllm-research'"},
                        "message": {"type": "string", "description": "The message text to send"},
                    },
                    "required": ["to", "message"],
                },
                executor=_execute_peer_send,
            ),
        ]


# Module-level instance — discovered by registry
AIConnectChannel_instance = AIConnectChannel()
