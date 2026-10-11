"""Adapter tts_service: Statusabbildung, Ort, Zwischenspeicher, Schreibaufrufe, Dienst nicht erreichbar.
Der Dienst ist ein httpx.MockTransport; der echte wird nie getroffen (conftest)."""

from typing import Any, Callable

import httpx
import pytest

import aifred.lib.tts_service as tts_service
from aifred.lib.config import TTS_SUITE_CACHE_TTL_S
from aifred.lib.tts_client import EngineInfo, EngineInUse, TTSClient, TTSSuiteUnreachable


def info(engine: str = "xtts", host: str | None = None, **fields: Any) -> dict[str, Any]:
    base = {
        "engine": engine, "host": host, "label": {"de": f"{engine} lang", "en": f"{engine} long"},
        "label_short": {"de": engine.upper(), "en": engine.upper()}, "enabled": True, "deactivated": False,
        "status": "stopped", "unit": "sentence", "unit_default": "sentence", "needs_gpu": True,
        "cloud": False, "browser": False, "installed": True, "start_hint": None, "vram_mib": {},
    }
    return {**base, **fields}


class FakeService:
    def __init__(self, listed: list[dict[str, Any]]) -> None:
        self.listed = listed
        self.requests: list[tuple[str, str]] = []
        self.stop_error: dict[str, str] | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        if request.url.path == "/engines":
            return httpx.Response(200, json=self.listed)
        if request.url.path.endswith("/stop") and self.stop_error:
            return httpx.Response(409, json=self.stop_error)
        if request.url.path == "/engines/reload":
            return httpx.Response(200, json={"removed": [], "added": []})
        if request.url.path == "/config":
            return httpx.Response(200, json={"entries": []})
        return httpx.Response(204)

    def reads(self) -> int:
        return self.requests.count(("GET", "/engines"))


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeService]:
    clock = {"now": 100.0}
    monkeypatch.setattr(tts_service, "monotonic", lambda: clock["now"])

    def make(*listed: dict[str, Any]) -> FakeService:
        fake = FakeService(list(listed))
        client = TTSClient("http://tts.test", "token", transport=httpx.MockTransport(fake.handler))
        monkeypatch.setattr(tts_service, "get_tts_client", lambda: client)
        tts_service.reset_cache()
        fake.clock = clock  # type: ignore[attr-defined]
        return fake

    return make


def engine_info(**fields: Any) -> EngineInfo:
    return EngineInfo(**{**info(), **fields})


@pytest.mark.parametrize(("fields", "key"), [
    ({"deactivated": True}, "engine_deactivated"),
    ({"enabled": False}, "disabled"),
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
def test_status_key(fields: dict[str, Any], key: str) -> None:
    assert tts_service.status_key(engine_info(**fields)) == key


@pytest.mark.parametrize(("fields", "place"), [
    ({"host": "Aragon"}, "Aragon"),
    ({"deactivated": True}, "deaktiviert"),
    ({"browser": True, "needs_gpu": False}, "Gerät"),
    ({"cloud": True, "needs_gpu": False}, "Cloud"),
    ({}, "lokal"),
    ({"needs_gpu": False}, "CPU"),
])
def test_location_label_and_entry_label(fields: dict[str, Any], place: str) -> None:
    entry = engine_info(**fields)
    assert tts_service.location_label(entry, "de") == place
    assert tts_service.entry_label(entry, "de") == f"XTTS · {place}"


def test_keys_units_labels_and_filters(service) -> None:
    service(
        info("xtts", unit="paragraph"), info("qwen3local", "Box"), info("moss", installed=False),
        info("piper", needs_gpu=False), info("edge", needs_gpu=False, cloud=True), info("fish", deactivated=True),
    )
    assert tts_service.gpu_engine_keys() == {"xtts", "moss"}
    assert tts_service.installed_gpu_engine_keys() == ["xtts"]
    assert tts_service.usable_engine_keys() == ["xtts", "qwen3local", "piper", "edge"]
    assert tts_service.speech_unit_for("xtts") == "paragraph"
    assert tts_service.engine_label("xtts", "de") == "xtts lang"
    assert tts_service.engine_label_short("xtts", "en") == "XTTS"
    assert tts_service.engine_key_for_label("xtts long") == "xtts"
    assert tts_service.engine_key_for_label("unknown") == "unknown"
    with pytest.raises(ValueError, match="not in the service's list"):
        tts_service.speech_unit_for("nope")


def test_first_enabled_entry_skips_disabled_and_deactivated(service) -> None:
    service(info("a", enabled=False), info("b", deactivated=True), info("c"))
    first = tts_service.first_enabled_entry()
    assert first is not None and first.engine == "c"


def test_running_engine_and_footprint_use_the_live_state(service) -> None:
    fake = service(info("xtts", status="running", vram_mib={"GPU-a": 3000}), info("qwen3local", "Box", status="running"))
    assert tts_service.running_gpu_engine() == "xtts"
    assert tts_service.local_gpu_footprint() == {"xtts": {"GPU-a": 3000}}
    assert fake.reads() == 2  # neither answer came from the cache


def test_unmeasured_footprint_is_an_error(service) -> None:
    service(info("xtts", status="running", vram_mib=None))
    with pytest.raises(tts_service.TTSVramUnknown):
        tts_service.local_gpu_footprint()


def test_the_list_is_cached_for_the_ttl_only(service) -> None:
    fake = service(info())
    tts_service.entries()
    tts_service.entries()
    assert fake.reads() == 1
    fake.clock["now"] += TTS_SUITE_CACHE_TTL_S + 0.1  # type: ignore[attr-defined]
    tts_service.entries()
    assert fake.reads() == 2


@pytest.mark.parametrize("write", [
    lambda: tts_service.start_entry("xtts"),
    lambda: tts_service.stop_entry("xtts"),
    lambda: tts_service.reload_engines(),
    lambda: tts_service.set_config({"entries": []}),
])
def test_writing_calls_clear_the_cache(service, write: Callable[[], object]) -> None:
    fake = service(info())
    tts_service.entries()
    write()
    tts_service.entries()
    assert fake.reads() == 2


def test_a_stop_refused_because_the_engine_is_in_use_is_reported(service) -> None:
    fake = service(info())
    fake.stop_error = {"error": "engine_in_use", "detail": "reading runs"}
    ok, message = tts_service.stop_engine("xtts")
    assert not ok and "in use" in message and "reading runs" in message
    with pytest.raises(EngineInUse):
        tts_service.stop_entry("xtts")


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
        info("qwen3local", "Aragon"),          # remote: never reserved
        info("qwen3local"),                    # no calibrated profile
        info("xtts"),
        info("piper", needs_gpu=False),
    )
    assert planned_tts_engine("model") == "xtts"
    fake.listed[2]["enabled"] = False
    tts_service.reset_cache()
    assert planned_tts_engine("model") == ""


def test_planning_with_an_unreachable_service_is_an_error_not_an_empty_plan() -> None:
    from aifred.lib.tts_llm_coordination import planned_tts_engine

    with pytest.raises(TTSSuiteUnreachable):
        planned_tts_engine("model")
