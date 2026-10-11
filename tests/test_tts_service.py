"""Adapter tts_service: AIfreds Liste (Einstellungen) + Katalog des Dienstes, Einheit, request_engines,
Hosts aus der Dienst-Konfiguration, Zwischenspeicher des Katalogs, Schreibaufrufe, Dienst nicht erreichbar.
Der Dienst ist ein httpx.MockTransport; der echte wird nie getroffen (conftest)."""

from typing import Any, Callable

import httpx
import pytest

import aifred.lib.tts_service as tts_service
from aifred.lib.config import TTS_SUITE_CACHE_TTL_S
from aifred.lib.tts_client import EngineInfo, EngineInUse, EngineRef, TTSClient, TTSSuiteUnreachable


def row(engine: str = "xtts", host: str | None = None, **fields: Any) -> dict[str, Any]:
    """A catalog row as GET /engines delivers it."""
    base = {
        "engine": engine, "host": host, "label": {"de": f"{engine} lang", "en": f"{engine} long"},
        "label_short": {"de": engine.upper(), "en": engine.upper()},
        "status": "stopped", "unit_default": "sentence", "needs_gpu": True,
        "cloud": False, "browser": False, "installed": True, "start_hint": None, "vram_mib": {},
    }
    return {**base, **fields}


def stored(engine: str, host: str | None = None, enabled: bool = True) -> dict[str, Any]:
    return {"engine": engine, "host": host, "enabled": enabled}


class FakeService:
    def __init__(self, catalog: list[dict[str, Any]]) -> None:
        self.catalog = catalog
        self.requests: list[tuple[str, str]] = []
        self.stop_error: dict[str, str] | None = None
        self.hosts: list[dict[str, Any]] = [
            {"name": "Aragon", "address": "10.0.0.2", "ssh_port": 2222, "ssh_user": "mp"},
            {"name": "Box", "address": "10.0.0.3", "ssh_port": 22, "ssh_user": ""},
        ]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        if request.url.path == "/engines":
            return httpx.Response(200, json=self.catalog)
        if request.url.path.endswith("/stop") and self.stop_error:
            return httpx.Response(409, json=self.stop_error)
        if request.url.path == "/engines/reload":
            return httpx.Response(200, json={"removed": [], "added": []})
        if request.url.path == "/config":
            return httpx.Response(200, json={"voices_dir": "/v", "hosts": self.hosts, "engines": {}})
        return httpx.Response(204)

    def reads(self) -> int:
        return self.requests.count(("GET", "/engines"))


class Settings:
    """What persisted_settings() returns in the tests: AIfreds list and the unit choices."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {"tts_escalation": [], "tts_toggles_per_engine": {}}


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    holder = Settings()
    monkeypatch.setattr(tts_service, "persisted_settings", lambda: holder.data)
    return holder


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> Callable[..., FakeService]:
    clock = {"now": 100.0}
    monkeypatch.setattr(tts_service, "monotonic", lambda: clock["now"])

    def make(*catalog: dict[str, Any], listed: list[dict[str, Any]] | None = None) -> FakeService:
        fake = FakeService(list(catalog))
        client = TTSClient("http://tts.test", "token", transport=httpx.MockTransport(fake.handler))
        monkeypatch.setattr(tts_service, "get_tts_client", lambda: client)
        settings.data["tts_escalation"] = listed if listed is not None else [stored(r["engine"], r["host"]) for r in catalog]
        tts_service.reset_cache()
        fake.clock = clock  # type: ignore[attr-defined]
        return fake

    return make


def engine_info(**fields: Any) -> EngineInfo:
    return EngineInfo(**row(**fields))


# ---- Status and place ---------------------------------------------------------------------


@pytest.mark.parametrize(("fields", "key"), [
    ({"browser": True, "status": "stopped"}, "ready"),
    ({"installed": False}, "no_image"),
    ({"status": "starting"}, "starting"),
    ({"status": "running"}, "running"),
    ({"status": "running", "needs_gpu": False}, "ready"),
    ({"status": "running", "cloud": True, "needs_gpu": False}, "cloud"),
    ({"status": "stopped", "start_hint": "no card fits"}, "start_failed"),
    ({"status": "unreachable", "host": "Box"}, "unreachable"),
    ({"status": "stopped", "cloud": True, "needs_gpu": False}, "no_access"),
    ({"status": "stopped"}, "sleeping"),
])
def test_row_status_key(fields: dict[str, Any], key: str) -> None:
    assert tts_service.row_status_key(engine_info(**fields)) == key


def test_entry_status_adds_the_users_decision_and_the_missing_row() -> None:
    on = tts_service.ListEntry("xtts", None, True, "sentence", engine_info())
    assert tts_service.status_key(on) == "sleeping"
    assert tts_service.status_key(tts_service.ListEntry("xtts", None, False, "sentence", engine_info())) == "disabled"
    assert tts_service.status_key(tts_service.ListEntry("gone", None, True, "", None)) == "not_offered"


@pytest.mark.parametrize(("fields", "place"), [
    ({"host": "Aragon"}, "Aragon"),
    ({"browser": True, "needs_gpu": False}, "Gerät"),
    ({"cloud": True, "needs_gpu": False}, "Cloud"),
    ({}, "lokal"),
    ({"needs_gpu": False}, "CPU"),
])
def test_location_and_labels(fields: dict[str, Any], place: str) -> None:
    info = engine_info(**fields)
    assert tts_service.location_label(info, "de") == place
    assert tts_service.row_label(info, "de") == f"XTTS · {place}"
    assert tts_service.entry_label(tts_service.ListEntry("xtts", info.host, True, "sentence", info), "de") == f"XTTS · {place}"


def test_label_of_an_entry_the_service_does_not_offer_names_key_and_host() -> None:
    assert tts_service.entry_label(tts_service.ListEntry("gone", None, True, "", None), "de") == "gone · Dieser Rechner"
    assert tts_service.entry_label(tts_service.ListEntry("gone", "Box", True, "", None), "de") == "gone · Box"


# ---- The list: settings + catalog ----------------------------------------------------------


def test_the_list_and_its_order_come_from_the_settings_the_rest_from_the_catalog(service) -> None:
    service(
        row("xtts", status="running"), row("piper", needs_gpu=False, status="running"), row("qwen3local", "Box"),
        listed=[stored("piper"), stored("xtts", enabled=False), stored("qwen3local", "Box")],
    )
    listed = tts_service.entries()
    assert [(e.engine, e.host, e.enabled) for e in listed] == [
        ("piper", None, True), ("xtts", None, False), ("qwen3local", "Box", True),
    ]
    assert [tts_service.status_key(e) for e in listed] == ["ready", "disabled", "sleeping"]
    assert listed[1].info is not None and listed[1].info.status == "running"


def test_an_engine_in_the_catalog_but_not_in_the_list_does_not_appear(service) -> None:
    service(row("xtts"), row("piper"), listed=[stored("piper")])
    assert [e.engine for e in tts_service.entries()] == ["piper"]


def test_an_entry_without_catalog_row_is_listed_but_never_active(service) -> None:
    service(row("xtts"), listed=[stored("gone"), stored("xtts", "Nowhere"), stored("xtts")])
    listed = tts_service.entries()
    assert [e.info is None for e in listed] == [True, True, False]
    assert [e.active for e in listed] == [False, False, True]
    assert tts_service.request_engines() == [EngineRef("xtts", "sentence", None)]


def test_first_enabled_entry_skips_disabled_and_not_offered(service) -> None:
    service(row("a"), row("c"), listed=[stored("a", enabled=False), stored("b"), stored("c")])
    first = tts_service.first_enabled_entry()
    assert first is not None and first.engine == "c"


def test_stored_entries_need_no_service(settings) -> None:
    settings.data["tts_escalation"] = [stored("xtts"), stored("piper", enabled=False)]
    settings.data["tts_toggles_per_engine"] = {"piper": {"unit": "whole"}}
    # conftest points the default client at a dead address: only the settings are read
    assert [(e.engine, e.enabled, e.unit, e.info) for e in tts_service.stored_entries()] == [
        ("xtts", True, "", None), ("piper", False, "whole", None),
    ]


@pytest.mark.parametrize("broken", [
    [{"engine": "xtts", "host": None}],
    [stored("xtts"), stored("xtts")],
])
def test_a_broken_list_is_refused(broken: list[dict[str, Any]]) -> None:
    with pytest.raises(ValueError):
        tts_service.check_stored_list(broken)


def test_the_same_engine_on_another_machine_is_no_duplicate() -> None:
    tts_service.check_stored_list([stored("xtts"), stored("xtts", "Box")])


# ---- Unit ---------------------------------------------------------------------------------


def test_the_unit_is_the_users_choice_else_the_catalogs_default(service, settings) -> None:
    service(row("xtts", unit_default="paragraph"), row("piper", unit_default="whole"), row("edge", unit_default="sentence"))
    settings.data["tts_toggles_per_engine"] = {"edge": {"autoplay": True, "unit": "whole"}}
    assert tts_service.speech_unit_for("xtts") == "paragraph"      # no choice: unit_default of the catalog
    assert tts_service.speech_unit_for("edge") == "whole"          # the choice wins
    assert {e.engine: e.unit for e in tts_service.entries()} == {"xtts": "paragraph", "piper": "whole", "edge": "whole"}


def test_a_choice_needs_no_catalog_but_a_default_does(settings) -> None:
    settings.data["tts_toggles_per_engine"] = {"edge": {"unit": "whole"}}
    assert tts_service.speech_unit_for("edge") == "whole"
    with pytest.raises(TTSSuiteUnreachable):                        # conftest: dead service, no silent "whole"
        tts_service.speech_unit_for("piper")


def test_an_invalid_saved_unit_is_a_configuration_error(settings) -> None:
    settings.data["tts_toggles_per_engine"] = {"edge": {"unit": "word"}}
    with pytest.raises(ValueError, match="speech unit"):
        tts_service.speech_unit_for("edge")


def test_an_engine_unknown_to_the_catalog_has_no_default_unit(service) -> None:
    service(row("xtts"))
    with pytest.raises(ValueError, match="not in the service's catalog"):
        tts_service.speech_unit_for("nope")


# ---- The list of a request ----------------------------------------------------------------------


def test_request_engines_are_the_active_entries_in_order_with_unit_and_host(service, settings) -> None:
    service(
        row("xtts", unit_default="paragraph"), row("piper", unit_default="whole"), row("qwen3local", "Box"),
        row("browser", browser=True, needs_gpu=False),
        listed=[stored("piper", enabled=False), stored("qwen3local", "Box"), stored("xtts"), stored("browser")],
    )
    settings.data["tts_toggles_per_engine"] = {"xtts": {"unit": "sentence"}}
    assert tts_service.request_engines() == [
        EngineRef("qwen3local", "sentence", "Box"),
        EngineRef("xtts", "sentence", None),
        EngineRef("browser", "sentence", None),   # the service skips the browser entry itself
    ]


def test_request_engines_with_nothing_switched_on_is_empty(service) -> None:
    service(row("xtts"), listed=[stored("xtts", enabled=False)])
    assert tts_service.request_engines() == []   # the service answers such a request with 422


# ---- Catalog based keys ---------------------------------------------------------------------


def test_keys_and_labels_come_from_the_catalog_not_from_the_list(service) -> None:
    service(
        row("xtts"), row("qwen3local", "Box"), row("moss", installed=False),
        row("piper", needs_gpu=False), row("edge", needs_gpu=False, cloud=True),
        listed=[stored("piper")],
    )
    assert tts_service.gpu_engine_keys() == {"xtts", "moss"}
    assert tts_service.installed_gpu_engine_keys() == ["xtts"]
    assert tts_service.usable_engine_keys() == ["xtts", "qwen3local", "piper", "edge"]
    assert tts_service.engine_label("xtts", "de") == "xtts lang"
    assert tts_service.engine_label_short("xtts", "en") == "XTTS"
    assert tts_service.engine_key_for_label("xtts long") == "xtts"
    assert tts_service.engine_key_for_label("unknown") == "unknown"


def test_active_local_gpu_keys_follow_the_list(service) -> None:
    service(
        row("xtts"), row("qwen3local"), row("qwen3local", "Box"), row("piper", needs_gpu=False),
        listed=[stored("qwen3local", "Box"), stored("piper"), stored("xtts", enabled=False), stored("qwen3local")],
    )
    assert tts_service.active_local_gpu_keys() == ["qwen3local"]
    assert tts_service.has_active_local_gpu_entry("qwen3local")
    assert not tts_service.has_active_local_gpu_entry("xtts")


def test_add_choices_offer_every_catalog_row_not_in_the_list(service) -> None:
    service(
        row("xtts"), row("xtts", "Aragon"), row("piper", needs_gpu=False), row("edge", needs_gpu=False, cloud=True),
        listed=[stored("xtts"), stored("edge")],
    )
    assert tts_service.add_choices("de") == [
        {"label": "XTTS · Aragon", "value": "xtts|Aragon"},
        {"label": "PIPER · CPU", "value": "piper|"},   # an engine the list does not hold at all: the gap the catalog closes
    ]


# ---- Live state (never cached) -----------------------------------------------------------------


def test_running_engine_and_footprint_use_the_live_state(service) -> None:
    fake = service(row("xtts", status="running", vram_mib={"GPU-a": 3000}), row("qwen3local", "Box", status="running"))
    assert tts_service.running_gpu_engine() == "xtts"
    assert tts_service.local_gpu_footprint() == {"xtts": {"GPU-a": 3000}}
    assert fake.reads() == 2  # neither answer came from the cache


def test_unmeasured_footprint_is_an_error(service) -> None:
    service(row("xtts", status="running", vram_mib=None))
    with pytest.raises(tts_service.TTSVramUnknown):
        tts_service.local_gpu_footprint()


def test_the_catalog_is_cached_for_the_ttl_only_the_settings_never(service, settings) -> None:
    fake = service(row("xtts"))
    tts_service.entries()
    settings.data["tts_escalation"] = [stored("xtts", enabled=False)]    # a change of the list shows at once
    assert not tts_service.entries()[0].enabled
    assert fake.reads() == 1
    fake.clock["now"] += TTS_SUITE_CACHE_TTL_S + 0.1  # type: ignore[attr-defined]
    tts_service.entries()
    assert fake.reads() == 2


@pytest.mark.parametrize("write", [
    lambda: tts_service.start_entry("xtts"),
    lambda: tts_service.stop_entry("xtts"),
    lambda: tts_service.reload_engines(),
    lambda: tts_service.set_config({"hosts": []}),
])
def test_writing_calls_clear_the_cache(service, write: Callable[[], object]) -> None:
    fake = service(row())
    tts_service.entries()
    write()
    tts_service.entries()
    assert fake.reads() == 2


def test_a_stop_refused_because_the_engine_is_in_use_is_reported(service) -> None:
    fake = service(row())
    fake.stop_error = {"error": "engine_in_use", "detail": "reading runs"}
    ok, message = tts_service.stop_engine("xtts")
    assert not ok and "in use" in message and "reading runs" in message
    with pytest.raises(EngineInUse):
        tts_service.stop_entry("xtts")


# ---- Hosts: the service's ---------------------------------------------------------------------


def test_host_rows_come_from_the_services_config(service) -> None:
    service(row())
    assert tts_service.host_rows() == [
        {"name": "Aragon", "address": "10.0.0.2", "ssh_user": "mp", "ssh_port": "2222", "ssh": "mp · 2222"},
        {"name": "Box", "address": "10.0.0.3", "ssh_user": "", "ssh_port": "22", "ssh": "22"},
    ]


def test_edit_config_stores_the_changed_configuration_and_clears_the_cache(service, monkeypatch) -> None:
    sent: list[Any] = []
    fake = service(row())
    original = fake.handler

    def recording(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/config":
            sent.append(request.content)
            return httpx.Response(200, json={"stored": True})
        return original(request)

    client = TTSClient("http://tts.test", "token", transport=httpx.MockTransport(recording))
    monkeypatch.setattr(tts_service, "get_tts_client", lambda: client)
    tts_service.entries()

    def add_host(config: dict[str, Any]) -> None:
        config["hosts"].append({"name": "New", "address": "10.0.0.9"})

    assert tts_service.edit_config(add_host) == {"stored": True}
    assert b'"New"' in sent[0] and b'"voices_dir"' in sent[0]   # the rest of the configuration travels back
    tts_service.entries()
    assert fake.reads() == 2


# ---- Service not reachable --------------------------------------------------------------------


def test_an_unreachable_service_raises_and_the_ui_text_names_it() -> None:
    # conftest points the default client at a dead address
    with pytest.raises(TTSSuiteUnreachable) as raised:
        tts_service.gpu_engine_keys()
    assert "unreachable" in tts_service.unreachable_text(raised.value, "en")
    assert "nicht erreichbar" in tts_service.unreachable_text(raised.value, "de")


def test_planned_engine_is_the_topmost_active_local_gpu_entry_with_a_profile(service, monkeypatch) -> None:
    from aifred.lib.tts_llm_coordination import planned_tts_engine

    monkeypatch.setattr("aifred.lib.calibration.has_llamaswap_tts_variant", lambda path, model_id, key: key == "xtts")
    fake = service(
        row("qwen3local", "Aragon"), row("qwen3local"), row("xtts"), row("piper", needs_gpu=False),
        listed=[stored("qwen3local", "Aragon"), stored("qwen3local"), stored("xtts"), stored("piper")],
    )
    assert planned_tts_engine("model") == "xtts"   # remote: never reserved; no calibrated profile for qwen3local
    tts_service_settings = tts_service.persisted_settings()
    tts_service_settings["tts_escalation"][2]["enabled"] = False
    assert planned_tts_engine("model") == ""
    assert fake.reads() == 1                       # the list is read from the settings each time, the catalog once


def test_planning_with_an_unreachable_service_is_an_error_not_an_empty_plan(settings) -> None:
    from aifred.lib.tts_llm_coordination import planned_tts_engine

    settings.data["tts_escalation"] = [stored("xtts")]
    with pytest.raises(TTSSuiteUnreachable):
        planned_tts_engine("model")
