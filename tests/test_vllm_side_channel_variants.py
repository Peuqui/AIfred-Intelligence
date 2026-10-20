"""Profile fuer TTS und VLM aus einer kalibrierten vLLM-Basis (Weg A, 20.10.2026).

Freigemacht wird in fester Reihenfolge: freie Karte, gemessener Rest auf einer
Modellkarte, kleinerer KV-Cache (bis ein voller Kontext), GMU (nur PLE),
kleinerer Kontext.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aifred.lib.calibration import vllm_flow
from aifred.lib.calibration.vllm_flow import (
    LOWER_GMU,
    MEASURED_HEADROOM,
    SMALLER_CACHE,
    SMALLER_CONTEXT,
    SPARE_CARD,
    KvMeasurement,
    plan_side_channels,
)
from aifred.lib.calibration.vllm_probe import VllmSpec
from aifred.lib.config import SIDE_CHANNEL_RESIDUAL_MB as RESIDUAL

MIB = 1024 * 1024
MML = 262144
TOTAL = {0: 49152, 1: 32768, 2: 49152, 3: 32768, 4: 32768}


def _kv(free: dict[int, int], model_cards: list[int], blocks: int = 10000,
        one_context: int = 4000) -> KvMeasurement:
    """Messung der Basis: ein Block belegt 1 MiB auf jeder Modellkarte."""
    return KvMeasurement(blocks=blocks, blocks_budget=blocks, blocks_one_context=one_context,
                         fill_tokens=MML - 1000, bytes_per_block={c: MIB for c in model_cards},
                         free_under_load_mb=free)


BASE = VllmSpec(checkpoint=Path("."), served_name="m", gpu_ids=[0, 2, 1, 3], pp=4,
                gmu=0.95, mml=MML, num_gpu_blocks=10000)


def _plan(kv: KvMeasurement, tts_mb: int, vlm_card: int | None = None, vlm_mb: int = 0,
          ple: bool = False):
    return plan_side_channels(kv, BASE, tts_mb=tts_mb, vlm_card=vlm_card, vlm_mb=vlm_mb,
                              total_mb=TOTAL, ple=ple)


def test_a_card_outside_the_topology_takes_both_side_channels() -> None:
    # Flash-Next PP4 laesst GPU 4 frei: TTS und VLM gehen beide dorthin
    kv = _kv({0: 900, 1: 700, 2: 900, 3: 700, 4: 32000}, model_cards=[0, 1, 2, 3])
    plan, needs = _plan(kv, tts_mb=6000, vlm_card=4, vlm_mb=5000)
    assert (plan.step, plan.tts_card, plan.blocks) == (SPARE_CARD, 4, 10000)
    assert needs == {4: 6000 + 5000 + RESIDUAL}     # ein Restpuffer je Karte, nicht je Modell
    assert not plan.needs_boot


def test_measured_headroom_on_a_model_card_is_used_without_a_boot() -> None:
    # Alle Karten tragen das Modell; GPU 0 hat nach der laengsten Anfrage 9 GB frei
    kv = _kv({0: 9000, 1: 730, 2: 7000, 3: 730, 4: 730}, model_cards=[0, 1, 2, 3, 4])
    plan, needs = _plan(kv, tts_mb=6000)
    assert (plan.step, plan.tts_card, plan.blocks) == (MEASURED_HEADROOM, 0, 10000)
    assert needs == {0: 6000 + RESIDUAL}


def test_the_cache_shrinks_on_the_card_that_costs_the_fewest_blocks() -> None:
    kv = _kv({0: 2000, 1: 500, 2: 3000, 3: 500}, model_cards=[0, 1, 2, 3])
    plan, _ = _plan(kv, tts_mb=6000)
    # GPU 2 fehlen 3512 MiB, GPU 0 fehlen 4512 — GPU 2 kostet weniger Bloecke
    assert (plan.step, plan.tts_card) == (SMALLER_CACHE, 2)
    assert plan.blocks == 10000 - (6000 + RESIDUAL - 3000)
    assert plan.needs_boot


def test_below_one_full_context_the_context_shrinks() -> None:
    kv = _kv({0: 2000, 1: 500}, model_cards=[0, 1], one_context=8000)
    plan, _ = _plan(kv, tts_mb=6000)
    blocks = 10000 - (6000 + RESIDUAL - 2000)
    assert (plan.step, plan.blocks) == (SMALLER_CONTEXT, blocks)
    assert plan.mml == (MML * blocks // 8000) // 1024 * 1024


def test_a_ple_model_lowers_its_gmu_before_the_context() -> None:
    kv = _kv({0: 2000, 1: 500}, model_cards=[0, 1], one_context=8000)
    plan, _ = _plan(kv, tts_mb=6000, ple=True)
    # Cache auf einen Kontext (2000 MiB frei), den Rest (2512 MiB von 49152) die GMU
    assert (plan.step, plan.blocks, plan.mml) == (LOWER_GMU, 8000, MML)
    assert plan.gmu == pytest.approx(0.95 - 0.06)


def test_nothing_fits_when_only_cards_without_the_model_are_short() -> None:
    # Keine Modellkarte kann fuer die TTS Platz machen, und die VLM-Karte ist
    # eine fremde, volle Karte — das haengt nicht an diesem Profil
    kv = _kv({0: 100, 4: 1000}, model_cards=[0])
    assert _plan(kv, tts_mb=0, vlm_card=4, vlm_mb=5000) is None


def _install_base(monkeypatch, kv: KvMeasurement) -> list[tuple[str, dict]]:
    """Basisprofil vorgeben, Schreiben und Karten abfangen; gibt die geschriebenen Profile zurueck."""
    from aifred.lib import model_vram_cache, operating_points

    base_meta = {"source": "AIfred vLLM auto-calibration", "throughput_tok_s": 40.0,
                 "kv": kv.as_meta(), "spec": vllm_flow.spec_meta(BASE)}
    monkeypatch.setattr(operating_points, "get_operating_point",
                        lambda name: {"llamaswap": {"cmd": "x"}, "meta": base_meta})
    monkeypatch.setattr(vllm_flow, "_smi_index_by_uuid", lambda: {f"G{i}": i for i in TOTAL})
    monkeypatch.setattr(vllm_flow, "drain_gpus", lambda progress, cancel_check: [
        type("G", (), {"uuid": f"G{i}", "total_mb": mb})() for i, mb in TOTAL.items()])
    monkeypatch.setattr(vllm_flow, "analyze_checkpoint",
                        lambda path: type("M", (), {"ple_bytes": 0})())
    monkeypatch.setattr(model_vram_cache, "record_calibration_failure", lambda *a, **k: True)
    monkeypatch.setattr(model_vram_cache, "clear_calibration_failure", lambda name: None)
    written: list[tuple[str, dict]] = []

    def fake_write(spec, meta_block):
        written.append((spec.served_name, {**meta_block, "blocks": spec.num_gpu_blocks}))
        return Path(f"/tmp/{spec.served_name}.yaml")

    monkeypatch.setattr(vllm_flow, "_write_profile", fake_write)
    return written


def test_spare_card_variant_is_the_base_under_its_own_name(monkeypatch, tmp_path) -> None:
    kv = _kv({0: 900, 1: 700, 2: 900, 3: 700, 4: 32000}, model_cards=[0, 1, 2, 3])
    written = _install_base(monkeypatch, kv)
    monkeypatch.setattr(vllm_flow, "measure_under_load",
                        lambda *a, **k: pytest.fail("a spare card needs no boot"))
    path = vllm_flow.calibrate_vllm_side_channel_variant(
        "m", "m-tts-xtts", tts_mb=6000, vlm_card=None, vlm_mb=0,
        log_dir=tmp_path, progress=lambda m: None)
    assert path == Path("/tmp/m-tts-xtts.yaml")
    name, meta = written[0]
    assert (name, meta["blocks"], meta["derived_from"]) == ("m-tts-xtts", 10000, "m")
    assert meta["side_channels"]["step"] == SPARE_CARD
    assert meta["side_channels"]["tts_card"] == 4


def test_a_booted_variant_is_measured_and_shrunk_once_more_when_short(
    monkeypatch, tmp_path,
) -> None:
    kv = _kv({0: 2000, 1: 500}, model_cards=[0, 1])
    written = _install_base(monkeypatch, kv)
    booted: list[int] = []

    def fake_measure(spec, log_dir, progress, cancel_check, *, policy):
        assert policy == "keep"               # das Profil verkleinert den Cache absichtlich
        booted.append(spec.num_gpu_blocks)
        # Erste Messung: 300 MiB weniger frei als vorhergesagt
        gained = 10000 - spec.num_gpu_blocks - (300 if len(booted) == 1 else 0)
        return spec, _kv({0: 2000 + gained, 1: 500 + gained}, model_cards=[0, 1],
                         blocks=spec.num_gpu_blocks)

    monkeypatch.setattr(vllm_flow, "measure_under_load", fake_measure)
    vllm_flow.calibrate_vllm_side_channel_variant(
        "m", "m-tts-xtts", tts_mb=6000, vlm_card=None, vlm_mb=0,
        log_dir=tmp_path, progress=lambda m: None)
    first = 10000 - (6000 + RESIDUAL - 2000)
    assert booted == [first, first - 300]
    assert written[0][1]["blocks"] == first - 300
    assert written[0][1]["side_channels"]["step"] == SMALLER_CACHE
