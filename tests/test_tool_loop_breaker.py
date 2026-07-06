"""Identical-call loop breaker (function_calling.ToolKit).

A model that can't make progress tends to re-emit the SAME tool call
byte-for-byte; that can never produce a new result, so the breaker refuses
it after SECURITY_MAX_IDENTICAL_TOOL_CALLS repeats instead of burning a
full inference round each time. Genuinely different arguments are unaffected.
"""

import asyncio

from aifred.lib.function_calling import Tool, ToolKit


def _kit():
    calls: list[dict] = []

    def executor(**kw):
        calls.append(kw)
        return "ok"

    kit = ToolKit(
        tools=[Tool(name="store_memory", description="x", parameters={}, executor=executor, tier=2)],
        _source="browser",
        _max_tier=4,
    )
    return kit, calls


def test_identical_calls_refused_after_limit():
    kit, calls = _kit()

    async def run():
        results = [await kit.execute("store_memory", {"content": "X"}) for _ in range(5)]
        return results

    results = asyncio.run(run())
    # First 2 execute, 3rd..5th refused (SECURITY_MAX_IDENTICAL_TOOL_CALLS=2)
    assert "error" not in results[0] and "error" not in results[1]
    assert all("Refused" in r for r in results[2:])
    assert len(calls) == 2  # executor really ran only twice


def test_different_arguments_not_blocked():
    kit, calls = _kit()

    async def run():
        return [
            await kit.execute("store_memory", {"content": "A"}),
            await kit.execute("store_memory", {"content": "B"}),
            await kit.execute("store_memory", {"content": "C"}),
        ]

    results = asyncio.run(run())
    assert all("error" not in r for r in results)
    assert len(calls) == 3


def test_key_order_is_canonicalised():
    """Same args, different JSON key order → still counts as identical."""
    kit, calls = _kit()

    async def run():
        return [
            await kit.execute("store_memory", {"a": 1, "b": 2}),
            await kit.execute("store_memory", {"b": 2, "a": 1}),
            await kit.execute("store_memory", {"a": 1, "b": 2}),
        ]

    results = asyncio.run(run())
    assert "error" not in results[0]
    assert "error" not in results[1]      # 2nd repeat still within limit
    assert "Refused" in results[2]        # 3rd identical → refused
    assert len(calls) == 2
