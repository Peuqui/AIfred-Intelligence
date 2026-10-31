"""Identical-call loop breaker (function_calling.ToolKit).

A model that can't make progress tends to re-emit the SAME tool call
byte-for-byte. Write tools are refused before running after
SECURITY_MAX_IDENTICAL_TOOL_CALLS repeats (a repeated write duplicates its
effect whatever it returns); read-only tools are refused once the same call
returned the same result that often in a row. Different arguments, or a
changed result in between, are unaffected. What counts as read-only is the
tool's effect, not its tier: a private calendar is read at a high tier.
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


def _read_kit(state: dict):
    """Read-only tool whose result is whatever ``state["value"]`` holds."""
    calls: list[dict] = []

    def executor(**kw):
        calls.append(kw)
        return state["value"]

    kit = ToolKit(
        tools=[Tool(name="list_files", description="x", parameters={}, executor=executor, tier=0)],
        _source="browser",
        _max_tier=4,
    )
    return kit, calls


def test_read_with_unchanged_result_refused_after_limit():
    kit, _calls = _read_kit({"value": "same"})

    async def run():
        return [await kit.execute("list_files", {"subfolder": "a"}) for _ in range(5)]

    results = asyncio.run(run())
    assert results[0] == results[1] == "same"
    assert all("Refused" in r and "did not change" in r for r in results[2:])


def test_read_after_change_in_between_is_not_refused():
    """The Heizöl case: same list_files call, but a write happened in between."""
    state = {"value": "3 files"}
    kit, calls = _read_kit(state)

    async def run():
        first = [await kit.execute("list_files", {"subfolder": "a"}) for _ in range(2)]
        state["value"] = "4 files"
        third = await kit.execute("list_files", {"subfolder": "a"})
        return first, third

    first, third = asyncio.run(run())
    assert first == ["3 files", "3 files"]
    assert third == "4 files"
    assert len(calls) == 3


def test_read_with_different_arguments_not_blocked():
    kit, _calls = _read_kit({"value": "same"})

    async def run():
        return [await kit.execute("list_files", {"subfolder": s}) for s in "abcde"]

    assert all("Refused" not in r for r in asyncio.run(run()))


def test_write_with_changing_result_still_refused():
    """store_memory answers with a fresh id each time; the result never repeats."""
    counter = iter(range(100))
    kit = ToolKit(
        tools=[Tool(name="store_memory", description="x", parameters={},
                    executor=lambda **kw: f"stored {next(counter)}", tier=2)],
        _source="browser",
        _max_tier=4,
    )

    async def run():
        return [await kit.execute("store_memory", {"content": "X"}) for _ in range(5)]

    results = asyncio.run(run())
    assert all("Refused" in r for r in results[2:])


def _sensitive_read_kit(result: dict):
    def executor(**kw):
        return result["value"]

    kit = ToolKit(
        tools=[Tool(name="nextcloud_search", description="x", parameters={}, executor=executor,
                    tier=2, read_only=True)],
        _source="browser",
        _max_tier=4,
    )
    return kit


def test_sensitive_read_is_judged_by_its_result_not_by_its_tier():
    result = {"value": "a"}
    kit = _sensitive_read_kit(result)

    async def run():
        out = []
        for value in ("a", "a", "b", "b", "c"):
            result["value"] = value
            out.append(await kit.execute("nextcloud_search", {"query": "x"}))
        return out

    assert asyncio.run(run()) == ["a", "a", "b", "b", "c"]


def test_sensitive_read_with_unchanged_result_is_still_refused():
    kit = _sensitive_read_kit({"value": "same"})

    async def run():
        return [await kit.execute("nextcloud_search", {"query": "x"}) for _ in range(4)]

    results = asyncio.run(run())
    assert results[:2] == ["same", "same"]
    assert all("did not change" in r for r in results[2:])
