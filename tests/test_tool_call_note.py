"""Notiz für die llm_history: was die Werkzeuge einer Antwort getan haben.

Kontrakt von ToolKit.call_note:
- Schreibende Aufrufe stehen mit ihren Angaben drin, lesende nur gezählt.
- Geheimnisse in den Angaben sind unkenntlich.
- Lange Werte werden einzeln und sichtbar gekürzt, alle übrigen Angaben bleiben vollständig:
  Das Modell liest die Notiz als seinen Aufruf, ein mitten im Wert abgeschnittenes Datum hielt
  es am 31.10.2026 für einen Fehler und widerrief einen gelungenen Eintrag.
- Die Notiz sagt, was gelungen ist; auch fehlgeschlagene lesende Aufrufe sind markiert.
- Fehlgeschlagen ist ein Aufruf bei einer Ausnahme und bei einem Ergebnis mit ``error``.
- Abgelehnte Aufrufe liefen nicht und stehen nicht drin.
- Das Abholen leert die Aufzeichnung.
"""

import asyncio
import json

from aifred.lib.config import TOOL_CALL_NOTE_VALUE_CHARS
from aifred.lib.function_calling import Tool, ToolKit


def _kit(result: str = "ok", read_result: str = "gelesen") -> ToolKit:
    def write(**_kw):
        return result

    def read(**_kw):
        return read_result

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
    assert "nextcloud_create" in note and "Zahnarzt" in note and "}) ✗" not in note


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


def test_long_value_is_cut_visibly_and_the_others_stay_whole() -> None:
    kit = _kit()
    _run(kit, ("nextcloud_create", {"content": "a" * 5000, "end": "2026-11-12", "data": {"calendar": "Urlaub"}}))
    note = kit.call_note()
    assert note is not None
    assert "a" * TOOL_CALL_NOTE_VALUE_CHARS + "…" in note
    assert "a" * (TOOL_CALL_NOTE_VALUE_CHARS + 1) not in note
    assert "'end': '2026-11-12'" in note and "'calendar': 'Urlaub'" in note


def test_call_from_the_live_test_stays_complete() -> None:
    args = {"entity_type": "event", "data": {"title": "AIfred-Notiztest 1", "all_day": True, "start": "2026-11-12",
                                             "end": "2026-11-12", "calendar": "BF85F99B-5B55-4067-8832-1F0276C2E2D0_shared_by_mp"}}
    kit = _kit(json.dumps({"success": True}))
    _run(kit, ("nextcloud_create", args))
    note = kit.call_note()
    assert note is not None
    assert str(args) in note


def test_secret_in_a_nested_value_is_masked_before_cutting() -> None:
    kit = _kit()
    _run(kit, ("nextcloud_create", {"data": {"password": "streng-geheim" * 20}}))
    note = kit.call_note()
    assert note is not None
    assert "streng-geheim" not in note


def test_error_field_counts_as_failed() -> None:
    kit = _kit(json.dumps({"error": "no such calendar"}))
    _run(kit, ("nextcloud_create", {"title": "x"}))
    note = kit.call_note()
    assert note is not None
    assert "}) ✗" in note


def test_failed_read_is_marked_in_its_count() -> None:
    kit = _kit(read_result=json.dumps({"error": "kaputt"}))
    _run(kit, ("nextcloud_search", {"query": "a"}), ("nextcloud_search", {"query": "b"}))
    note = kit.call_note()
    assert note is not None
    assert "nextcloud_search ×2 ✗2" in note


def test_legend_does_not_touch_the_last_call() -> None:
    kit = _kit()
    _run(kit, ("nextcloud_search", {"query": "a"}))
    note = kit.call_note()
    assert note is not None
    assert "nextcloud_search ×1 (" not in note and "nextcloud_search ×1 ✗" not in note


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


def test_every_write_stands_in_the_note() -> None:
    kit = _kit()
    _run(kit, *[("nextcloud_create", {"n": n}) for n in range(30)])
    note = kit.call_note()
    assert note is not None
    assert note.count("nextcloud_create") == 30


def test_collecting_empties_the_record() -> None:
    kit = _kit()
    _run(kit, ("nextcloud_create", {"title": "x"}))
    assert kit.call_note() is not None
    assert kit.call_note() is None
