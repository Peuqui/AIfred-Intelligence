"""Tests für aifred.lib.vision_gpu_select — agnostische GPU-Auswahl."""

from __future__ import annotations

import pytest

from aifred.lib import vision_gpu_select as vgs
from aifred.lib.vision_gpu_select import (
    GpuInfo,
    ollama_override_text,
    pick_tts_gpu,
    pick_vlm_gpu,
    resolve_gpu_id,
)


def _rtx8000(idx: int) -> GpuInfo:
    return GpuInfo(
        index=idx, name="Quadro RTX 8000", compute_capability=(7, 5),
        total_memory_mb=49152,
    )


def _v100(idx: int) -> GpuInfo:
    return GpuInfo(
        index=idx, name="Tesla V100-PCIE-32GB", compute_capability=(7, 0),
        total_memory_mb=32768,
    )


def _p40(idx: int) -> GpuInfo:
    return GpuInfo(
        index=idx, name="Tesla P40", compute_capability=(6, 1),
        total_memory_mb=24576,
    )


def _a100(idx: int, mem: int = 81920) -> GpuInfo:
    return GpuInfo(idx, "A100", (8, 0), mem)


class TestPickVlmGpu:
    def test_user_real_setup_2x_rtx_v100_2x_p40(self):
        """Reales Setup des Users — VLM auf die V100 (Idx 3). Nur eine
        Karte im Side-Channel-Tier (P40 per Floor raus), also teilt sich
        das VLM sie mit dem TTS."""
        gpus = [_rtx8000(0), _p40(1), _rtx8000(2), _v100(3), _p40(4)]
        assert pick_vlm_gpu(gpus) == 3  # V100

    def test_two_top_class_picks_second(self):
        gpus = [_rtx8000(0), _rtx8000(1)]
        assert pick_vlm_gpu(gpus) == 1

    def test_three_top_class_vlm_on_third(self):
        """3× gleiche Klasse: LLM behält die erste, die Side-Channels
        teilen sich die dritte (Sammelkarte), die zweite bleibt frei."""
        gpus = [_rtx8000(0), _rtx8000(1), _rtx8000(2)]
        assert pick_vlm_gpu(gpus) == 2

    def test_single_top_class_falls_to_next_best(self):
        """Wenn nur EINE RTX 8000 da ist, geht das VLM auf die V100 statt
        die einzige top-class zu blockieren."""
        gpus = [_rtx8000(0), _v100(1), _p40(2)]
        assert pick_vlm_gpu(gpus) == 1  # V100

    def test_single_top_with_one_lower_class(self):
        gpus = [_rtx8000(0), _p40(1)]
        assert pick_vlm_gpu(gpus) == 1  # P40 (soft-floor last resort)

    def test_only_one_gpu_returns_it(self):
        gpus = [_rtx8000(0)]
        assert pick_vlm_gpu(gpus) == 0

    def test_no_gpus_raises(self):
        with pytest.raises(RuntimeError, match="no CUDA GPU"):
            pick_vlm_gpu([])

    def test_equal_compute_different_mem_uses_mem_for_ordering(self):
        """Same compute_cap, but different memory — bigger goes first
        (LLM), VLM lands on the smaller one."""
        big = _a100(0, 81920)
        small = _a100(1, 40960)
        gpus = [small, big]  # input order shouldn't matter
        assert pick_vlm_gpu(gpus) == 1  # small A100

    def test_pci_index_breaks_ties(self):
        """Identical GPUs (cap + mem) — order by PCI index for reproducibility."""
        a = _rtx8000(2)
        b = _rtx8000(0)
        assert pick_vlm_gpu([a, b]) == 2  # b is LLM (idx 0), a is VLM (idx 2)


class TestPickTtsGpu:
    def test_user_real_setup_tts_on_v100(self):
        """One V100 in the tier → TTS co-locates with VLM on it."""
        gpus = [_rtx8000(0), _p40(1), _rtx8000(2), _v100(3), _p40(4)]
        assert pick_tts_gpu(gpus) == 3  # V100

    def test_two_v100_tier_shares_one_card(self):
        """Sammelkarte (2026-08-29): TTS und VLM teilen sich die zweite
        Tier-Karte — die erste bleibt für Backend-Topologien frei
        (z.B. TP2×PP2 bei der vLLM-Kalibration)."""
        gpus = [_rtx8000(0), _rtx8000(1), _v100(2), _v100(3), _p40(4)]
        assert pick_tts_gpu(gpus) == 3  # gemeinsame Sammelkarte
        assert pick_vlm_gpu(gpus) == 3  # dieselbe Karte

    def test_three_top_class_shared_on_third(self):
        """3× same class: LLM keeps the first, side channels share the
        third — the second stays free for the backend."""
        gpus = [_rtx8000(0), _rtx8000(1), _rtx8000(2)]
        assert pick_tts_gpu(gpus) == 2
        assert pick_vlm_gpu(gpus) == 2

    def test_single_gpu_tts_on_it(self):
        assert pick_tts_gpu([_rtx8000(0)]) == 0

    def test_no_gpus_raises(self):
        with pytest.raises(RuntimeError, match="no CUDA GPU"):
            pick_tts_gpu([])


class TestComputeFloor:
    def test_p40_excluded_when_volta_present(self):
        """Floor: a P40 is never a side-channel host while a Volta+ card
        sits in the tier. TTS and VLM both land on the V100, not the P40."""
        gpus = [_rtx8000(0), _v100(1), _p40(2), _p40(3)]
        assert pick_tts_gpu(gpus) == 1  # V100, not a P40
        assert pick_vlm_gpu(gpus) == 1  # V100 (only floored card)

    def test_p40_only_soft_fallback(self):
        """All-P40 host: floor finds nothing ≥ 7.0, so it falls back —
        LLM keeps the first P40, side channels share the third."""
        gpus = [_p40(0), _p40(1), _p40(2)]
        assert pick_tts_gpu(gpus) == 2  # gemeinsame Sammelkarte
        assert pick_vlm_gpu(gpus) == 2  # dieselbe Karte

    def test_rtx_plus_p40_soft_fallback_to_p40(self):
        """Top tier is the only Volta+ card → side-channels fall back to
        the P40 rather than invading the LLM's RTX 8000."""
        gpus = [_rtx8000(0), _p40(1)]
        assert pick_tts_gpu(gpus) == 1  # P40 (last resort)
        assert pick_vlm_gpu(gpus) == 1  # P40


class TestResolveGpuId:
    def setup_method(self):
        vgs.reset_cache()

    def test_int_passed_through(self):
        assert resolve_gpu_id(3) == 3

    def test_numeric_string_passed_through(self):
        assert resolve_gpu_id("7") == 7

    def test_none_returns_none(self):
        assert resolve_gpu_id(None) is None

    def test_auto_resolves_via_pick(self, monkeypatch):
        monkeypatch.setattr(vgs, "list_gpus", lambda: [_rtx8000(0), _rtx8000(2)])
        assert resolve_gpu_id("auto") == 2

    def test_auto_returns_none_on_no_gpu(self, monkeypatch):
        monkeypatch.setattr(vgs, "list_gpus", lambda: [])
        assert resolve_gpu_id("auto") is None

    def test_auto_is_cached(self, monkeypatch):
        calls = {"n": 0}

        def fake_list():
            calls["n"] += 1
            return [_rtx8000(0), _rtx8000(2)]

        monkeypatch.setattr(vgs, "list_gpus", fake_list)
        assert resolve_gpu_id("auto") == 2
        assert resolve_gpu_id("auto") == 2
        # list_gpus only called once
        assert calls["n"] == 1

    def test_reset_cache_forces_re_pick(self, monkeypatch):
        monkeypatch.setattr(vgs, "list_gpus", lambda: [_rtx8000(0), _rtx8000(2)])
        assert resolve_gpu_id("auto") == 2
        # Now "hardware change" — different layout
        monkeypatch.setattr(vgs, "list_gpus", lambda: [_v100(3)])
        # Without reset: still cached
        assert resolve_gpu_id("auto") == 2
        vgs.reset_cache()
        assert resolve_gpu_id("auto") == 3

    def test_invalid_string_returns_none(self):
        assert resolve_gpu_id("garbage") is None

    def test_case_insensitive_auto(self, monkeypatch):
        monkeypatch.setattr(vgs, "list_gpus", lambda: [_rtx8000(0), _rtx8000(2)])
        assert resolve_gpu_id("AUTO") == 2


class TestOllamaOverrideText:
    def test_pins_correct_device(self):
        text = ollama_override_text(2)
        assert "[Service]" in text
        assert 'CUDA_DEVICE_ORDER=PCI_BUS_ID' in text
        assert 'CUDA_VISIBLE_DEVICES=2' in text

    def test_includes_pci_bus_id_order(self):
        text = ollama_override_text(0)
        # PCI_BUS_ID is essential — without it the index would be remapped
        assert "PCI_BUS_ID" in text
