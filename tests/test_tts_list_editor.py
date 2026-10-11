"""Listen-Editor der Sprachausgabe: Die Liste (Reihenfolge, an/aus, Rechner je Eintrag) und die Einheit je Engine
schreiben in AIfreds Einstellungen; Zeilen, Status und Auswahl kommen aus dem Katalog des Dienstes, die Rechner
gehören dem Dienst (Schreiben per set_config). Der Dienst ist ein httpx.MockTransport (conftest sperrt den echten)."""

from __future__ import annotations

import asyncio
import json
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


def host(name: str, address: str = "10.0.0.2", ssh_port: int = 22, ssh_user: str = "") -> dict[str, Any]:
    return {"name": name, "address": address, "ssh_port": ssh_port, "ssh_user": ssh_user}


class FakeTTSService:
    """GET /engines and GET/POST /config of the service."""

    def __init__(self, catalog: list[dict[str, Any]], hosts: list[dict[str, Any]]) -> None:
        self.catalog = catalog
        self.config: dict[str, Any] = {"voices_dir": "/voices", "hosts": hosts, "engines": {}}
        self.rejection: str | None = None  # the reason of a 400 config_invalid answer
        self.stored: list[dict[str, Any]] = []  # every configuration that was posted

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/engines":
            return httpx.Response(200, json=self.catalog)
        if request.url.path == "/config" and request.method == "GET":
            return httpx.Response(200, json=self.config)
        if request.url.path == "/config" and request.method == "POST":
            if self.rejection is not None:
                return httpx.Response(400, json={"error": "config_invalid", "detail": self.rejection})
            self.config = json.loads(request.content)
            self.stored.append(self.config)
            return httpx.Response(200, json=self.config)
        raise AssertionError(f"unexpected request {request.method} {request.url.path}")


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
    """The fake service plus AIfreds list; the LLM plan names the topmost entry of the list as the reserved engine."""
    def make(
        entries: list[dict[str, Any]],
        catalog: list[dict[str, Any]] | None = None,
        hosts: list[dict[str, Any]] | None = None,
    ) -> tuple[FakeTTSService, SettingsFile]:
        fake = FakeTTSService(catalog if catalog is not None else [catalog_row(e["engine"], e["host"]) for e in entries], hosts or [])
        client = TTSClient("http://tts.test", "token", transport=httpx.MockTransport(fake.handler))
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
        self.tts_host_form_open = True
        self.tts_new_host_name = ""
        self.tts_new_host_address = ""
        self.tts_new_host_ssh_user = ""
        self.tts_new_host_ssh_port = ""
        self._edit_tts_lists = MethodType(TTSConfigMixin._edit_tts_lists, self)  # type: ignore[attr-defined]
        self._edit_tts_hosts = MethodType(TTSConfigMixin._edit_tts_hosts, self)  # type: ignore[attr-defined]

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
    service(
        [stored("xtts"), stored("piper", enabled=False), stored("qwen3local", "Box")],
        catalog=[catalog_row("xtts"), catalog_row("piper"), catalog_row("qwen3local", "Box")],
        hosts=[host("Box")],
    )
    rows = computed("tts_escalation_rows", FakeState())
    assert [row["engine"] for row in rows] == ["xtts", "piper", "qwen3local"]
    assert rows[0] == {
        "index": 0, "engine": "xtts", "label": "XTTS · lokal", "enabled": True, "unit": "sentence",
        "unavailable": False, "reserved": True, "note": "", "status": "schläft — startet bei Bedarf",
    }
    assert rows[1]["enabled"] is False and rows[1]["status"] == "aus" and rows[1]["reserved"] is False
    assert rows[2]["label"] == "QWEN3LOCAL · Box" and rows[2]["reserved"] is False  # a host entry is never reserved
    assert computed("tts_service_error", FakeState()) == ""


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


def test_unreachable_service_shows_the_list_and_a_message_but_raises_nothing(settings_file) -> None:
    settings_file([stored("xtts"), stored("piper", enabled=False)])
    state = FakeState()  # no `service` fixture: the conftest client points at a dead address
    rows = computed("tts_escalation_rows", state)
    assert [(row["engine"], row["enabled"], row["status"]) for row in rows] == [
        ("xtts", True, "nicht erreichbar"), ("piper", False, "nicht erreichbar"),
    ]
    assert rows[0]["label"] == "xtts · Dieser Rechner" and not rows[0]["unavailable"]
    assert computed("tts_add_options", state) == []
    assert computed("tts_host_rows", state) == []
    assert computed("tts_service_error", state).startswith(UNREACHABLE_PREFIX)
    unit = computed("tts_speech_unit", state)
    assert unit.startswith(UNREACHABLE_PREFIX) and unit != "whole"


def test_speech_unit_is_the_one_of_the_topmost_active_entry(service) -> None:
    _, file = service(
        [stored("xtts", enabled=False), stored("piper")],
        catalog=[catalog_row("xtts"), catalog_row("piper", unit_default="whole")],
    )
    assert computed("tts_speech_unit", FakeState()) == "whole"
    file.data["tts_toggles_per_engine"] = {"piper": {"unit": "paragraph"}}
    assert computed("tts_speech_unit", FakeState()) == "paragraph"


def test_speech_unit_without_an_active_entry_is_whole(service) -> None:
    service([stored("xtts", enabled=False)])
    assert computed("tts_speech_unit", FakeState()) == "whole"


def test_add_options_offer_catalog_engines_the_list_does_not_hold(service) -> None:
    service(
        [stored("xtts")],
        catalog=[
            catalog_row("xtts"), catalog_row("xtts", "Aragon"), catalog_row("piper", needs_gpu=False),
            catalog_row("edge", needs_gpu=False, cloud=True),
        ],
        hosts=[host("Aragon")],
    )
    assert computed("tts_add_options", FakeState()) == [
        {"label": "XTTS · Aragon", "value": "xtts|Aragon"},
        {"label": "PIPER · CPU", "value": "piper|"},
        {"label": "EDGE · Cloud", "value": "edge|"},
    ]


def test_host_rows_show_user_and_port(service) -> None:
    service([], hosts=[host("Aragon", "10.0.0.2", 2222, "mp"), host("Box", "10.0.0.3")])
    assert [(row["name"], row["address"], row["ssh"]) for row in computed("tts_host_rows", FakeState())] == [
        ("Aragon", "10.0.0.2", "mp · 2222"), ("Box", "10.0.0.3", "22"),
    ]


# ---- Writing the list (settings.json) ------------------------------------------------------


def test_add_entry_appends_to_the_list_in_the_settings(service, toasts) -> None:
    _, file = service([stored("xtts")])
    state = FakeState(file)
    events = run(TTSConfigMixin.add_tts_entry, state, "piper|")  # type: ignore[arg-type]
    assert file.data["tts_escalation"] == [stored("xtts"), stored("piper")]
    assert events == [] and toasts == [] and state.tts_list_revision == 1


def test_add_entry_on_a_host_keeps_the_host(service, toasts) -> None:
    _, file = service([stored("xtts")])
    run(TTSConfigMixin.add_tts_entry, FakeState(file), "xtts|Aragon")  # type: ignore[arg-type]
    assert file.data["tts_escalation"] == [stored("xtts"), stored("xtts", "Aragon")]


def test_add_entry_twice_is_a_toast_and_changes_nothing(service, toasts) -> None:
    _, file = service([stored("xtts")])
    state = FakeState(file)
    events = run(TTSConfigMixin.add_tts_entry, state, "xtts|")  # type: ignore[arg-type]
    assert len(events) == 1 and events[0][0] == "error" and "already in the list" in events[0][1]
    assert file.writes == 0 and state.tts_list_revision == 0


def test_remove_entry_replans_when_the_planned_engine_moves(service, toasts) -> None:
    _, file = service([stored("xtts"), stored("piper")])
    state = FakeState(file)
    events = run(TTSConfigMixin.remove_tts_entry, state, 0)  # type: ignore[arg-type]
    assert file.data["tts_escalation"] == [stored("piper")]
    assert events == [FakeState.replan_tts] and state.tts_list_revision == 1


def test_move_entry_swaps_neighbours(service, toasts) -> None:
    _, file = service([stored("xtts"), stored("piper"), stored("edge")])
    events = run(TTSConfigMixin.move_tts_entry, FakeState(file), 1, -1)  # type: ignore[arg-type]
    assert [item["engine"] for item in file.data["tts_escalation"]] == ["piper", "xtts", "edge"]
    assert events == [FakeState.replan_tts]


def test_enable_entry_changes_only_the_flag_of_that_entry(service, toasts) -> None:
    _, file = service([stored("xtts"), stored("xtts", "Box"), stored("piper")])
    events = run(TTSConfigMixin.set_tts_entry_enabled, FakeState(file), 1, False)  # type: ignore[arg-type]
    assert file.data["tts_escalation"] == [stored("xtts"), stored("xtts", "Box", enabled=False), stored("piper")]
    assert events == []


def test_a_stale_index_is_a_toast_not_a_wrong_deletion(service, toasts) -> None:
    _, file = service([stored("xtts")])
    events = run(TTSConfigMixin.remove_tts_entry, FakeState(file), 5)  # type: ignore[arg-type]
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


def test_editing_the_list_works_while_the_service_is_down_but_says_the_plan_is_unchecked(settings_file, toasts) -> None:
    file = settings_file([stored("xtts"), stored("piper")])
    state = FakeState(file)   # the conftest client points at a dead address
    events = run(TTSConfigMixin.move_tts_entry, state, 1, -1)  # type: ignore[arg-type]
    assert [item["engine"] for item in file.data["tts_escalation"]] == ["piper", "xtts"]
    assert [kind for kind, _ in events] == ["warning"] and "VRAM" in events[0][1]


# ---- Hosts (stored by the service) --------------------------------------------------------


def fill(state: FakeState, name: str = "", address: str = "", user: str = "", port: str = "") -> FakeState:
    state.tts_new_host_name, state.tts_new_host_address = name, address
    state.tts_new_host_ssh_user, state.tts_new_host_ssh_port = user, port
    return state


def test_saving_a_new_host_posts_user_and_port_and_clears_the_form(service, toasts) -> None:
    fake, file = service([stored("xtts")])
    state = fill(FakeState(file), "Aragon", "10.0.0.2", "mp", "2222")
    run(TTSConfigMixin.save_tts_host, state)  # type: ignore[arg-type]
    assert fake.stored[-1]["hosts"] == [{"name": "Aragon", "address": "10.0.0.2", "ssh_user": "mp", "ssh_port": 2222}]
    assert fake.stored[-1]["voices_dir"] == "/voices"      # the rest of the configuration travels back unchanged
    assert (state.tts_new_host_name, state.tts_new_host_ssh_port, state.tts_host_form_open) == ("", "", False)
    assert state.tts_list_revision == 1 and file.writes == 0   # hosts are not AIfreds settings


def test_an_empty_port_is_left_to_the_service(service, toasts) -> None:
    fake, file = service([stored("xtts")])
    run(TTSConfigMixin.save_tts_host, fill(FakeState(file), "Box", "10.0.0.3"))  # type: ignore[arg-type]
    assert fake.stored[-1]["hosts"] == [{"name": "Box", "address": "10.0.0.3"}]


def test_saving_a_host_of_an_existing_name_changes_that_host(service, toasts) -> None:
    fake, file = service([stored("xtts")], hosts=[host("Aragon", "10.0.0.2"), host("Box", "10.0.0.3")])
    run(TTSConfigMixin.save_tts_host, fill(FakeState(file), "Aragon", "10.0.0.9", "", "2200"))  # type: ignore[arg-type]
    assert sorted((h["name"], h["address"]) for h in fake.stored[-1]["hosts"]) == [("Aragon", "10.0.0.9"), ("Box", "10.0.0.3")]
    assert len(fake.stored[-1]["hosts"]) == 2


def test_edit_host_fills_the_form(service) -> None:
    _, file = service([], hosts=[host("Aragon", "10.0.0.2", 2222, "mp")])
    state = FakeState(file)
    state.tts_host_form_open = False
    TTSConfigMixin.edit_tts_host(state, "Aragon")  # type: ignore[arg-type]
    assert (state.tts_new_host_name, state.tts_new_host_address, state.tts_new_host_ssh_user, state.tts_new_host_ssh_port) == (
        "Aragon", "10.0.0.2", "mp", "2222",
    )
    assert state.tts_host_form_open is True


def test_a_bad_port_or_missing_fields_store_nothing(service, toasts) -> None:
    fake, file = service([stored("xtts")])
    state = fill(FakeState(file), "Box", "10.0.0.3", "", "abc")
    assert run(TTSConfigMixin.save_tts_host, state) == [("error", 'Rechner: Der SSH-Port muss eine Zahl sein, eingegeben wurde „abc“')]  # type: ignore[arg-type]
    state.tts_new_host_name = ""
    assert run(TTSConfigMixin.save_tts_host, state) == [("error", "Rechner: Name und Adresse sind Pflicht")]  # type: ignore[arg-type]
    assert fake.stored == [] and state.tts_host_form_open is True


def test_remove_host(service, toasts) -> None:
    fake, file = service([stored("xtts")], hosts=[host("Aragon"), host("Box")])
    run(TTSConfigMixin.remove_tts_host, FakeState(file), "Aragon")  # type: ignore[arg-type]
    assert [item["name"] for item in fake.stored[-1]["hosts"]] == ["Box"]


def test_a_host_the_list_still_names_is_not_removed(service, toasts) -> None:
    fake, file = service([stored("xtts", "Aragon")], hosts=[host("Aragon")])
    events = run(TTSConfigMixin.remove_tts_host, FakeState(file), "Aragon")  # type: ignore[arg-type]
    assert len(events) == 1 and events[0][0] == "error" and "still has entries" in events[0][1]
    assert fake.stored == []


def test_config_invalid_from_the_service_is_shown_as_a_toast_and_the_form_stays_open(service, toasts) -> None:
    fake, file = service([stored("xtts")])
    fake.rejection = "hosts[Box]: address 'a b' is not a host name or address"
    state = fill(FakeState(file), "Box", "a b")
    events = run(TTSConfigMixin.save_tts_host, state)  # type: ignore[arg-type]
    assert events == [("error", "hosts[Box]: address 'a b' is not a host name or address")]
    assert toasts == events and state.tts_list_revision == 0
    assert state.tts_new_host_name == "Box" and state.tts_host_form_open is True


def test_unreachable_service_on_a_host_write_is_a_toast(settings_file, toasts) -> None:
    state = fill(FakeState(settings_file([stored("xtts")])), "Box", "10.0.0.3")   # the conftest client points at a dead address
    events = run(TTSConfigMixin.save_tts_host, state)  # type: ignore[arg-type]
    assert len(events) == 1 and events[0][0] == "error" and events[0][1].startswith(UNREACHABLE_PREFIX)
    assert state.tts_list_revision == 0
