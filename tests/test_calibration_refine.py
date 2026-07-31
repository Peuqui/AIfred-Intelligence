"""Context-maximizing refine (``_context_refine_swap``) vs. the fastest-first
cascade (``_refine_split_from_measurement``).

The cascade relieves the card with the least *absolute* free VRAM and spills
DOWNSTREAM first. When the bottleneck sits at the cascade tail (the 397B
8b-combo dead-end on CUDA4), the shared destination SSOT
(``_cascade_destination``) now falls back UPSTREAM to the highest-headroom
card instead of giving up into a ctx-shrink. The context refine instead
relieves the card whose free VRAM runs out first as context grows and moves
the layer to the highest-ceiling destination, upstream if need be.
"""

from __future__ import annotations

import asyncio

import pytest

import aifred.lib.calibration.ctx_search as ctx_search
import aifred.lib.calibration.fit_math as fit_math
import aifred.lib.calibration.split_refine as split_refine
from aifred.lib.calibration.ctx_search import (
    _binary_search_fitting_ctx,
    _CtxSearchResult,
    _known_ctx_ceiling,
)
from aifred.lib.calibration.split_refine import (
    _context_refine_swap,
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
    fit_math._MODEL_BIAS_CACHE.clear()
    yield
    fit_math._MODEL_BIAS_CACHE.clear()


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


def test_cascade_falls_back_upstream_on_tail_bottleneck():
    """Least-absolute-free card is the tail P40 → no downstream target; the
    upstream fallback relieves it onto the highest-headroom card instead of
    the old dead-end (None → ctx-shrink, the 397B 8b-combo loss)."""
    split = (17.0, 18.0, 8.0, 9.0, 9.0)
    gpus = _gpus_5()
    vmodel = _vmodel(split, [0.010, 0.006, 0.006, 0.006, 0.006])
    # GPU4 (tail) has the least absolute free; GPU0 the steepest slope.
    r = VerifyResult(fits=False, measured_free_mb=(500, 8000, 12000, 5000, 300),
                     thinks=None, detail="")
    out, reason = _refine_split_from_measurement(
        split, gpus, r, _budget((20000, 20000, 18000, 14000, 14000)),
        vmodel, TOTAL_LAYERS, MODEL_MB, 120000,
    )
    assert out is not None
    # The tail bottleneck was relieved …
    assert out[4] == split[4] - 1
    # … onto the upstream card with the most headroom (GPU2, 12000 MB free).
    assert out[2] == split[2] + 1
    assert sum(out) == sum(split)
    assert "CUDA2" in reason


def test_cascade_prefers_idle_upstream_over_downstream():
    """Single-GPU model on slot 1 (second RTX) OOMs; slot 0 (first RTX) is
    completely idle. The overflow layer must land on the idle faster card,
    not cascade down onto the V100 (the 35B single-GPU case, 2026-07-31).
    Tested at the SSOT directly — the real case runs via the blind-shift
    path, which shares this destination choice."""
    dest = split_refine._cascade_destination(
        src=1, layers=[0.0, 41.0, 0.0, 0.0, 0.0],
        free_estimate=(48000, 200, 32000, 32000, 32000),
        layer_cost_per_gpu=(1000.0,) * 5,
        min_free_mb=192, step=1.0, keep_active_set=False,
    )
    assert dest == 0
    # Without a free estimate the idle-upstream shortcut must NOT guess —
    # fall back to the plain downstream rule.
    dest_blind = split_refine._cascade_destination(
        src=1, layers=[0.0, 41.0, 0.0, 0.0, 0.0],
        free_estimate=(),
        layer_cost_per_gpu=(1000.0,) * 5,
        min_free_mb=192, step=1.0, keep_active_set=False,
    )
    assert dest_blind == 2


def test_cascade_idle_upstream_not_activated_for_speed_variant():
    """keep_active_set (speed variant) must still never activate an idle
    card — even the idle-upstream shortcut respects it."""
    split = (0.0, 41.0, 2.0, 0.0, 0.0)
    gpus = _gpus_5()
    vmodel = _vmodel(split, [0.006] * 5)
    r = VerifyResult(fits=False, measured_free_mb=(48000, 200, 25000, 32000, 32000),
                     thinks=None, detail="")
    out, _reason = _refine_split_from_measurement(
        split, gpus, r, _budget((48000, 48000, 32000, 32000, 32000)),
        vmodel, TOTAL_LAYERS, MODEL_MB, 120000,
        keep_active_set=True,
    )
    assert out is not None
    # Idle slot 0 untouched; overflow stays within the active set (slot 2).
    assert out[0] == 0.0
    assert out[2] == split[2] + 1


def test_cascade_upstream_respects_blocked_dest():
    """The upstream fallback must skip reserve-loaded (blocked) GPUs — the
    layer then lands on the best NON-blocked upstream card."""
    split = (17.0, 18.0, 8.0, 9.0, 9.0)
    gpus = _gpus_5()
    vmodel = _vmodel(split, [0.010, 0.006, 0.006, 0.006, 0.006])
    r = VerifyResult(fits=False, measured_free_mb=(500, 8000, 12000, 5000, 300),
                     thinks=None, detail="")
    budget = Budget(
        per_gpu_free=(20000, 20000, 18000, 14000, 14000),
        first_gpu_handicap=500, safety_margin=192,
        gpu_reserve_mb=(0, 0, 4000, 0, 0),  # GPU2 = VLM side-channel
    )
    out, _reason = _refine_split_from_measurement(
        split, gpus, r, budget, vmodel, TOTAL_LAYERS, MODEL_MB, 120000,
    )
    assert out is not None
    assert out[2] == split[2]            # blocked GPU2 stays untouched
    assert out[1] == split[1] + 1        # next-best headroom (GPU1) takes it
    assert out[4] == split[4] - 1


def test_cascade_no_upstream_without_free_estimate():
    """Without a per-card free estimate the fallback must NOT guess an
    upstream target (fast cards are usually packed full) — None it is."""
    dest = split_refine._cascade_destination(
        src=4, layers=[17.0, 18.0, 8.0, 9.0, 9.0], free_estimate=(),
        layer_cost_per_gpu=(), min_free_mb=192, step=1.0,
        keep_active_set=False,
    )
    assert dest is None


def test_measured_split_ceiling_min_over_active_cards():
    """Ceiling = min over active cards of ctx + (free−margin)/(layers·slope);
    idle cards are ignored (the upward search's rescue-probe anchor)."""
    # card0: 1000 + (400−200)/(4·0.001) = 51.000 (limiter)
    # card1: 1000 + (1000−200)/(2·0.001) = 401.000
    c = split_refine._measured_split_ceiling(
        [4, 2], [400.0, 1000.0], (0.001, 0.001), 1000, 200,
    )
    assert c == 51000.0
    # idle card contributes nothing
    c2 = split_refine._measured_split_ceiling(
        [4, 0], [400.0, 0.0], (0.001, 0.001), 1000, 200,
    )
    assert c2 == 51000.0
    # no active cards → inf (guard)
    assert split_refine._measured_split_ceiling([0], [0.0], (0.001,), 1000, 200) == float("inf")


def test_cascade_prefers_downstream_before_upstream():
    """The glass cascade stays a cascade: a downstream card that holds the
    reserve wins even when an upstream card has MORE headroom."""
    dest = split_refine._cascade_destination(
        src=2, layers=[17.0, 18.0, 8.0, 9.0, 9.0],
        free_estimate=(500, 9000, 300, 5000, 4000),
        layer_cost_per_gpu=(1200.0,) * 5, min_free_mb=192, step=1.0,
        keep_active_set=False,
    )
    assert dest == 3


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
    kwargs.setdefault("probe_cache", {})
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

    monkeypatch.setattr(ctx_search, "verify", _fake_verify)
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
    kwargs.setdefault("probe_cache", {})
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

    monkeypatch.setattr(ctx_search, "verify", _fake_verify)
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

    monkeypatch.setattr(ctx_search, "verify", _fake_verify)
    res, msgs = _drain_search_verbose(
        current_split=split, candidate=_candidate(split, vram_model=None),
        model=_model(), gpus=_gpus_5(), budget=_budget((30000,) * 5),
        full_cmd="--model x", port=1, env=None, probe_thinking=False,
        thinks_seen=None, status_prefix="t", lo=8192, hi=200000,
        iteration=0, initial_load_sig=None, initial_bias_mb=None,
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

    monkeypatch.setattr(ctx_search, "verify", _fake_verify)
    common = dict(
        current_split=split, candidate=_candidate(split, vram_model=vmodel),
        model=_model(), gpus=_gpus_5(), budget=_budget((30000,) * 5),
        full_cmd="--model x", port=1, env=None, probe_thinking=False,
        thinks_seen=None, status_prefix="v", lo=8192, hi=200000,
        iteration=0, initial_load_sig=None, initial_bias_mb=None,
    )
    # Variant 1: no seed, no cache — learns and caches the bias (the cached
    # value may legitimately be negative since the bias is bidirectional).
    res1, _ = _drain_search_verbose(**common)
    assert res1.best_r is not None and res1.best_r.fits
    assert fit_math._MODEL_BIAS_CACHE

    # Variant 2: same model+GPUs, still initial_bias_mb=0 — the cache seeds it,
    # so the search never falls into the unmeasured-bias floor.
    res2, msgs2 = _drain_search_verbose(**common)
    assert res2.best_r is not None and res2.best_r.fits
    assert not any("unmeasured bias" in m for m in msgs2)
    assert any("math max" in m for m in msgs2)


def test_mmproj_extra_counted_on_first_active_slot(tmp_path):
    """fit-params cannot model --mmproj; the projection must add the
    projector file size onto the first GPU that holds layers (the 35B
    single-GPU miss, 2026-07-31)."""
    from aifred.lib.calibration import projection as proj_mod
    mm = tmp_path / "mmproj-test-F16.gguf"
    mm.write_bytes(b"\x00" * (3 * 1024 * 1024))
    cmd = f"llama-server --mmproj {mm} --tensor-split 0,41,0,0,0 -c 1"
    assert proj_mod._mmproj_extra_mb(cmd) == 3
    assert proj_mod._first_active_slot(cmd, 5) == 1
    # Ohne mmproj: kein Zuschlag; ohne tensor-split: Slot 0.
    assert proj_mod._mmproj_extra_mb("llama-server -c 1") == 0
    assert proj_mod._first_active_slot("llama-server -c 1", 5) == 0
    # Fehlende Datei: 0 statt Exception.
    assert proj_mod._mmproj_extra_mb(
        "llama-server --mmproj /nonexistent/mm.gguf -c 1"
    ) == 0


def test_bias_state_bidirectional_with_oom_floor():
    """_BiasState learns in both directions; after a non-fitting probe the
    applied bias never drops below the hardest OOM measurement (oscillation
    guard for the risky/negative direction)."""
    state = fit_math._BiasState(cache_key=("m", ("u0",), "b2048-ub2048"))
    # Fitting probe, math too pessimistic: predicted 500 vs real 3000 free.
    upd = state.observe(500, 3000, fits=True)
    assert upd == (0, -2500)
    assert state.applied == -2500 and state.measured
    # Non-fitting probe: predicted 100 vs real 150 → raw -50 becomes the
    # OOM floor; applied rises to it.
    state.observe(100, 150, fits=False)
    assert state.oom_floor == -50 and state.applied == -50
    # A later fitting probe measuring an even lower raw must NOT drop the
    # applied bias below the proven OOM floor.
    state.observe(500, 3000, fits=True)
    assert state.applied == -50
    # Cache always carries the latest applied value.
    assert fit_math._MODEL_BIAS_CACHE[state.cache_key] == -50


def test_batch_signature_extraction():
    """-b/-ub are parsed from the cmd; absent flags fall back to the
    llama.cpp defaults (b=2048, ub=512)."""
    assert fit_math._batch_signature("llama-server -b 2048 -ub 2048 -c 1") == "b2048-ub2048"
    assert fit_math._batch_signature("llama-server -c 262144") == "b2048-ub512"


def test_pessimistic_math_recovers_after_first_probe(monkeypatch):
    """When the cost model is far too pessimistic (fit-params overstating
    ub-2048 compute buffers), the old one-directional bias froze the search
    into pure bisection. Now the first measured probe learns a negative bias
    and the math is trusted again (a 'math max' probe appears)."""
    split = (17.0, 18.0, 9.0, 9.0, 8.0)
    vmodel = _vmodel(split, [0.006] * 5)
    # Inflate the intercepts: math now predicts ~8 GB less free than real.
    vmodel = VRamModel(
        n_gpus=vmodel.n_gpus, kv_quant=vmodel.kv_quant, ngl=vmodel.ngl,
        tensor_split=vmodel.tensor_split,
        intercept_mb=tuple(i + 8000.0 for i in vmodel.intercept_mb),
        slope_mb_per_tok=vmodel.slope_mb_per_tok,
        low_point=vmodel.low_point, high_point=vmodel.high_point,
    )
    threshold = 150016

    async def _fake_verify(*, context, **kw):
        if context <= threshold:
            return VerifyResult(True, (9000,) * 5, None, "fit")
        return VerifyResult(False, (150,) * 5, None, "oom")

    monkeypatch.setattr(ctx_search, "verify", _fake_verify)
    res, msgs = _drain_search_verbose(
        current_split=split, candidate=_candidate(split, vram_model=vmodel),
        model=_model(), gpus=_gpus_5(), budget=_budget((30000,) * 5),
        full_cmd="--model x -b 2048 -ub 2048", port=1, env=None,
        probe_thinking=False, thinks_seen=None, status_prefix="t",
        lo=8192, hi=200000,
        iteration=0, initial_load_sig=None, initial_bias_mb=None,
    )
    assert res.best_r is not None and res.best_r.fits
    assert res.best_ctx <= threshold
    # The negative bias was learned and the math trusted again.
    assert any("math bias updated" in m for m in msgs)
    assert any("math max" in m for m in msgs)
    key = fit_math._bias_key(
        _model(), _gpus_5(), "b2048-ub2048",
    )
    assert fit_math._MODEL_BIAS_CACHE[key] < 0


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

    monkeypatch.setattr(ctx_search, "verify", _fake_verify)
    res = _drain_search(
        current_split=split, candidate=_candidate(split), model=_model(),
        gpus=_gpus_5(), budget=_budget((20000,) * 5),
        full_cmd="--model x", port=1, env=None, probe_thinking=False,
        thinks_seen=None, status_prefix="t", lo=8192, hi=200000,
        iteration=0, initial_load_sig=sig,
    )
    assert res.best_r is None
    assert probes["n"] == 1  # stopped after the first identical load death


# ── probe cache: dedup + proven-ceiling (combo-variant crawl fix) ─────────

def test_known_ctx_ceiling_caps_at_proven_failure():
    """The smallest FAILED ctx above the anchor at THIS split bounds the
    upward push; fits, other splits, and lower failures are ignored."""
    split = (17.0, 18.0, 8.0, 9.0, 9.0)
    other = (16.0, 18.0, 8.0, 9.0, 10.0)
    fit = VerifyResult(True, (2000,) * 5, None, "fit")
    oom = VerifyResult(False, (100,) * 5, None, "oom")
    cache = {
        (split, 100096): fit,
        (split, 120064): oom,   # nearest failure above the anchor
        (split, 140032): oom,   # a higher failure — not the nearest
        (other, 90112): oom,    # wrong split — must be ignored
    }
    # Nearest failure above the anchor wins.
    assert _known_ctx_ceiling(cache, split, 100096, 262144) == 120064
    # A fit is not a ceiling; the other-split OOM is ignored.
    assert _known_ctx_ceiling(cache, split, 95000, 262144) == 120064
    # Nothing failed above 140032 at this split → fall back to default.
    assert _known_ctx_ceiling(cache, split, 140032, 262144) == 262144


def test_down_search_caches_every_probe_without_reprobing(monkeypatch):
    """Each physical probe is recorded in the shared cache and no ctx is
    loaded twice — the bookkeeping the upward push reuses to skip reloads."""
    split = (17.0, 18.0, 8.0, 9.0, 9.0)
    threshold = 98304
    seen: list[int] = []

    async def _fake_verify(*, context, **kw):
        seen.append(context)
        if context <= threshold:
            return VerifyResult(True, (2000,) * 5, None, "fit")
        return VerifyResult(False, (100,) * 5, None, "oom")

    monkeypatch.setattr(ctx_search, "verify", _fake_verify)
    cache: dict = {}
    _drain_search(
        current_split=split, candidate=_candidate(split), model=_model(),
        gpus=_gpus_5(), budget=_budget((20000,) * 5),
        full_cmd="--model x", port=1, env=None, probe_thinking=False,
        thinks_seen=None, status_prefix="t", lo=8192, hi=200000,
        iteration=0, initial_load_sig=None, probe_cache=cache,
    )
    # No ctx was physically probed twice …
    assert len(seen) == len(set(seen))
    # … and every probe landed in the cache under this split.
    assert all((split, c) in cache for c in seen)
    assert len(cache) == len(seen)


def test_down_search_reuses_cached_probe(monkeypatch):
    """A ctx already in the cache is served from it — verify() is never
    called for that value, saving the ~3-min reload."""
    split = (17.0, 18.0, 8.0, 9.0, 9.0)
    threshold = 98304
    probed: list[int] = []

    async def _fake_verify(*, context, **kw):
        probed.append(context)
        if context <= threshold:
            return VerifyResult(True, (2000,) * 5, None, "fit")
        return VerifyResult(False, (100,) * 5, None, "oom")

    monkeypatch.setattr(ctx_search, "verify", _fake_verify)
    # First run fills the cache; a second run on the SAME cache must not
    # re-probe any of the already-known contexts.
    cache: dict = {}
    _drain_search(
        current_split=split, candidate=_candidate(split), model=_model(),
        gpus=_gpus_5(), budget=_budget((20000,) * 5),
        full_cmd="--model x", port=1, env=None, probe_thinking=False,
        thinks_seen=None, status_prefix="t", lo=8192, hi=200000,
        iteration=0, initial_load_sig=None, probe_cache=cache,
    )
    probed.clear()
    _drain_search(
        current_split=split, candidate=_candidate(split), model=_model(),
        gpus=_gpus_5(), budget=_budget((20000,) * 5),
        full_cmd="--model x", port=1, env=None, probe_thinking=False,
        thinks_seen=None, status_prefix="t", lo=8192, hi=200000,
        iteration=0, initial_load_sig=None, probe_cache=cache,
    )
    # The deterministic search revisits the same contexts — all cached now,
    # so verify() is never called again → zero reloads.
    assert probed == []
