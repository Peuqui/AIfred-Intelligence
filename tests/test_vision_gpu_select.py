"""Tests für aifred.lib.vision_gpu_select — agnostische GPU-Auswahl."""

from __future__ import annotations

import pytest

from aifred.lib import vision_gpu_select as vgs
from aifred.lib.vision_gpu_select import (
    GpuInfo,
    ollama_override_text,
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


class TestPickVlmGpu:
    def test_user_real_setup_2x_rtx_v100_2x_p40(self):
        """Genau das Setup des Users — sollte die zweite RTX 8000 (Idx 2) wählen."""
        gpus = [_rtx8000(0), _p40(1), _rtx8000(2), _v100(3), _p40(4)]
        assert pick_vlm_gpu(gpus) == 2

    def test_two_top_class_picks_second(self):
        gpus = [_rtx8000(0), _rtx8000(1)]
        assert pick_vlm_gpu(gpus) == 1

    def test_three_top_class_still_picks_second(self):
        gpus = [_rtx8000(0), _rtx8000(1), _rtx8000(2)]
        assert pick_vlm_gpu(gpus) == 1

    def test_single_top_class_falls_to_next_best(self):
        """Wenn nur EINE RTX 8000 da ist, geht das VLM auf die V100 statt
        die einzige top-class zu blockieren."""
        gpus = [_rtx8000(0), _v100(1), _p40(2)]
        assert pick_vlm_gpu(gpus) == 1  # V100

    def test_single_top_with_one_lower_class(self):
        gpus = [_rtx8000(0), _p40(1)]
        assert pick_vlm_gpu(gpus) == 1  # P40 (next-best)

    def test_only_one_gpu_returns_it(self):
        gpus = [_rtx8000(0)]
        assert pick_vlm_gpu(gpus) == 0

    def test_no_gpus_raises(self):
        with pytest.raises(RuntimeError, match="no CUDA GPU"):
            pick_vlm_gpu([])

    def test_equal_compute_different_mem_uses_mem_for_ordering(self):
        """Same compute_cap, but different memory — bigger goes first;
        second-best is the smaller one."""
        big = GpuInfo(0, "A100-80GB", (8, 0), 81920)
        small = GpuInfo(1, "A100-40GB", (8, 0), 40960)
        gpus = [small, big]  # input order shouldn't matter
        assert pick_vlm_gpu(gpus) == 1  # small A100

    def test_pci_index_breaks_ties(self):
        """Identical GPUs (cap + mem) — order by PCI index for reproducibility."""
        a = _rtx8000(2)
        b = _rtx8000(0)
        assert pick_vlm_gpu([a, b]) == 2  # b is "first" (idx 0), a is "second" (idx 2)


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
