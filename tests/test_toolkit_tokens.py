"""Tool-Schemata zaehlen zum Prompt: Zaehlung, Kompressionspruefung, Tool-Budget."""
from types import SimpleNamespace

from aifred.lib.context_manager import estimate_toolkit_tokens
from aifred.lib.tool_output_cap import TOOL_OUTPUT_MIN_TOKENS, TOOL_OUTPUT_TOTAL_INPUT_RATIO, compute_budget

_DEFS = [{"type": "function", "function": {"name": f"tool_{i}", "description": "Does something useful " * 20,
          "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}} for i in range(5)]


def test_toolkit_tokens_counted_from_definitions():
    assert estimate_toolkit_tokens(None) == 0
    assert estimate_toolkit_tokens(SimpleNamespace(definitions=[])) == 0
    assert estimate_toolkit_tokens(SimpleNamespace(definitions=_DEFS)) > 5 * 20


def test_tool_budget_shrinks_by_tool_schema_tokens():
    ctx = 32_768
    without = compute_budget(ctx, sys_tok=4_000, hist_tok=2_000, mem_tok=500)
    with_tools = compute_budget(ctx, sys_tok=4_000, hist_tok=2_000, mem_tok=500, tools_tok=6_000)
    assert without - with_tools == 6_000
    assert with_tools == int(ctx * TOOL_OUTPUT_TOTAL_INPUT_RATIO) - 12_500
    assert compute_budget(ctx, sys_tok=30_000, hist_tok=0, tools_tok=20_000) == TOOL_OUTPUT_MIN_TOKENS
