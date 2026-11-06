"""An MCP server's tools become AIfred tools with the server's tier.

Since b786c1ea (01.11.2026) a tool names the channel it sends on (outbound_channel); only AIfred's
own channels do. The MCP plugin still passed outbound=..., so building any MCP tool raised TypeError.
"""

from pathlib import Path

from mcp import types as mcp_types

from aifred.plugins.tools.mcp_client import McpClientPlugin, load_servers

_EXAMPLE = Path(__file__).resolve().parent.parent / "aifred/plugins/tools/mcp_client/servers.example.json"


def test_example_config_is_valid() -> None:
    assert set(load_servers(_EXAMPLE)) == {"my-server", "my-local-tool"}


def test_tool_carries_server_tier_and_sends_on_no_aifred_channel() -> None:
    cfg = load_servers(_EXAMPLE)["my-server"]
    mcp_tool = mcp_types.Tool(name="lookup", description="Looks something up", inputSchema={"type": "object"})
    tool = McpClientPlugin._build_tool("my-server", cfg, mcp_tool)
    assert tool.name == "my-server_lookup"
    assert tool.tier == cfg["tier"]
    assert tool.outbound_channel == ""
