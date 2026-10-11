"""Adapter tts_service: AIfreds Liste und Rechner-Schalter (Einstellungen) im Abgleich mit dem Katalog des
Dienstes, Einheit, request_engines, Zwischenspeicher des Katalogs, Dienst nicht erreichbar.
Der Dienst ist ein httpx.MockTransport; der echte wird nie getroffen (conftest). AIfred schreibt nichts hinein."""

from typing import Any, Callable

import httpx
import pytest

import aifred.lib.tts_service as tts_service
from aifred.lib.config import TTS_SUITE_CACHE_TTL_S
from aifred.lib.config import DEFAULT_TTS_ESCALATION
from aifred.lib.tts_client import EngineInfo, EngineRef, TTSClient, TTSSuiteUnreachable


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

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        if request.url.path == "/engines":
            return httpx.Response(200, json=self.catalog)
        raise AssertionError(f"AIfred must only read the catalog, got {request.method} {request.url.path}")

    def reads(self) -> int:
        return self.requests.count(("GET", "/engines"))


class Settings:
    """What persisted_settings() returns in the tests: AIfreds list, host switches and unit choices."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {"tts_escalation": [], "tts_hosts": [], "tts_toggles_per_engine": {}}


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    holder = Settings()
    monkeypatch.setattr(tts_service, "persisted_settings", lambda: holder.data)
    return holder


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> Callable[..., FakeService]:
    clock = {"now": 100.0}
    monkeypatch.setattr(tts_service, "monotonic", lambda: clock["now"])

    def make(
        *catalog: dict[str, Any], listed: list[dict[str, Any]] | None = None, hosts: list[dict[str, Any]] | None = None,
    ) -> FakeService:
        """``listed`` / ``hosts`` = what settings.json holds; default: every catalog row / host, all on."""
        fake = FakeService(list(catalog))
        client = TTSClient("http://tts.test", transport=httpx.MockTransport(fake.handler))
        monkeypatch.setattr(tts_service, "get_tts_client", lambda: client)
        settings.data["tts_escalation"] = listed if listed is not None else [stored(r["engine"], r["host"]) for r in catalog]
        settings.data["tts_hosts"] = hosts if hosts is not None else [
            {"name": name, "enabled": True} for name in dict.fromkeys(r["host"] for r in catalog if r["host"])
        ]
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
    on = tts_service.ListEntry("xtts", None, True, "sentence", engine_info(), True)
    assert tts_service.status_key(on) == "sleeping"
    assert tts_service.status_key(tts_service.ListEntry("xtts", None, False, "sentence", engine_info(), True)) == "disabled"
    assert tts_service.status_key(tts_service.ListEntry("gone", None, True, "", None, True)) == "not_offered"
    off_host = tts_service.ListEntry("xtts", "Box", True, "sentence", engine_info(host="Box"), False)
    assert tts_service.status_key(off_host) == "host_off"
    assert not off_host.active


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
    assert tts_service.entry_label(tts_service.ListEntry("xtts", info.host, True, "sentence", info, True), "de") == f"XTTS · {place}"


def test_label_of_an_entry_the_service_does_not_offer_names_key_and_host() -> None:
    assert tts_service.entry_label(tts_service.ListEntry("gone", None, True, "", None, True), "de") == "gone · Dieser Rechner"
    assert tts_service.entry_label(tts_service.ListEntry("gone", "Box", True, "", None, True), "de") == "gone · Box"


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


def test_a_catalog_row_the_list_does_not_know_comes_last_and_switched_off(service, settings) -> None:
    service(row("xtts"), row("piper"), row("edge", needs_gpu=False, cloud=True), listed=[stored("piper")])
    listed = tts_service.entries()
    assert [(e.engine, e.enabled, e.active) for e in listed] == [
        ("piper", True, True), ("xtts", False, False), ("edge", False, False),
    ]
    assert tts_service.request_engines() == [EngineRef("piper", "sentence", None)]   # nothing new is sent (cost, cloud)
    assert settings.data["tts_escalation"] == [stored("piper")]                     # reading writes nothing


def test_a_fresh_installs_start_list_orders_and_switches_the_rows_it_names(service, settings) -> None:
    service(
        row("piper", needs_gpu=False), row("fishspeech"), row("qwen3local"), row("xtts"),
        listed=[dict(entry) for entry in DEFAULT_TTS_ESCALATION],
    )
    listed = tts_service.entries()
    assert [(e.engine, e.enabled, e.info is not None) for e in listed] == [
        ("qwen3local", True, True), ("xtts", True, True), ("dashscope_audio3", True, False),
        ("piper", True, True), ("edge", True, False), ("browser", True, False),
        ("fishspeech", False, True),   # offered, not named by the start list: behind it, switched off
    ]
    assert [r.engine for r in tts_service.request_engines()] == ["qwen3local", "xtts", "piper"]


def test_a_stored_row_the_service_no_longer_offers_stays_visible_and_is_never_sent(service, settings) -> None:
    service(row("xtts"), listed=[stored("gone"), stored("xtts")])
    listed = tts_service.entries()
    assert [(e.engine, e.info is None, tts_service.status_key(e)) for e in listed] == [
        ("gone", True, "not_offered"), ("xtts", False, "sleeping"),
    ]
    assert tts_service.request_engines() == [EngineRef("xtts", "sentence", None)]
    assert settings.data["tts_escalation"] == [stored("gone"), stored("xtts")]       # nothing is deleted


def test_matched_list_and_hosts_are_what_a_change_by_the_user_saves(service) -> None:
    service(
        row("xtts"), row("xtts", "Box"), row("piper", needs_gpu=False), row("qwen3local", "Aragon"),
        listed=[stored("piper", enabled=False), stored("gone")], hosts=[{"name": "Old", "enabled": True}],
    )
    rows = tts_service.catalog()
    assert tts_service.matched_list(rows) == [
        stored("piper", enabled=False), stored("gone"),
        stored("xtts", enabled=False), stored("xtts", "Box", enabled=False), stored("qwen3local", "Aragon", enabled=False),
    ]
    assert tts_service.matched_hosts(rows) == [
        {"name": "Old", "enabled": True}, {"name": "Box", "enabled": False}, {"name": "Aragon", "enabled": False},
    ]


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
    {"tts_escalation": [{"engine": "xtts", "host": None}], "tts_hosts": []},
    {"tts_escalation": [stored("xtts"), stored("xtts")], "tts_hosts": []},
    {"tts_escalation": [], "tts_hosts": [{"name": "Box"}]},
    {"tts_escalation": [], "tts_hosts": [{"name": "Box", "enabled": True}, {"name": "Box", "enabled": False}]},
])
def test_a_broken_list_or_host_choice_is_refused(broken: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        tts_service.check_stored_settings(broken)


def test_the_same_engine_on_another_machine_is_no_duplicate() -> None:
    tts_service.check_stored_settings({"tts_escalation": [stored("xtts"), stored("xtts", "Box")], "tts_hosts": []})


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


# ---- Live state (never cached) -----------------------------------------------------------------


def test_running_engine_uses_the_live_state(service) -> None:
    fake = service(row("xtts", status="running"), row("qwen3local", "Box", status="running"))
    tts_service.catalog()
    assert tts_service.running_gpu_engine() == "xtts"
    assert fake.reads() == 2  # the answer did not come from the cache


def test_the_catalog_is_cached_for_the_ttl_only_the_settings_never(service, settings) -> None:
    fake = service(row("xtts"))
    tts_service.entries()
    settings.data["tts_escalation"] = [stored("xtts", enabled=False)]    # a change of the list shows at once
    assert not tts_service.entries()[0].enabled
    assert fake.reads() == 1
    fake.clock["now"] += TTS_SUITE_CACHE_TTL_S + 0.1  # type: ignore[attr-defined]
    tts_service.entries()
    assert fake.reads() == 2


# ---- Hosts: the catalog shows them, AIfred only switches them ----------------------------------


def test_hosts_are_always_all_the_service_offers_new_ones_switched_off(service, settings) -> None:
    service(
        row("xtts", "Aragon"), row("xtts", "Box"),
        hosts=[{"name": "Aragon", "enabled": True}, {"name": "Old", "enabled": True}],
    )
    assert tts_service.host_rows() == [
        {"name": "Aragon", "enabled": True, "offered": True},
        {"name": "Old", "enabled": True, "offered": False},      # no longer offered: stays, shown as such
        {"name": "Box", "enabled": False, "offered": True},      # new: switched off
    ]
    assert len(settings.data["tts_hosts"]) == 2                    # reading writes nothing


def test_a_switched_off_host_takes_its_entries_out_of_the_request_but_keeps_them(service) -> None:
    service(
        row("xtts"), row("qwen3local", "Box"), row("qwen3local", "Aragon"),
        hosts=[{"name": "Box", "enabled": False}, {"name": "Aragon", "enabled": True}],
    )
    listed = tts_service.entries()
    assert [(e.host, e.active, tts_service.status_key(e)) for e in listed] == [
        (None, True, "sleeping"), ("Box", False, "host_off"), ("Aragon", True, "sleeping"),
    ]
    assert [ref.host for ref in tts_service.request_engines()] == [None, "Aragon"]
    assert tts_service.single_engine_ref("qwen3local", "whole").host == "Aragon"
    assert [e.engine for e in listed if e.host == "Box"] == ["qwen3local"]   # the entry itself stays in the list


def test_an_entry_on_a_host_that_was_never_switched_on_is_not_active(service) -> None:
    service(row("xtts", "Box"), hosts=[])
    assert not tts_service.entries()[0].active
    assert tts_service.request_engines() == []


def test_the_adapter_has_no_writing_calls() -> None:
    for name in ("start_entry", "stop_entry", "start_engine", "stop_engine", "reload_engines", "sync_voices",
                 "edit_config", "set_config", "local_gpu_footprint", "add_choices"):
        assert not hasattr(tts_service, name), name


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
