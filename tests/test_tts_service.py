"""Adapter tts_service: AIfreds Liste und Rechner-Schalter (Einstellungen) im Abgleich mit dem Katalog des
Dienstes, Einheit, request_engines, Zwischenspeicher des Katalogs, Dienst nicht erreichbar.
Der Dienst ist ein httpx.MockTransport; der echte wird nie getroffen (conftest). AIfred schreibt nichts hinein."""

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from aifred.lib import tts_service
from aifred.lib.config import DEFAULT_TTS_ESCALATION, TTS_SUITE_CACHE_TTL_S, VRAM_CONTEXT_RATIO_DENSE
from aifred.lib.research import context_utils
from aifred.lib.tts_client import (
    EngineInfo,
    EngineRef,
    SpeakSummary,
    TraceStep,
    TTSClient,
    TTSSuiteUnreachable,
)


def row(engine: str = "xtts", host: str | None = None, **fields: Any) -> dict[str, Any]:
    """A catalog row as GET /engines delivers it."""
    base = {
        "engine": engine, "host": host, "label": {"de": f"{engine} lang", "en": f"{engine} long"},
        "label_short": {"de": engine.upper(), "en": engine.upper()},
        "status": "stopped", "unit_default": "sentence", "needs_gpu": True,
        "cloud": False, "browser": False, "installed": True, "start_hint": None, "vram_mib": {}, "idle_remaining_s": None,
        "default_vram_mib": None, "needed_mib": None, "default_max_parallel": 1, "default_max_queue": 4,
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
    """What persisted_settings() returns in the tests: AIfreds list and unit choices."""

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
        """``listed`` = what settings.json holds; default: every catalog row, all on."""
        fake = FakeService(list(catalog))
        client = TTSClient("http://tts.test", transport=httpx.MockTransport(fake.handler))
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


def test_the_reserves_are_the_figures_the_service_names_for_its_local_gpu_engines(service) -> None:
    service(
        row("xtts", needed_mib=3936), row("qwen3local", needed_mib=6280),
        row("qwen3local", "Box"), row("piper", needs_gpu=False), row("kokoro"),   # other machine, no GPU, no budget
    )
    assert tts_service.gpu_engine_needs() == {"xtts": 3936, "qwen3local": 6280}


OLLAMA_CONTEXT = 100_000


@pytest.fixture
def ollama_state(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setattr(context_utils, "get_ollama_calibrated_max_context", lambda *_: OLLAMA_CONTEXT)
    monkeypatch.setattr(context_utils, "get_rope_factor_for_model", lambda _: 1.0)
    monkeypatch.setattr(context_utils, "is_moe_model", lambda _: False)
    return SimpleNamespace(enable_tts=True, backend_type="ollama", agent_tuning={})


def test_the_ollama_context_reserves_for_the_first_switched_on_local_gpu_engine(service, ollama_state) -> None:
    service(row("piper", needs_gpu=False), row("xtts", needed_mib=3936), row("qwen3local", needed_mib=6280))
    num_ctx, source = context_utils.get_agent_num_ctx("aifred", ollama_state, "model")
    assert num_ctx == OLLAMA_CONTEXT - int(3936 / VRAM_CONTEXT_RATIO_DENSE)   # piper comes first and holds no card
    assert "XTTS" in source


def test_the_ollama_context_reserves_nothing_for_a_list_without_a_local_gpu_engine(service, ollama_state) -> None:
    service(row("piper", needs_gpu=False))
    assert context_utils.get_agent_num_ctx("aifred", ollama_state, "model")[0] == OLLAMA_CONTEXT


def test_a_service_that_does_not_answer_costs_the_ollama_chat_nothing(settings, ollama_state) -> None:
    settings.data["tts_escalation"] = [stored("xtts")]
    tts_service.reset_cache()                                     # conftest: dead service
    assert context_utils.get_agent_num_ctx("aifred", ollama_state, "model")[0] == OLLAMA_CONTEXT


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
        row("piper", needs_gpu=False), row("extra_engine"), row("qwen3local"), row("xtts"),
        listed=[dict(entry) for entry in DEFAULT_TTS_ESCALATION],
    )
    listed = tts_service.entries()
    assert [(e.engine, e.enabled, e.info is not None) for e in listed] == [
        ("qwen3local", True, True), ("xtts", True, True), ("dashscope_audio3", True, False),
        ("piper", True, True), ("edge", True, False), ("browser", True, False),
        ("extra_engine", False, True),   # offered, not named by the start list: behind it, switched off
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


def test_matched_list_is_what_a_change_by_the_user_saves(service) -> None:
    service(
        row("xtts"), row("xtts", "Box"), row("piper", needs_gpu=False), row("qwen3local", "Aragon"),
        listed=[stored("piper", enabled=False), stored("gone")],
    )
    rows = tts_service.catalog()
    assert tts_service.matched_list(rows) == [
        stored("piper", enabled=False), stored("gone"),
        stored("xtts", enabled=False), stored("xtts", "Box", enabled=False), stored("qwen3local", "Aragon", enabled=False),
    ]


def test_an_entry_without_catalog_row_is_listed_but_never_active(service) -> None:
    service(row("xtts"), listed=[stored("gone"), stored("xtts", "Nowhere"), stored("xtts")])
    listed = tts_service.entries()
    assert [e.info is None for e in listed] == [True, True, False]
    assert [e.active for e in listed] == [False, False, True]
    assert tts_service.request_engines() == [EngineRef("xtts", "sentence", None)]


def test_first_enabled_key_is_read_from_the_settings_alone(settings) -> None:
    settings.data["tts_escalation"] = [stored("piper", enabled=False), stored("xtts", "Box"), stored("edge")]
    assert tts_service.first_enabled_key() == "xtts"
    settings.data["tts_escalation"] = [stored("piper", enabled=False)]
    assert tts_service.first_enabled_key() is None


def test_stored_entries_need_no_service(settings) -> None:
    settings.data["tts_escalation"] = [stored("xtts"), stored("piper", enabled=False)]
    settings.data["tts_toggles_per_engine"] = {"piper": {"unit": "whole"}}
    # conftest points the default client at a dead address: only the settings are read
    assert [(e.engine, e.enabled, e.unit, e.info) for e in tts_service.stored_entries()] == [
        ("xtts", True, "", None), ("piper", False, "whole", None),
    ]


@pytest.mark.parametrize("broken", [
    {"tts_escalation": [{"engine": "xtts", "host": None}]},
    {"tts_escalation": [stored("xtts"), stored("xtts")]},
])
def test_a_broken_list_is_refused(broken: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        tts_service.check_stored_settings(broken)


def test_the_same_engine_on_another_machine_is_no_duplicate() -> None:
    tts_service.check_stored_settings({"tts_escalation": [stored("xtts"), stored("xtts", "Box")]})


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
        row("xtts"), row("qwen3local", "Box"), row("extra_engine", installed=False),
        row("piper", needs_gpu=False), row("edge", needs_gpu=False, cloud=True),
        listed=[stored("piper")],
    )
    assert tts_service.gpu_engine_keys() == {"xtts", "extra_engine"}
    assert tts_service.installed_gpu_engine_keys() == ["xtts"]
    assert tts_service.usable_engine_keys() == ["xtts", "qwen3local", "piper", "edge"]
    assert tts_service.engine_label("xtts", "de") == "xtts lang"
    assert tts_service.engine_label_short("xtts", "en") == "XTTS"
    assert tts_service.engine_key_for_label("xtts long") == "xtts"
    with pytest.raises(ValueError, match="names no TTS engine"):
        tts_service.engine_key_for_label("unknown")


def test_active_local_keys_follow_the_stored_list_without_the_service(settings) -> None:
    settings.data["tts_escalation"] = [
        stored("qwen3local", "Box"), stored("piper"), stored("xtts", enabled=False), stored("qwen3local"),
    ]
    # no service behind the client (conftest): nothing is asked
    assert tts_service.active_local_keys() == ["piper", "qwen3local"]


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


def test_planned_engine_is_the_topmost_active_local_entry_with_a_profile(settings, monkeypatch) -> None:
    from aifred.lib.tts_llm_coordination import planned_tts_engine

    monkeypatch.setattr("aifred.lib.calibration.has_llamaswap_tts_variant", lambda path, model_id, key: key == "xtts")
    settings.data["tts_escalation"] = [stored("qwen3local", "Aragon"), stored("qwen3local"), stored("xtts"), stored("piper")]
    assert planned_tts_engine("model") == "xtts"   # remote: never reserved; no calibrated profile for qwen3local
    settings.data["tts_escalation"][2]["enabled"] = False
    assert planned_tts_engine("model") == ""


def test_planning_does_not_need_the_service(settings, monkeypatch) -> None:
    """The service may be down (a restart, not installed): the plan reads settings.json and the llama-swap
    configuration only, and answers as ever. conftest points the client at a dead address."""
    from aifred.lib.tts_llm_coordination import planned_tts_engine

    monkeypatch.setattr("aifred.lib.calibration.has_llamaswap_tts_variant", lambda path, model_id, key: True)
    settings.data["tts_escalation"] = [stored("xtts")]
    assert planned_tts_engine("model") == "xtts"


def test_the_escalation_report_names_who_spoke_who_failed_and_the_change_of_voice(service) -> None:
    service(row("xtts"), row("qwen3local", "Box"))
    summary = SpeakSummary(
        trace=(
            TraceStep("xtts", "failed", "unreachable"),
            TraceStep("qwen3local@Box", "spoke"),
            TraceStep("piper", "skipped", "deactivated"),   # not in the catalog: named by its key
        ),
        switch="unreachable",
    )
    assert tts_service.escalation_report(summary, "Browser aifred") == [
        "🔊 [Browser aifred] TTS escalation: failed XTTS · local (unreachable)",
        "🔊 [Browser aifred] TTS escalation: QWEN3LOCAL · Box speaks",
        "🔊 [Browser aifred] TTS escalation: skipped piper (deactivated)",
        "🔊 [Browser aifred] TTS escalation: voice change announced (unreachable)",
    ]


def test_the_note_of_a_step_the_catalog_does_not_have_names_its_key(service) -> None:
    service(row("xtts"))
    note = tts_service.spoken_note((TraceStep("xtts", "spoke"), TraceStep("deactivated_engine", "spoke")), "en")
    assert note == "XTTS · local \u2192 deactivated_engine"


def test_the_note_of_the_browser_voice_needs_no_service() -> None:
    # conftest points the client at a dead address
    assert tts_service.browser_note("de") == "Browser · Gerät"
    assert tts_service.browser_note("en") == "Browser · device"
