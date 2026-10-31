"""Notiz für die llm_history: was die Werkzeuge einer Antwort getan haben.

Kontrakt von ToolKit.call_note:
- Schreibende Aufrufe stehen mit ihren Angaben drin, lesende nur gezählt.
- Geheimnisse in den Angaben sind unkenntlich, lange Angaben gekürzt.
- Fehlgeschlagen ist ein Aufruf bei einer Ausnahme und bei einem Ergebnis mit ``error``.
- Abgelehnte Aufrufe liefen nicht und stehen nicht drin.
- Das Abholen leert die Aufzeichnung.
"""

import asyncio
import json

from aifred.lib.config import TOOL_CALL_NOTE_ARGS_CHARS, TOOL_CALL_NOTE_MAX_WRITES
from aifred.lib.function_calling import Tool, ToolKit


def _kit(result: str = "ok") -> ToolKit:
    def write(**_kw):
        return result

    def read(**_kw):
        return "gelesen"

    def broken(**_kw):
        raise RuntimeError("kaputt")

    return ToolKit(
        tools=[
            Tool(name="nextcloud_create", description="x", parameters={}, executor=write, tier=2),
            Tool(name="nextcloud_search", description="x", parameters={}, executor=read, tier=2, read_only=True),
            Tool(name="broken_tool", description="x", parameters={}, executor=broken, tier=2),
        ],
        _source="browser",
        _max_tier=4,
    )


def _run(kit: ToolKit, *calls: tuple) -> None:
    async def run() -> None:
        for name, args in calls:
            await kit.execute(name, args)

    asyncio.run(run())


def test_nothing_ran_means_no_note() -> None:
    assert _kit().call_note() is None


def test_write_stands_with_its_arguments() -> None:
    kit = _kit()
    _run(kit, ("nextcloud_create", {"title": "Zahnarzt"}))
    note = kit.call_note()
    assert note is not None
    assert "nextcloud_create" in note and "Zahnarzt" in note and "✗ " not in note.split("(✗")[0]


def test_reads_are_counted_without_arguments() -> None:
    kit = _kit()
    _run(kit, ("nextcloud_search", {"query": "a"}), ("nextcloud_search", {"query": "b"}))
    note = kit.call_note()
    assert note is not None
    assert "nextcloud_search ×2" in note and "query" not in note


def test_secret_in_the_arguments_is_masked() -> None:
    kit = _kit()
    _run(kit, ("nextcloud_create", {"title": "x", "password": "streng-geheim"}))
    note = kit.call_note()
    assert note is not None
    assert "streng-geheim" not in note


def test_long_arguments_are_cut() -> None:
    kit = _kit()
    _run(kit, ("nextcloud_create", {"content": "a" * 5000}))
    note = kit.call_note()
    assert note is not None
    assert note.count("a" * 50) <= 2 and len(note) < TOOL_CALL_NOTE_ARGS_CHARS + 300


def test_error_field_counts_as_failed() -> None:
    kit = _kit(json.dumps({"error": "no such calendar"}))
    _run(kit, ("nextcloud_create", {"title": "x"}))
    note = kit.call_note()
    assert note is not None
    assert "}) ✗" in note


def test_exception_counts_as_failed() -> None:
    kit = _kit()
    _run(kit, ("broken_tool", {"a": 1}))
    note = kit.call_note()
    assert note is not None
    assert "}) ✗" in note


def test_refused_repeat_did_not_run_and_is_left_out() -> None:
    kit = _kit()
    _run(kit, *[("nextcloud_create", {"title": "x"})] * 5)
    note = kit.call_note()
    assert note is not None
    assert note.count("nextcloud_create") == 2


def test_many_writes_are_capped() -> None:
    kit = _kit()
    _run(kit, *[("nextcloud_create", {"n": n}) for n in range(TOOL_CALL_NOTE_MAX_WRITES + 3)])
    note = kit.call_note()
    assert note is not None
    assert note.count("nextcloud_create") == TOOL_CALL_NOTE_MAX_WRITES and "+3" in note


def test_collecting_empties_the_record() -> None:
    kit = _kit()
    _run(kit, ("nextcloud_create", {"title": "x"}))
    assert kit.call_note() is not None
    assert kit.call_note() is None
