"""TTS burn-in through the service: peak = maximum of the catalog's VRAM samples, release at the
end, no number from a burn-in whose syntheses all failed. The service is a fake; no container runs."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

import aifred.lib.config as config
from aifred.lib import tts_stress_burnin
from aifred.lib.tts_stress_burnin import stress_burnin_tts
from tests.fake_tts_service import FakeTTSService, audio_frame, catalog_row, install_fake_service, summary_frame

GOOD_READING = audio_frame(b"RIFF") + summary_frame()


class VramFake(FakeTTSService):
    """``vram``: the VRAM map /engines reports on each call, in order; the last one repeats."""

    def __init__(self, catalog: list[dict[str, Any]], vram: list[Any]) -> None:
        super().__init__(catalog)
        self.vram = vram
        self.catalog_reads = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/engines":
            sample = self.vram[min(self.catalog_reads, len(self.vram) - 1)]
            self.catalog_reads += 1
            rows = [{**row, "vram_mib": sample} for row in self.catalog]
            return httpx.Response(200, json=rows)
        return super().handler(request)


@pytest.fixture(autouse=True)
def _fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TTS_BURNIN_VRAM_POLL_S", 0.01)


def install(monkeypatch: pytest.MonkeyPatch, tmp_path: Any, fake: FakeTTSService) -> None:
    install_fake_service(monkeypatch, tmp_path, fake)


def run(engine: str = "xtts", iterations: int = 2) -> int | None:
    return asyncio.run(stress_burnin_tts(engine, iterations=iterations))


def test_the_peak_is_the_maximum_of_the_catalog_samples_summed_over_cards(monkeypatch, tmp_path) -> None:
    fake = VramFake([catalog_row("xtts")], [{}, {"GPU-a": 5000, "GPU-b": 200}, {"GPU-a": 4000}])
    # read 0 = the burn-in's look at the catalog; read 1 = first sample (the spike); the rest settles lower
    fake.script = [GOOD_READING]
    install(monkeypatch, tmp_path, fake)

    assert run() == 5200
    assert len(fake.speaks) == 2


def test_the_syntheses_go_through_the_service_on_this_machine_as_a_whole_text(monkeypatch, tmp_path) -> None:
    fake = VramFake([catalog_row("xtts")], [{"GPU-a": 100}])
    fake.script = [GOOD_READING]
    install(monkeypatch, tmp_path, fake)

    run(iterations=2)

    assert [head["language"] for head in fake.speaks] == ["de", "en"]
    for head in fake.speaks:
        assert head["engines"] == [{"engine": "xtts", "unit": "whole", "host": None}]
        assert head["paragraph_pause_ms"] == 0


def test_the_syntheses_carry_the_voice_of_the_burnin_agent(monkeypatch, tmp_path) -> None:
    """The service has no default voice for xtts/qwen3local: without a voice every synthesis would fail."""
    fake = VramFake([catalog_row("xtts")], [{"GPU-a": 100}])
    fake.script = [GOOD_READING]
    install(monkeypatch, tmp_path, fake)
    monkeypatch.setattr(config, "TTS_BURNIN_AGENT", "aifred")
    monkeypatch.setattr(
        tts_stress_burnin, "voices_for",
        lambda engines, agent: {engines[0].engine: {"voice": f"voice of {agent}", "speed": 1.0, "pitch": 1.0}},
    )

    run(iterations=1)

    assert fake.speaks[0]["voices"] == {"xtts": {"voice": "voice of aifred", "speed": 1.0, "pitch": 1.0}}


def test_the_container_is_released_at_the_end(monkeypatch, tmp_path) -> None:
    fake = VramFake([catalog_row("xtts")], [{"GPU-a": 100}])
    fake.script = [GOOD_READING]
    install(monkeypatch, tmp_path, fake)

    run()

    assert fake.releases == [[{"engine": "xtts", "host": None}]]


def test_all_syntheses_failed_gives_no_number_but_still_releases(monkeypatch, tmp_path) -> None:
    fake = VramFake([catalog_row("xtts")], [{"GPU-a": 900}])
    fake.script = [503]
    install(monkeypatch, tmp_path, fake)

    assert run() is None
    assert fake.releases == [[{"engine": "xtts", "host": None}]]


def test_a_reading_without_audio_is_no_success(monkeypatch, tmp_path) -> None:
    fake = VramFake([catalog_row("xtts")], [{"GPU-a": 900}])
    fake.script = [summary_frame()]
    install(monkeypatch, tmp_path, fake)

    assert run() is None


def test_one_failed_synthesis_among_successes_still_measures(monkeypatch, tmp_path) -> None:
    fake = VramFake([catalog_row("xtts")], [{"GPU-a": 800}])
    fake.script = [503, GOOD_READING]
    install(monkeypatch, tmp_path, fake)

    assert run() == 800


def test_a_vram_the_service_could_not_measure_fails_the_burnin_and_releases(monkeypatch, tmp_path) -> None:
    fake = VramFake([catalog_row("xtts")], [None])
    fake.script = [GOOD_READING]
    install(monkeypatch, tmp_path, fake)

    assert run() is None
    assert fake.releases == [[{"engine": "xtts", "host": None}]]


def test_an_engine_without_local_gpu_needs_no_burnin(monkeypatch, tmp_path) -> None:
    fake = VramFake([catalog_row("piper", needs_gpu=False)], [{}])
    install(monkeypatch, tmp_path, fake)

    assert run("piper") == 0
    assert fake.speaks == []
    assert fake.releases == []


def test_a_not_installed_engine_is_no_burnin(monkeypatch, tmp_path) -> None:
    fake = VramFake([catalog_row("xtts", installed=False)], [{}])
    install(monkeypatch, tmp_path, fake)

    assert run() is None
    assert fake.speaks == []


def test_an_engine_the_service_does_not_offer_is_no_burnin(monkeypatch, tmp_path) -> None:
    fake = VramFake([catalog_row("xtts")], [{}])
    install(monkeypatch, tmp_path, fake)

    assert run("extra_engine") is None


def test_an_unreachable_service_fails_the_burnin(monkeypatch) -> None:
    # conftest points the default client at a dead address and forbids systemctl
    import aifred.lib.tts_client as tts_client

    monkeypatch.setattr(tts_client.TTSClient, "ensure_ready", lambda self, timeout_s=0: None)

    assert run() is None


def test_the_cli_helpers_of_the_old_registry_are_gone() -> None:
    assert not hasattr(tts_stress_burnin, "_query_gpu_used_mb")
    assert not hasattr(tts_stress_burnin, "_resolve_tts_gpu_index")
