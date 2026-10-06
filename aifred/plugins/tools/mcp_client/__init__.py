"""MCP client plugin — exposes the tools of arbitrary MCP servers to AIfred.

Servers are configured in ``servers.json`` next to this file (machine-local,
see ``servers.example.json``). Every tool of a server becomes the AIfred tool
``<server>_<tool>``; name, description and parameter schema come from the
server, tier and outbound flag from the server's config entry.

``get_tools`` is called synchronously from many places (UI, prompt loader), MCP
discovery is async: the tool list of each server is fetched once in a helper
thread and cached until the plugin registry is reloaded. A server that is down
at that moment contributes no tools until the next reload.
"""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from mcp import ClientSession, StdioServerParameters
from mcp import types as mcp_types
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from ....lib.function_calling import Tool
from ....lib.logging_utils import log_message
from ....lib.plugin_base import PluginContext

SERVERS_FILE = Path(__file__).parent / "servers.json"
REQUIRED_SERVER_KEYS = ("transport", "tier", "outbound", "timeout_seconds")
TRANSPORT_REQUIRED_KEYS = {
    "stdio": ("command",),
    "sse": ("url",),
    "http": ("url",),
}


def load_servers(path: Path = SERVERS_FILE) -> dict[str, dict[str, Any]]:
    """Read and validate the server config. Fail-loud on a malformed entry:
    a typo here must not silently drop a server."""
    if not path.exists():
        return {}
    servers: dict[str, dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    for name, cfg in servers.items():
        missing = [key for key in REQUIRED_SERVER_KEYS if key not in cfg]
        transport = cfg.get("transport")
        if transport not in TRANSPORT_REQUIRED_KEYS:
            raise ValueError(f"mcp_client: server '{name}': unknown transport {transport!r}")
        missing += [key for key in TRANSPORT_REQUIRED_KEYS[transport] if key not in cfg]
        if missing:
            raise ValueError(f"mcp_client: server '{name}': missing keys {missing}")
    return servers


@asynccontextmanager
async def _open_session(cfg: dict[str, Any]) -> AsyncIterator[ClientSession]:
    """One initialized MCP session to the configured server."""
    transport = cfg["transport"]
    if transport == "stdio":
        params = StdioServerParameters(
            command=cfg["command"], args=cfg.get("args", []), env=cfg.get("env"),
        )
        connection = stdio_client(params)
    elif transport == "sse":
        connection = sse_client(cfg["url"], headers=cfg.get("headers"))
    else:
        connection = streamablehttp_client(cfg["url"], headers=cfg.get("headers"))
    async with connection as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            yield session


async def _list_server_tools(cfg: dict[str, Any]) -> list[mcp_types.Tool]:
    async with _open_session(cfg) as session:
        async with asyncio.timeout(cfg["timeout_seconds"]):
            return (await session.list_tools()).tools


async def _call_server_tool(cfg: dict[str, Any], tool_name: str, arguments: dict[str, Any]) -> str:
    async with _open_session(cfg) as session:
        async with asyncio.timeout(cfg["timeout_seconds"]):
            result = await session.call_tool(tool_name, arguments)
    text = "\n".join(
        block.text for block in result.content if isinstance(block, mcp_types.TextContent)
    )
    if result.isError:
        return json.dumps({"error": text})
    return text


def _discover_in_thread(cfg: dict[str, Any]) -> list[mcp_types.Tool]:
    """Run the async discovery from sync code. ``get_tools`` may be called
    inside a running event loop, so a thread with its own loop is the one
    way that always works."""
    outcome: list[list[mcp_types.Tool] | Exception] = []

    def _run() -> None:
        try:
            outcome.append(asyncio.run(_list_server_tools(cfg)))
        except Exception as exc:  # noqa: BLE001 — reported by the caller
            outcome.append(exc)

    thread = threading.Thread(target=_run, daemon=True, name="mcp-discovery")
    thread.start()
    thread.join(cfg["timeout_seconds"] + 5)
    if not outcome:
        raise TimeoutError("MCP discovery did not finish")
    result = outcome[0]
    if isinstance(result, Exception):
        raise result
    return result


@dataclass
class McpClientPlugin:
    name: str = "mcp_client"

    def __post_init__(self) -> None:
        # server name -> discovered tools (empty list = discovery failed)
        self._discovered: dict[str, list[mcp_types.Tool]] = {}

    def is_available(self) -> bool:
        return bool(load_servers())

    def _tools_of(self, server: str, cfg: dict[str, Any]) -> list[mcp_types.Tool]:
        if server not in self._discovered:
            try:
                self._discovered[server] = _discover_in_thread(cfg)
                log_message(f"🔌 mcp_client: '{server}' offers {len(self._discovered[server])} tools")
            except Exception as exc:  # noqa: BLE001
                log_message(f"❌ mcp_client: discovery of '{server}' failed: {exc!r}", "error")
                self._discovered[server] = []
        return self._discovered[server]

    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        tools: list[Tool] = []
        for server, cfg in load_servers().items():
            for mcp_tool in self._tools_of(server, cfg):
                tools.append(self._build_tool(server, cfg, mcp_tool))
        return tools

    @staticmethod
    def _build_tool(server: str, cfg: dict[str, Any], mcp_tool: mcp_types.Tool) -> Tool:
        async def _execute(**arguments: Any) -> str:
            log_message(f"🔌 mcp_client: {server}.{mcp_tool.name} {arguments}")
            try:
                return await _call_server_tool(cfg, mcp_tool.name, arguments)
            except Exception as exc:  # noqa: BLE001 — the model sees the failure
                log_message(f"❌ mcp_client: {server}.{mcp_tool.name} failed: {exc!r}", "error")
                return json.dumps({"error": f"{server}.{mcp_tool.name} failed: {exc}"})

        return Tool(
            name=f"{server}_{mcp_tool.name}",
            description=mcp_tool.description or "",
            parameters=mcp_tool.inputSchema,
            executor=_execute,
            tier=cfg["tier"],
            outbound=cfg["outbound"],
        )

    def get_prompt_instructions(self, lang: str, granted_tools: "set[str] | None" = None) -> str:
        from ....lib.plugin_base import load_plugin_instructions
        return load_plugin_instructions(self, lang, granted_tools)

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        for server in load_servers():
            if tool_name.startswith(f"{server}_"):
                return f"🔌 {tool_name}"
        return ""


plugin = McpClientPlugin()
