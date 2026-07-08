"""Context-maximizing refine (``_context_refine_swap``) vs. the fastest-first
cascade (``_refine_split_from_measurement``).

The cascade relieves the card with the least *absolute* free VRAM and can only
spill DOWNSTREAM. When the context-limiting card sits at the cascade tail (a P40
at a few MB but with a shallow KV slope), the cascade finds no downstream target
and gives up — capping the upward context push (the real 140.800-vs-114.944
Vigilantia gap). The context refine instead relieves the card whose free VRAM
runs out first as context grows and moves the layer to the highest-ceiling
destination, upstream if need be.
"""

from __future__ import annotations

import asyncio

import pytest

import aifred.lib.calibration.flow as flow
from aifred.lib.calibration.flow import (
    _binary_search_fitting_ctx,
    _context_refine_swap,
    _CtxSearchResult,
    _refine_split_from_measurement,
)
from aifred.lib.calibration.types import (
    GPU,
    Budget,
    Candidate,
    Model,
    VRamModel,
    VRamPoint,
)
from aifred.lib.calibration.verifier import VerifyResult

TOTAL_LAYERS = 61
MODEL_MB = 30000.0


@pytest.fixture(autouse=True)
def _clear_bias_cache():
    """The cross-variant bias cache is module-level; isolate every test."""
    flow._MODEL_BIAS_CACHE.clear()
    yield
    flow._MODEL_BIAS_CACHE.clear()


def _vmodel(split, slope_per_layer):
    """Build a VRamModel whose per-layer KV slope decomposes to
    ``slope_per_layer`` (see optimizer._per_gpu_coefficients)."""
    mb_per_layer = MODEL_MB / TOTAL_LAYERS
    slope_mb_per_tok = tuple(slope_per_layer[i] * split[i] for i in range(len(split)))
    intercept = tuple(split[i] * mb_per_layer + 300.0 for i in range(len(split)))
    return VRamModel(
        n_gpus=len(split), kv_quant="f16", ngl=99, tensor_split=tuple(split),
        intercept_mb=intercept, slope_mb_per_tok=slope_mb_per_tok,
        low_point=VRamPoint(2048, (0,) * len(split), (0,) * len(split)),
        high_point=VRamPoint(65536, (0,) * len(split), (0,) * len(split)),
    )


def _gpus_5():
    return [
        GPU("u0", "RTX 8000", 7.5, 49000, 0, speed_class=0, first_in_class=True),
        GPU("u1", "RTX 8000", 7.5, 49000, 0, speed_class=0, first_in_class=False),
        GPU("u2", "V100", 7.0, 32000, 0, speed_class=1, first_in_class=True),
        GPU("u3", "P40", 6.1, 24000, 0, speed_class=2, first_in_class=True),
        GPU("u4", "P40", 6.1, 24000, 0, speed_class=2, first_in_class=False),
    ]


def _budget(free_5):
    return Budget(
        per_gpu_free=tuple(free_5), first_gpu_handicap=500,
        safety_margin=192, gpu_reserve_mb=(),
    )


def test_cascade_deadends_on_tail_bottleneck():
    """Least-absolute-free card is the tail P40 → cascade has no downstream
    target and returns None (the give-up the fix targets)."""
    split = (17.0, 18.0, 8.0, 9.0, 9.0)
    gpus = _gpus_5()
    vmodel = _vmodel(split, [0.010, 0.006, 0.006, 0.006, 0.006])
    # GPU4 (tail) has the least absolute free; GPU0 the steepest slope.
    r = VerifyResult(fits=False, measured_free_mb=(500, 8000, 12000, 5000, 300),
                     thinks=None, detail="")
    out, _reason = _refine_split_from_measurement(
        split, gpus, r, _budget((20000, 20000, 18000, 14000, 14000)),
        vmodel, TOTAL_LAYERS, MODEL_MB, 120000,
    )
    assert out is None


def test_context_refine_relieves_ctx_limiter_where_cascade_fails():
    """Same scenario: the context refine relieves the true ctx-limiter
    (GPU0, steepest slope) and raises the measured ceiling."""
    split = (17.0, 18.0, 8.0, 9.0, 9.0)
    gpus = _gpus_5()
    vmodel = _vmodel(split, [0.010, 0.006, 0.006, 0.006, 0.006])
    r = VerifyResult(fits=False, measured_free_mb=(500, 8000, 12000, 5000, 300),
                     thinks=None, detail="")
    out, reason = _context_refine_swap(
        split, gpus, r, _budget((20000, 20000, 18000, 14000, 14000)),
        vmodel, TOTAL_LAYERS, MODEL_MB, 120000,
    )
    assert out is not None
    # A whole layer left the context-limiting card (GPU0).
    assert out[0] == split[0] - 1
    # Layer count is conserved.
    assert sum(out) == sum(split)
    assert "ctx-limiter" in reason


def test_context_refine_can_move_upstream():
    """When the ctx-limiter is the tail card, the destination is upstream —
    the move the downstream-only cascade structurally cannot make."""
    split = (16.0, 16.0, 10.0, 10.0, 9.0)
    gpus = _gpus_5()
    # GPU4 (tail) has the steepest slope → smallest free ÷ KV-slope → limiter.
    vmodel = _vmodel(split, [0.005, 0.005, 0.005, 0.005, 0.020])
    r = VerifyResult(fits=False, measured_free_mb=(9000, 9000, 12000, 9000, 250),
                     thinks=None, detail="")
    out, reason = _context_refine_swap(
        split, gpus, r, _budget((20000, 20000, 18000, 14000, 14000)),
        vmodel, TOTAL_LAYERS, MODEL_MB, 120000,
    )
    assert out is not None
    # The tail card was relieved …
    assert out[4] < split[4]
    # … onto an upstream card (index < 4), impossible for the cascade.
    moved_to = [i for i in range(5) if out[i] > split[i]]
    assert moved_to and all(i < 4 for i in moved_to)


def test_vlm_case_relieves_limiter_not_fast_sibling():
    """The reported Vigilantia VLM case (17:18:8:9:9): GPU2 is the reserve-
    loaded VLM GPU (low measured free). The swap must take from the ctx-limiter
    GPU0 — never from the fast sibling GPU1 (the 2026-07-07 bad move) — and must
    still raise the ceiling. This is what the old cascade + blanket lock missed.
    """
    split = (17.0, 18.0, 8.0, 9.0, 9.0)
    gpus = _gpus_5()
    vmodel = _vmodel(split, [0.010, 0.006, 0.006, 0.006, 0.006])
    # GPU0 steep+tight = ctx-limiter; GPU2 (VLM) reserve-reduced but not the
    # limiter; GPU1 (fast sibling) has comfortable headroom.
    r = VerifyResult(fits=False, measured_free_mb=(600, 9000, 3500, 8000, 8000),
                     thinks=None, detail="")
    out, reason = _context_refine_swap(
        split, gpus, r, _budget((20000, 20000, 18000, 14000, 14000)),
        vmodel, TOTAL_LAYERS, MODEL_MB, 120000,
        keep_active_set=True,  # VLM pins the GPU set
    )
    assert out is not None
    assert out[0] == split[0] - 1        # relieved the ctx-limiter GPU0 …
    assert out[1] >= split[1]            # … the fast sibling GPU1 is never the
                                         #     SOURCE (the 2026-07-07 bad move)
    assert sum(out) == sum(split)


def test_context_refine_respects_speed_lock():
    """keep_active_set=True must not activate an idle GPU (speed variant)."""
    split = (20.0, 20.0, 21.0, 0.0, 0.0)  # only 3 cards active
    gpus = _gpus_5()
    vmodel = _vmodel(
        (20.0, 20.0, 21.0, 1.0, 1.0),  # measured with all 5 to get slopes
        [0.010, 0.006, 0.006, 0.006, 0.006],
    )
    r = VerifyResult(fits=False, measured_free_mb=(300, 8000, 9000, 14000, 14000),
                     thinks=None, detail="")
    out, _reason = _context_refine_swap(
        split, gpus, r, _budget((20000, 20000, 18000, 14000, 14000)),
        vmodel, TOTAL_LAYERS, MODEL_MB, 120000, keep_active_set=True,
    )
    if out is not None:
        # Idle cards (3, 4) stay idle.
        assert out[3] == 0.0 and out[4] == 0.0


# ── _binary_search_fitting_ctx (Point 2+3: cost-model-seeded ctx search) ──

def _drain_search(**kwargs) -> _CtxSearchResult:
    async def run() -> _CtxSearchResult:
        res = None
        async for item in _binary_search_fitting_ctx(**kwargs):
            if isinstance(item, _CtxSearchResult):
                res = item
        assert res is not None
        return res
    return asyncio.run(run())


def _model():
    from pathlib import Path
    return Model(
        model_id="m", gguf_path=Path("/x.gguf"), native_context=262144,
        total_layers=TOTAL_LAYERS, size_mb=MODEL_MB,
        mb_per_layer=MODEL_MB / TOTAL_LAYERS, quantization="Q4",
    )


def _candidate(split, vram_model=None):
    return Candidate(
        mode="gpu", n_gpus=len([x for x in split if x > 0]), kv_quant="f16",
        ngl=99, tensor_split=tuple(split), max_context=200000,
        predicted_free_mb=(0,) * len(split), vram_model=vram_model,
    )


def test_binary_search_finds_highest_fitting_ctx(monkeypatch):
    """Down-search converges on the highest fitting ctx (256-precision) — the
    tight anchor that replaces base's 5 blind 10%-shrinks."""
    split = (17.0, 18.0, 8.0, 9.0, 9.0)
    threshold = 98304  # fits iff ctx <= threshold

    async def _fake_verify(*, context, **kw):
        if context <= threshold:
            return VerifyResult(True, (2000,) * 5, None, "fit")
        return VerifyResult(False, (100,) * 5, None, "oom")

    monkeypatch.setattr(flow, "verify", _fake_verify)
    res = _drain_search(
        current_split=split, candidate=_candidate(split), model=_model(),
        gpus=_gpus_5(), budget=_budget((20000,) * 5),
        full_cmd="--model x", port=1, env=None, probe_thinking=False,
        thinks_seen=None, status_prefix="t", lo=8192, hi=200000,
        iteration=0, initial_load_sig=None,
    )
    assert res.best_r is not None and res.best_r.fits
    assert res.best_ctx % 256 == 0
    assert threshold - 512 <= res.best_ctx <= threshold


def _drain_search_verbose(**kwargs):
    """Like _drain_search but also returns the yielded progress strings."""
    msgs: list[str] = []

    async def run():
        res = None
        async for item in _binary_search_fitting_ctx(**kwargs):
            if isinstance(item, _CtxSearchResult):
                res = item
            else:
                msgs.append(item)
        assert res is not None
        return res
    res = asyncio.run(run())
    return res, msgs


def test_seeded_bias_trusts_math_instead_of_crawling(monkeypatch):
    """With a real vram_model AND a seeded bias, the tight regime no longer
    forces pure bisection — the math is trusted (a 'math max' probe appears).
    This is the fixed-512-floor fix: previously 150-200 MB free < 512 meant
    'too tight' and the search bisected the whole way."""
    split = (17.0, 18.0, 9.0, 9.0, 8.0)  # sums to TOTAL_LAYERS (61)
    vmodel = _vmodel(split, [0.006, 0.006, 0.006, 0.006, 0.006])
    threshold = 150016  # fits iff ctx <= this (256-multiple)

    async def _fake_verify(*, context, **kw):
        if context <= threshold:
            return VerifyResult(True, (2000,) * 5, None, "fit")
        return VerifyResult(False, (150,) * 5, None, "oom")

    monkeypatch.setattr(flow, "verify", _fake_verify)
    res, msgs = _drain_search_verbose(
        current_split=split, candidate=_candidate(split, vram_model=vmodel),
        model=_model(), gpus=_gpus_5(), budget=_budget((30000,) * 5),
        full_cmd="--model x", port=1, env=None, probe_thinking=False,
        thinks_seen=None, status_prefix="t", lo=8192, hi=200000,
        iteration=0, initial_load_sig=None, initial_bias_mb=120,
    )
    assert res.best_r is not None and res.best_r.fits
    assert res.best_ctx <= threshold
    # The math was trusted at least once (not the old always-bisect crawl).
    assert any("math max" in m for m in msgs)


def test_no_model_still_bisects(monkeypatch):
    """Without a vram_model the math is meaningless, so the search must keep
    bisecting (no seeded/measured bias is trusted) — no regression."""
    split = (17.0, 18.0, 9.0, 9.0, 8.0)
    threshold = 98304

    async def _fake_verify(*, context, **kw):
        if context <= threshold:
            return VerifyResult(True, (2000,) * 5, None, "fit")
        return VerifyResult(False, (150,) * 5, None, "oom")

    monkeypatch.setattr(flow, "verify", _fake_verify)
    res, msgs = _drain_search_verbose(
        current_split=split, candidate=_candidate(split, vram_model=None),
        model=_model(), gpus=_gpus_5(), budget=_budget((30000,) * 5),
        full_cmd="--model x", port=1, env=None, probe_thinking=False,
        thinks_seen=None, status_prefix="t", lo=8192, hi=200000,
        iteration=0, initial_load_sig=None, initial_bias_mb=0,
    )
    assert res.best_r is not None and res.best_r.fits
    assert res.best_ctx <= threshold
    assert not any("math max" in m for m in msgs)


def test_cross_variant_bias_cache_carries_to_next_variant(monkeypatch):
    """Point 2 / cross-engine: variant 1 measures the model+hardware bias and
    caches it; variant 2 (same model+GPUs) then trusts the math from probe 1
    instead of re-learning it (never hits the 'unmeasured bias' floor)."""
    split = (17.0, 18.0, 9.0, 9.0, 8.0)
    vmodel = _vmodel(split, [0.006] * 5)
    threshold = 150016

    async def _fake_verify(*, context, **kw):
        if context <= threshold:
            return VerifyResult(True, (2000,) * 5, None, "fit")
        return VerifyResult(False, (150,) * 5, None, "oom")

    monkeypatch.setattr(flow, "verify", _fake_verify)
    common = dict(
        current_split=split, candidate=_candidate(split, vram_model=vmodel),
        model=_model(), gpus=_gpus_5(), budget=_budget((30000,) * 5),
        full_cmd="--model x", port=1, env=None, probe_thinking=False,
        thinks_seen=None, status_prefix="v", lo=8192, hi=200000,
        iteration=0, initial_load_sig=None, initial_bias_mb=0,
    )
    # Variant 1: no seed, no cache — learns and caches the bias.
    res1, _ = _drain_search_verbose(**common)
    assert res1.best_r is not None and res1.best_r.fits
    assert any(v > 0 for v in flow._MODEL_BIAS_CACHE.values())

    # Variant 2: same model+GPUs, still initial_bias_mb=0 — the cache seeds it,
    # so the search never falls into the unmeasured-bias floor.
    res2, msgs2 = _drain_search_verbose(**common)
    assert res2.best_r is not None and res2.best_r.fits
    assert not any("unmeasured bias" in m for m in msgs2)
    assert any("math max" in m for m in msgs2)


def test_binary_search_stops_on_ctx_independent_load_death(monkeypatch):
    """A load that dies with the same per-GPU minimum regardless of ctx is
    ctx-independent — the search must stop, not re-run the minutes-long load."""
    split = (17.0, 18.0, 8.0, 9.0, 9.0)
    sig = (100, 100, 100, 100, 100)
    probes = {"n": 0}

    async def _fake_verify(*, context, **kw):
        probes["n"] += 1
        # No measurement (load death) with a constant load minimum.
        return VerifyResult(False, (), None, "segfault", load_min_free_mb=sig)

    monkeypatch.setattr(flow, "verify", _fake_verify)
    res = _drain_search(
        current_split=split, candidate=_candidate(split), model=_model(),
        gpus=_gpus_5(), budget=_budget((20000,) * 5),
        full_cmd="--model x", port=1, env=None, probe_thinking=False,
        thinks_seen=None, status_prefix="t", lo=8192, hi=200000,
        iteration=0, initial_load_sig=sig,
    )
    assert res.best_r is None
    assert probes["n"] == 1  # stopped after the first identical load death
