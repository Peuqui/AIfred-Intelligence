"""Listen-Editor der Sprachausgabe: Die Liste ist immer der Abgleich aus AIfreds gespeicherter
Reihenfolge/Schaltern (settings.json) und dem Katalog des Dienstes. Nichts wird hinzugefügt oder entfernt, nur
geschaltet und verschoben; jede Änderung speichert die vollständige abgeglichene Liste. In den Dienst wird
nichts geschrieben (der Fake-Dienst beantwortet nur GET /engines). Der Dienst ist ein httpx.MockTransport
(conftest sperrt den echten)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import MethodType, SimpleNamespace
from typing import Any

import httpx
import pytest

from aifred.lib import tts_service
from aifred.lib.tts_client import TTSClient
from aifred.state._tts_config_mixin import TTSConfigMixin

UNREACHABLE_PREFIX = "TTS-Dienst nicht erreichbar"


def stored(engine: str, host: str | None = None, enabled: bool = True) -> dict[str, Any]:
    return {"engine": engine, "host": host, "enabled": enabled}


def catalog_row(engine: str, host: str | None = None, **fields: Any) -> dict[str, Any]:
    base = {
        "engine": engine, "host": host, "label": {"de": f"{engine} lang", "en": f"{engine} long"},
        "label_short": {"de": engine.upper(), "en": engine.upper()}, "status": "stopped",
        "unit_default": "sentence", "needs_gpu": True, "cloud": False, "browser": False,
        "installed": True, "start_hint": None, "vram_mib": {},
    }
    return {**base, **fields}


class FakeTTSService:
    """Only GET /engines: AIfred reads the catalog and writes nothing."""

    def __init__(self, catalog: list[dict[str, Any]]) -> None:
        self.catalog = catalog

    def handler(self, request: httpx.Request) -> httpx.Response:
        if (request.method, request.url.path) == ("GET", "/engines"):
            return httpx.Response(200, json=self.catalog)
        raise AssertionError(f"AIfred must only read the catalog, got {request.method} {request.url.path}")


class SettingsFile:
    """settings.json as the editor sees it: persisted_settings() reads it, _write_settings_file replaces it."""

    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self.data: dict[str, Any] = {"tts_escalation": entries, "tts_toggles_per_engine": {}}
        self.writes = 0


@pytest.fixture
def settings_file(monkeypatch: pytest.MonkeyPatch) -> Callable[..., SettingsFile]:
    def make(entries: list[dict[str, Any]]) -> SettingsFile:
        file = SettingsFile(entries)
        monkeypatch.setattr("aifred.lib.settings.persisted_settings", lambda: file.data)
        monkeypatch.setattr(tts_service, "persisted_settings", lambda: file.data)
        return file

    return make


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch, settings_file) -> Callable[..., tuple[FakeTTSService, SettingsFile]]:
    """The fake service plus AIfreds list; the LLM plan names the topmost entry of the list as the reserved engine.
    Default catalog: one row per listed entry."""
    def make(
        entries: list[dict[str, Any]],
        catalog: list[dict[str, Any]] | None = None,
    ) -> tuple[FakeTTSService, SettingsFile]:
        fake = FakeTTSService(catalog if catalog is not None else [catalog_row(e["engine"], e["host"]) for e in entries])
        client = TTSClient("http://tts.test", transport=httpx.MockTransport(fake.handler))
        monkeypatch.setattr(tts_service, "get_tts_client", lambda: client)
        tts_service.reset_cache()
        file = settings_file(entries)
        monkeypatch.setattr(
            "aifred.lib.tts_llm_coordination.planned_tts_engine",
            lambda model_id: file.data["tts_escalation"][0]["engine"] if file.data["tts_escalation"] else "",
        )
        return fake, file

    return make


@pytest.fixture
def toasts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Every toast the handlers raise, as ``(kind, message)``; the handlers yield the same tuple."""
    raised: list[tuple[str, str]] = []

    def record(kind: str, message: str, **_: Any) -> tuple[str, str]:
        raised.append((kind, message))
        return (kind, message)

    monkeypatch.setattr("aifred.lib.toast.toast", record)
    return raised


class FakeState:
    """What the editor's handlers and computed vars touch of the Reflex state."""

    ui_language = "de"

    def __init__(self, file: SettingsFile | None = None) -> None:
        self.file = file
        self.tts_list_revision = 0
        self.agent_tuning = {"aifred": SimpleNamespace(model_id="model")}
        self._edit_tts_lists = MethodType(TTSConfigMixin._edit_tts_lists, self)  # type: ignore[attr-defined]

    def _write_settings_file(self, settings: dict[str, Any]) -> None:
        assert self.file is not None
        self.file.data = settings
        self.file.writes += 1

    def replan_tts(self) -> None:
        """Stands for the event the handlers chain when the planned engine moved."""


def computed(name: str, state: FakeState) -> Any:
    """Value of a computed var of the mixin (its function, without Reflex)."""
    return TTSConfigMixin.__dict__[name].fget(state)


def run(handler: Callable[..., Any], state: FakeState, *args: Any) -> list[Any]:
    async def collect() -> list[Any]:
        return [event async for event in handler(state, *args)]

    return asyncio.run(collect())


# ---- Reading -------------------------------------------------------------------------


def test_rows_combine_the_list_of_the_settings_with_the_catalog(service) -> None:
    _, file = service(
        [stored("xtts"), stored("piper", enabled=False), stored("qwen3local", "Box")],
        catalog=[catalog_row("xtts"), catalog_row("piper"), catalog_row("qwen3local", "Box")],
    )
    rows = computed("tts_escalation_rows", FakeState())
    assert [row["engine"] for row in rows] == ["xtts", "piper", "qwen3local"]
    assert rows[0] == {
        "index": 0, "engine": "xtts", "label": "XTTS · lokal", "enabled": True, "unit": "sentence",
        "unavailable": False, "reserved": True, "note": "", "warn": False, "status": "schläft — startet bei Bedarf",
    }
    assert rows[1]["enabled"] is False and rows[1]["status"] == "aus" and rows[1]["reserved"] is False
    assert rows[2]["label"] == "QWEN3LOCAL · Box" and rows[2]["reserved"] is False  # a host entry is never reserved
    assert computed("tts_service_error", FakeState()) == ""
    assert len(file.data["tts_escalation"]) == 3                                      # reading writes nothing


def test_catalog_rows_the_list_does_not_know_appear_at_the_end_switched_off(service, settings_file) -> None:
    service([stored("xtts")], catalog=[catalog_row("xtts"), catalog_row("piper", needs_gpu=False), catalog_row("xtts", "Aragon")])
    rows = computed("tts_escalation_rows", FakeState())
    assert [(row["engine"], row["label"], row["enabled"]) for row in rows] == [
        ("xtts", "XTTS · lokal", True), ("piper", "PIPER · CPU", False), ("xtts", "XTTS · Aragon", False),
    ]
    assert rows[2]["status"] == "aus"


def test_row_notes_name_missing_row_missing_image_and_start_failure(service) -> None:
    service(
        [stored("a"), stored("b"), stored("c"), stored("d")],
        catalog=[catalog_row("b", installed=False), catalog_row("c", start_hint="no card fits"), catalog_row("d")],
    )
    rows = computed("tts_escalation_rows", FakeState())
    assert [row["note"] for row in rows] == ["Vom Dienst nicht angeboten", "Image fehlt", "no card fits", ""]
    assert rows[0]["unavailable"] is True and rows[0]["status"] == "nicht angeboten"


def test_the_unit_of_a_row_is_the_choice_else_the_default_of_the_catalog(service) -> None:
    _, file = service([stored("xtts"), stored("piper")], catalog=[catalog_row("xtts", unit_default="paragraph"), catalog_row("piper")])
    file.data["tts_toggles_per_engine"] = {"piper": {"unit": "whole"}}
    assert [row["unit"] for row in computed("tts_escalation_rows", FakeState())] == ["paragraph", "whole"]


def test_unreachable_service_shows_the_stored_list_and_a_message_but_raises_nothing(settings_file) -> None:
    settings_file([stored("xtts"), stored("piper", enabled=False)])
    state = FakeState()  # no `service` fixture: the conftest client points at a dead address
    rows = computed("tts_escalation_rows", state)
    assert [(row["engine"], row["enabled"], row["status"]) for row in rows] == [
        ("xtts", True, "nicht erreichbar"), ("piper", False, "nicht erreichbar"),
    ]
    assert rows[0]["label"] == "xtts · Dieser Rechner" and not rows[0]["unavailable"]
    assert computed("tts_service_error", state).startswith(UNREACHABLE_PREFIX)


def test_warn_only_for_a_switched_on_entry_whose_machine_or_service_is_unreachable(service) -> None:
    service(
        [stored("xtts", "Box"), stored("piper", "Box", enabled=False), stored("edge", "Aragon"), stored("moss")],
        catalog=[
            catalog_row("xtts", "Box", status="unreachable"), catalog_row("piper", "Box", status="unreachable"),
            catalog_row("edge", "Aragon", status="running"), catalog_row("moss", status="stopped"),
        ],
    )
    assert [row["warn"] for row in computed("tts_escalation_rows", FakeState())] == [True, False, False, False]


def test_no_warn_while_the_service_itself_does_not_answer(settings_file) -> None:
    settings_file([stored("xtts", "Box")])
    assert [row["warn"] for row in computed("tts_escalation_rows", FakeState())] == [False]


# ---- Writing (settings.json): switching and ordering only ----------------------------------


def test_there_is_nothing_to_add_or_remove_or_to_write_into_the_service() -> None:
    for name in ("add_tts_entry", "remove_tts_entry", "remove_tts_host", "save_tts_host", "edit_tts_host",
                 "toggle_tts_host_form", "tts_add_options", "_edit_tts_hosts",
                 "set_tts_host_enabled", "tts_host_rows"):
        assert name not in TTSConfigMixin.__dict__, name


def test_move_entry_swaps_neighbours_and_saves_the_complete_matched_list(service, toasts) -> None:
    _, file = service(
        [stored("xtts"), stored("piper"), stored("edge")],
        catalog=[catalog_row("xtts"), catalog_row("piper"), catalog_row("edge"), catalog_row("moss")],
    )
    state = FakeState(file)
    events = run(TTSConfigMixin.move_tts_entry, state, 1, -1)  # type: ignore[arg-type]
    assert file.data["tts_escalation"] == [
        stored("piper"), stored("xtts"), stored("edge"), stored("moss", enabled=False),   # the unknown row is saved, off
    ]
    assert events == [FakeState.replan_tts] and state.tts_list_revision == 1


def test_enable_entry_changes_only_the_flag_of_that_entry(service, toasts) -> None:
    _, file = service([stored("xtts"), stored("xtts", "Box"), stored("piper")])
    events = run(TTSConfigMixin.set_tts_entry_enabled, FakeState(file), 1, False)  # type: ignore[arg-type]
    assert file.data["tts_escalation"] == [stored("xtts"), stored("xtts", "Box", enabled=False), stored("piper")]
    assert events == []


def test_switching_off_keeps_every_configuration(service, toasts) -> None:
    _, file = service([stored("piper")])
    file.data["tts_toggles_per_engine"] = {"piper": {"unit": "whole"}}
    file.data["tts_agent_voices_per_engine"] = {"piper": {"aifred": {"voice": "Ramona"}}}
    run(TTSConfigMixin.set_tts_entry_enabled, FakeState(file), 0, False)  # type: ignore[arg-type]
    assert file.data["tts_toggles_per_engine"] == {"piper": {"unit": "whole"}}
    assert file.data["tts_agent_voices_per_engine"] == {"piper": {"aifred": {"voice": "Ramona"}}}
    assert file.data["tts_escalation"] == [stored("piper", enabled=False)]


def test_a_stale_index_is_a_toast_not_a_wrong_change(service, toasts) -> None:
    _, file = service([stored("xtts")])
    events = run(TTSConfigMixin.set_tts_entry_enabled, FakeState(file), 5, False)  # type: ignore[arg-type]
    assert len(events) == 1 and events[0][0] == "error" and "no entry at position 5" in events[0][1]
    assert file.writes == 0


def test_set_unit_writes_it_under_the_engine_and_keeps_the_rest(service, toasts) -> None:
    _, file = service([stored("piper")])
    file.data["tts_toggles_per_engine"] = {"piper": {"autoplay": True, "unit": "sentence"}}
    state = FakeState(file)
    run(TTSConfigMixin.set_tts_entry_unit, state, "piper", "whole")  # type: ignore[arg-type]
    assert file.data["tts_toggles_per_engine"]["piper"] == {"autoplay": True, "unit": "whole"}
    assert state.tts_list_revision == 1
    assert tts_service.speech_unit_for("piper") == "whole"   # what the editor saved, the adapter reads back
    assert file.data["tts_escalation"] == [stored("piper")]  # the list is untouched


def test_set_unit_for_an_engine_without_choice_creates_it(service, toasts) -> None:
    _, file = service([stored("piper")])
    run(TTSConfigMixin.set_tts_entry_unit, FakeState(file), "piper", "paragraph")  # type: ignore[arg-type]
    assert file.data["tts_toggles_per_engine"] == {"piper": {"unit": "paragraph"}}


def test_an_unknown_unit_is_a_toast_and_nothing_is_written(service, toasts) -> None:
    _, file = service([stored("piper")])
    events = run(TTSConfigMixin.set_tts_entry_unit, FakeState(file), "piper", "chapter")  # type: ignore[arg-type]
    assert len(events) == 1 and events[0][0] == "error" and "speech unit" in events[0][1]
    assert file.writes == 0


def test_editing_needs_the_service_because_the_match_needs_the_catalog(settings_file, toasts) -> None:
    file = settings_file([stored("xtts"), stored("piper")])
    state = FakeState(file)   # the conftest client points at a dead address
    events = run(TTSConfigMixin.move_tts_entry, state, 1, -1)  # type: ignore[arg-type]
    assert len(events) == 1 and events[0][0] == "error" and events[0][1].startswith(f"TTS: {UNREACHABLE_PREFIX}")
    assert file.writes == 0 and state.tts_list_revision == 0

