"""Top-level calibration orchestrator.

Five sequential phases (``A``–``E``) each documented inline.  The output
protocol (``__RESULT__`` / ``__SPEED__`` strings) is preserved so that
existing state-mixin parsers keep working without change.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import AsyncIterator, Optional

from ..config import (
    CALIBRATION_MIN_CONTEXT,
    LLAMACPP_CALIBRATION_PORT,
    LLAMACPP_CALIBRATION_PRECISION,
    LLAMACPP_VISION_VRAM_RESERVE,
    LLAMACPP_VRAM_SAFETY_MARGIN,
    MIN_FREE_RAM_MB,
    MIN_USEFUL_CONTEXT_TOKENS,
)
from ..formatting import format_number
from ..gguf_utils import (
    extract_quantization_from_filename,
    get_gguf_layer_count,
    get_gguf_native_context,
    get_gguf_total_size,
)
from ..model_vram_cache import add_llamacpp_calibration
from . import llamaswap_io as io
from . import projection as proj
from .gpu import (
    build_budget,
    enumerate_gpus,
    find_min_gpus_for_weights,
    total_free_mb,
)
from .optimizer import OptResult, fill_fastest_first
from .types import Budget, Candidate, GPU, Model, Result
from .verifier import VerifyResult, kill_orphan_on_port, verify

logger = logging.getLogger(__name__)


# KV-quant levels in order of quality (higher = better, larger VRAM).
# Q4 is intentionally excluded from the default sweep: it sacrifices
# too much quality for marginal VRAM savings.  A caller can opt in by
# passing ``min_kv="q4_0"`` (used by edge-case re-runs on very small
# remaining budgets, never by the default flow).
_DEFAULT_KV_LEVELS = ("f16", "q8_0")
_ALL_KV_LEVELS = ("f16", "q8_0", "q4_0")


async def calibrate_llamacpp_model(
    model_id: str,
    gguf_path: Path,
    full_cmd: str,
    port: int = LLAMACPP_CALIBRATION_PORT,
    config_path: Optional[Path] = None,
    min_kv: str = "f16",
    known_thinking: Optional[bool] = None,
    env: Optional[dict[str, str]] = None,
) -> AsyncIterator[str]:
    """Calibrate one llama.cpp model end-to-end.

    Yields human-readable progress strings and two sentinel lines for
    programmatic consumers (state mixin / ``_parse_calibration_result``):

    ``__RESULT__:{ctx}:{ngl}:{mode}:{thinks|nothink}:{kv}:{ts_csv}:{num_gpus}``
    ``__SPEED__:{split_colon},{ctx},{num_gpus},{kv}``  (only when a
                                                       speed variant
                                                       was calibrated)

    When ``config_path`` is ``None`` the YAML is never written (dry-run
    mode used by TTS-variant calibration).
    """
    from ...backends.ollama import wait_for_vram_stable

    # ── Phase A: metadata + budget ──────────────────────────────────
    yield f"Reading GGUF metadata: {gguf_path.name}"
    model = _load_model_meta(model_id, gguf_path)
    if not model:
        yield "Cannot read GGUF metadata"
        yield "__RESULT__:0:0:error"
        return

    await kill_orphan_on_port(port)
    yield "Waiting for VRAM to stabilize..."
    await wait_for_vram_stable(max_wait_seconds=15.0)

    gpus = enumerate_gpus()
    if not gpus:
        yield "No GPUs detected"
        yield "__RESULT__:0:0:error"
        return
    gpu_total = tuple(g.total_mb for g in gpus)

    # Vision models keep extra reserve for image-preprocessing buffers.
    safety_margin = LLAMACPP_VRAM_SAFETY_MARGIN
    if _is_vision_model(full_cmd):
        safety_margin += LLAMACPP_VISION_VRAM_RESERVE
        yield f"Vision model detected — safety margin +{LLAMACPP_VISION_VRAM_RESERVE} MB"

    budget = build_budget(gpus, safety_margin=safety_margin)

    yield (
        f"Model: {model.model_id} ({format_number(model.size_mb / 1024, 1)} GB), "
        f"native context: {format_number(model.native_context)} "
        f"(model = {model.size_mb / sum(gpu_total):.0%} of "
        f"{format_number(sum(gpu_total) / 1024, 1)} GB VRAM)"
    )
    yield (
        f"Free VRAM: {format_number(total_free_mb(gpus))} MB, "
        f"first-GPU handicap: {budget.first_gpu_handicap} MB"
    )

    # ── Phase B+C: sequential search — F16 first, then Q8 ──────────
    # Stop as soon as one (kv, n_gpus) configuration reaches the native
    # context AND places all layers (reached_target).  Fewer GPUs are
    # tried first so the fastest cards absorb the load.
    min_gpus = find_min_gpus_for_weights(model.size_mb, gpus)
    kv_levels = _kv_levels_from(min_kv)

    yield (
        f"Phase B: searching (KV-quality first, then GPU count) for "
        f"native ctx={format_number(model.native_context)}..."
    )

    base_pick: Candidate | None = None
    all_tried: list[Candidate] = []
    for kv in kv_levels:
        if base_pick is not None:
            break
        for n in range(min_gpus, len(gpus) + 1):
            c, reason = await _project_cell(
                model, gpus, budget, full_cmd, kv, n,
            )
            if c is None:
                yield f"  [{n} GPUs / KV={kv}] skipped — {reason}"
                continue
            all_tried.append(c)
            yield _format_candidate_line(c, gpus)
            if c.max_context >= model.native_context:
                base_pick = c
                break

    # Fallback: nobody reached native — pick the configuration with the
    # highest max_context, using KV quality only as tie-breaker.
    # Rationale: once we're already below native we've lost the "full
    # quality" anchor, so a Q8 candidate with 150 k context is plainly
    # more useful than an F16 candidate with 60 k.  F16 only wins when
    # both candidates reach the same context.
    if base_pick is None and all_tried:
        _kv_rank = {"f16": 0, "q8_0": 1, "q4_0": 2}
        all_tried.sort(
            key=lambda c: (-c.max_context, _kv_rank.get(c.kv_quant, 3), c.n_gpus),
        )
        base_pick = all_tried[0]

    if base_pick is None:
        yield "No GPU-only candidate reaches minimum useful context — trying hybrid"
        async for msg in _calibrate_hybrid(
            model, gpus, budget, full_cmd, port, env,
            known_thinking=known_thinking, config_path=config_path,
        ):
            yield msg
        return

    yield (
        f"Phase C: chosen base = {base_pick.n_gpus} GPUs, KV={base_pick.kv_quant}, "
        f"split={_split_str(base_pick.tensor_split)}, "
        f"target ctx={format_number(base_pick.max_context)}"
    )

    base_result = await _verify_and_refine(
        base_pick, model, gpus, budget, full_cmd, port, env,
        probe_thinking=known_thinking is None,
        status_prefix="base",
    )
    async for msg in base_result.messages:
        yield msg

    final = base_result.result
    if final is None:
        yield "Base calibration failed verification — trying hybrid"
        async for msg in _calibrate_hybrid(
            model, gpus, budget, full_cmd, port, env,
            known_thinking=known_thinking, config_path=config_path,
        ):
            yield msg
        return

    thinks = final.thinks if known_thinking is None else known_thinking

    # ── Phase E: speed variant (fewer GPUs, fastest class only) ─────
    speed_result: Optional[Result] = None
    if len(gpus) > 1 and final.num_gpus > 1:
        speed_pick = await _find_speed_candidate(
            model, gpus, budget, full_cmd, all_tried,
            base_n_gpus=final.num_gpus,
            base_kv=final.kv_quant,
        )
        if speed_pick is not None:
            yield (
                f"Phase E: speed variant — {speed_pick.n_gpus} GPUs, "
                f"KV={speed_pick.kv_quant}, "
                f"split={_split_str(speed_pick.tensor_split)}, "
                f"target ctx={format_number(speed_pick.max_context)}"
            )
            yield _format_candidate_line(speed_pick, gpus)
            speed_verify = await _verify_and_refine(
                speed_pick, model, gpus, budget, full_cmd, port, env,
                probe_thinking=False,
                status_prefix="speed",
            )
            async for msg in speed_verify.messages:
                yield msg
            speed_result = speed_verify.result

    # ── Speed → Base promotion ─────────────────────────────────────
    # If the speed variant reaches native context at the same KV
    # quality, it is strictly better than the base (fewer/faster GPUs,
    # same everything else).  Promote it and drop the speed variant —
    # no point offering two configs that differ only in GPU count when
    # the smaller one is already on par.
    if (
        speed_result is not None
        and speed_result.context >= model.native_context
        and speed_result.kv_quant == final.kv_quant
    ):
        yield (
            f"Speed variant matches native ctx at KV={final.kv_quant} — "
            f"promoting to base ({speed_result.num_gpus} GPUs), "
            f"no separate speed variant kept"
        )
        final = speed_result
        speed_result = None

    # ── Phase D: write configs + persist cache ─────────────────────
    if config_path:
        async for msg in _write_base_config(config_path, model_id, final):
            yield msg

    _persist_cache(model, final, gpus)

    if speed_result and config_path:
        async for msg in _write_speed_config(config_path, model_id, speed_result):
            yield msg

    # ── Emit sentinels ─────────────────────────────────────────────
    yield _result_sentinel(final, thinks=thinks)
    if speed_result:
        yield _speed_sentinel(speed_result)


# ═══════════════════════════════════════════════════════════════════
# Phase helpers
# ═══════════════════════════════════════════════════════════════════

def _is_vision_model(cmd: str) -> bool:
    return "--mmproj" in cmd


def _kv_levels_from(min_kv: str) -> list[str]:
    """Pick which KV-quant levels to include in the sweep.

    Always includes F16 and Q8 (cheap to project anyway, and the picker
    uses a strict quality ranking).  Q4 is *only* added when the caller
    explicitly asks for it via ``min_kv="q4_0"`` — typically never, since
    the default flow prefers to add a GPU over dropping to Q4.

    ``min_kv`` is therefore interpreted as "Q4 is also acceptable",
    not "start sweeping here".
    """
    if min_kv == "q4_0":
        return list(_ALL_KV_LEVELS)  # f16, q8_0, q4_0
    return list(_DEFAULT_KV_LEVELS)  # f16, q8_0


def _split_str(ratios: tuple[float, ...]) -> str:
    return ":".join(str(int(r)) for r in ratios)


def _format_candidate_line(c: Candidate, gpus: list[GPU]) -> str:
    """One log line per projection cell, showing every GPU's predicted free.

    Example::

        [3 GPUs / KV=f16] max_ctx=262.144 split=22:22:4:0
          RTX 8000 (CUDA0): 2.500 MB, RTX 8000 (CUDA1): 3.100 MB,
          P40 (CUDA2): 1.800 MB, P40 (CUDA3): idle
    """
    parts: list[str] = []
    for i, g in enumerate(gpus):
        layers_i = int(c.tensor_split[i]) if i < len(c.tensor_split) else 0
        if layers_i == 0:
            parts.append(f"{g.name} (CUDA{g.cuda_id}): idle")
            continue
        free = c.predicted_free_mb[i] if i < len(c.predicted_free_mb) else 0
        parts.append(
            f"{g.name} (CUDA{g.cuda_id}): {format_number(max(0, free))} MB"
        )
    return (
        f"  [{c.n_gpus} GPUs / KV={c.kv_quant}] "
        f"max_ctx={format_number(c.max_context)} "
        f"split={_split_str(c.tensor_split)}\n"
        f"    {', '.join(parts)}"
    )


def _load_model_meta(model_id: str, gguf_path: Path) -> Model | None:
    native = get_gguf_native_context(gguf_path)
    total_layers = get_gguf_layer_count(gguf_path)
    if not native or not total_layers:
        return None
    size_mb = get_gguf_total_size(gguf_path) / (1024 ** 2)
    return Model(
        model_id=model_id,
        gguf_path=gguf_path,
        native_context=native,
        total_layers=total_layers,
        size_mb=size_mb,
        mb_per_layer=size_mb / total_layers,
        quantization=extract_quantization_from_filename(gguf_path.name),
    )


async def _find_speed_candidate(
    model: Model,
    gpus: list[GPU],
    budget: Budget,
    full_cmd: str,
    already_tried: list[Candidate],
    base_n_gpus: int,
    base_kv: str,
) -> Candidate | None:
    """Find a speed variant: fewer GPUs, same KV quality as base.

    Reuses already-projected cells from the base search when possible;
    runs new projections only for cells we haven't seen.  Speed picks
    the smallest n_gpus (strictly fewer than base) whose projected
    max_context reaches at least ``MIN_USEFUL_CONTEXT_TOKENS``.

    Returns ``None`` early when base is already at or below
    ``find_min_gpus_for_weights`` — the model simply won't fit on fewer
    cards, no point trying.
    """
    min_gpus = find_min_gpus_for_weights(model.size_mb, gpus)
    if base_n_gpus <= min_gpus:
        return None

    fastest_count = sum(1 for g in gpus if g.speed_class == 0)
    max_n = min(fastest_count, base_n_gpus - 1)
    if max_n < 1:
        return None

    for n in range(1, max_n + 1):
        # Re-use from base search if the same (n, kv) was already projected
        cached = next(
            (c for c in already_tried
             if c.n_gpus == n and c.kv_quant == base_kv),
            None,
        )
        if cached is not None:
            c: Candidate | None = cached
        else:
            c, _reason = await _project_cell(
                model, gpus, budget, full_cmd, base_kv, n,
            )
        if c is None:
            continue
        if c.max_context >= MIN_USEFUL_CONTEXT_TOKENS:
            return c
    return None


async def _project_cell(
    model: Model,
    gpus: list[GPU],
    budget: Budget,
    full_cmd: str,
    kv: str,
    n_gpus: int,
) -> tuple[Candidate | None, str]:
    """Project one (n_gpus, kv) cell.

    Returns ``(candidate, reason)``.  ``candidate`` is ``None`` on any
    failure; ``reason`` is a short label for the log so the caller can
    show exactly why a cell got skipped (fit-params error, model too big
    for this GPU count, …).
    """
    total_gpus = len(gpus)
    ctx_low = min(CALIBRATION_MIN_CONTEXT, model.native_context // 2) or 2048
    ctx_high = model.native_context
    active = list(range(n_gpus))

    seed = _seed_tensor_split(model.total_layers, active, gpus, budget)
    padded_seed = tuple(
        float(seed[i]) if i < len(seed) else 0.0 for i in range(total_gpus)
    )
    cmd = proj.adjust_cmd_for_projection(full_cmd, padded_seed, kv)

    try:
        low = await proj.project(cmd, model.gguf_path, ctx_low, ngl=99,
                                 n_gpus=total_gpus)
        high = await proj.project(cmd, model.gguf_path, ctx_high, ngl=99,
                                  n_gpus=total_gpus)
    except proj.FitParamsError as e:
        logger.warning(f"fit-params failed (n_gpus={n_gpus}, kv={kv}): {e}")
        return None, f"fit-params error: {e}"

    try:
        vmodel = proj.fit_linear_model(
            low=low, high=high,
            n_gpus=n_gpus, kv_quant=kv, ngl=99,
            tensor_split=padded_seed,
        )
    except ValueError as e:
        return None, f"linear-model fit error: {e}"

    opt: OptResult = fill_fastest_first(
        model=vmodel,
        budget=budget,
        gpus=gpus,
        active_gpus=active,
        total_layers=model.total_layers,
        model_size_mb=model.size_mb,
        target_context=model.native_context,
    )

    # If not every layer fits at native context, the model's KV cache
    # at native would eat more VRAM than available.  Before giving up,
    # binary-search the largest context at which all layers *do* fit —
    # for very large models (≥ 80 % of total VRAM) that's the only
    # GPU-only path and is always better than hybrid mode.
    if not opt.reached_target:
        reduced = _max_ctx_where_all_layers_fit(
            vmodel=vmodel, budget=budget, gpus=gpus, active=active,
            total_layers=model.total_layers, model_size_mb=model.size_mb,
            ceiling=model.native_context,
        )
        if reduced is None:
            placed = int(sum(opt.tensor_split))
            return None, (
                f"only {placed}/{model.total_layers} layers fit — model "
                f"too big for {n_gpus} GPU(s) even at minimum context"
            )
        # A reduced-context result with context == 0 means the binary
        # search landed on a split whose layer weights alone exceed a
        # GPU's budget — the "reached_target=True" path in
        # fill_fastest_first can report that when the overshoot
        # fallback crams layers onto an already-tight GPU.  Treat it as
        # a failure instead of propagating an unusable candidate.
        if reduced.context < CALIBRATION_MIN_CONTEXT:
            return None, (
                f"model too big for {n_gpus} GPU(s) at KV={kv}: no split "
                f"leaves room for even the minimum context"
            )
        opt = reduced

    return Candidate(
        mode="gpu",
        n_gpus=n_gpus,
        kv_quant=kv,
        ngl=99,
        tensor_split=opt.tensor_split,
        max_context=opt.context,
        predicted_free_mb=opt.per_gpu_predicted_free_mb,
        vram_model=vmodel,
    ), "ok"


def _max_ctx_where_all_layers_fit(
    vmodel,
    budget: Budget,
    gpus: list[GPU],
    active: list[int],
    total_layers: int,
    model_size_mb: float,
    ceiling: int,
) -> OptResult | None:
    """Binary-search the largest context where ``fill_fastest_first``
    can place every layer.

    For giant models the native context is unreachable because the KV
    cache alone would exceed available VRAM.  This function walks the
    context down to the largest multiple of the precision where every
    layer still has a home on the GPUs.  Returns ``None`` when even
    the minimum context can't hold the model — the caller then falls
    through to hybrid.
    """
    precision = LLAMACPP_CALIBRATION_PRECISION
    lo = CALIBRATION_MIN_CONTEXT
    hi = ceiling
    best: OptResult | None = None
    while lo <= hi:
        mid = ((lo + hi) // 2 // precision) * precision
        if mid < CALIBRATION_MIN_CONTEXT:
            break
        trial = fill_fastest_first(
            model=vmodel, budget=budget, gpus=gpus, active_gpus=active,
            total_layers=total_layers, model_size_mb=model_size_mb,
            target_context=mid,
        )
        if trial.reached_target:
            best = trial
            lo = mid + precision
        else:
            hi = mid - precision
    return best


def _seed_tensor_split(
    total_layers: int,
    active_gpus: list[int],
    gpus: list[GPU],
    budget: Budget,
) -> list[int]:
    """Initial integer layer split proportional to (free − handicap)."""
    weights: list[float] = []
    for i in active_gpus:
        free = budget.per_gpu_free[i]
        if gpus[i].first_in_class:
            free = max(0, free - budget.first_gpu_handicap)
        weights.append(float(free))
    if sum(weights) <= 0:
        weights = [float(gpus[i].total_mb) for i in active_gpus]
    total_w = sum(weights)
    raw = [total_layers * w / total_w for w in weights]
    layers = [int(round(r)) for r in raw]
    diff = total_layers - sum(layers)
    if diff != 0:
        order = sorted(
            range(len(active_gpus)),
            key=lambda k: raw[k] - layers[k],
            reverse=(diff > 0),
        )
        step = 1 if diff > 0 else -1
        for k in order[: abs(diff)]:
            layers[k] += step
    return layers


# ═══════════════════════════════════════════════════════════════════
# Phase C/E: verification with at most one refinement round
# ═══════════════════════════════════════════════════════════════════

class _VerifyAndRefine:
    """Small container so Phase C/E can yield messages then a result."""

    def __init__(self, messages, result: Result | None):
        self.messages = messages
        self.result = result


async def _verify_and_refine(
    candidate: Candidate,
    model: Model,
    gpus: list[GPU],
    budget: Budget,
    full_cmd: str,
    port: int,
    env: Optional[dict[str, str]],
    probe_thinking: bool,
    status_prefix: str,
) -> _VerifyAndRefine:
    """Verify ``candidate``; refine split from measured VRAM if needed.

    Returns a container with the streamed messages and final ``Result``.

    Loop structure:
      1. First verify at candidate.max_context with the optimizer's split.
      2. If OOM: shrink context once using measured overshoot, retry.
      3. If fits but uneven: refine split (one layer swap) and retry.
         Continue refining while the balance keeps improving (measured
         spread shrinks) and no two refinements produce the same split
         (oscillation guard).
    """
    messages: list[str] = []
    current_split = candidate.tensor_split
    current_ctx = candidate.max_context
    iteration = 0
    seen_splits: set[tuple[float, ...]] = {current_split}
    last_good: tuple[VerifyResult, tuple[float, ...], int] | None = None

    # ── Step 1: first verify ───────────────────────────────────────
    iteration += 1
    r = await verify(
        full_cmd=proj.adjust_cmd_for_projection(
            full_cmd, current_split, candidate.kv_quant,
        ),
        context=current_ctx, port=port, gpus=gpus,
        safety_margin_mb=budget.safety_margin,
        ngl=candidate.ngl, env=env, probe_thinking=probe_thinking,
    )
    messages.append(_fmt_verify(
        status_prefix, iteration, current_split, current_ctx, r,
    ))
    thinks_seen: bool | None = r.thinks

    if not r.fits:
        # Context overshoot — shrink based on measurement
        shrunk_ctx = _shrink_to_fit(
            candidate, gpus, budget, r, fallback_reduction=0.1,
        )
        if shrunk_ctx <= 0 or shrunk_ctx >= current_ctx:
            return _VerifyAndRefine(messages=_iter(messages), result=None)

        iteration += 1
        messages.append(
            f"{status_prefix} OOM — retrying at shrunk ctx "
            f"{format_number(shrunk_ctx)}"
        )
        r = await verify(
            full_cmd=proj.adjust_cmd_for_projection(
                full_cmd, current_split, candidate.kv_quant,
            ),
            context=shrunk_ctx, port=port, gpus=gpus,
            safety_margin_mb=budget.safety_margin,
            ngl=candidate.ngl, env=env,
            probe_thinking=probe_thinking and thinks_seen is None,
        )
        messages.append(_fmt_verify(
            status_prefix, iteration, current_split, shrunk_ctx, r,
        ))
        if r.thinks is not None:
            thinks_seen = r.thinks
        if not r.fits:
            return _VerifyAndRefine(messages=_iter(messages), result=None)
        current_ctx = shrunk_ctx

    last_good = (r, current_split, current_ctx)

    # ── Step 2: keep refining split while it helps ─────────────────
    while True:
        refined, reason = _refine_split_from_measurement(
            current_split, gpus, r, budget,
            vram_model=candidate.vram_model,
            total_layers=model.total_layers,
            model_size_mb=model.size_mb,
            current_context=current_ctx,
        )
        if refined is None:
            # Always log why refinement stopped — makes it transparent
            # that the algorithm *did* consider rebalancing.
            messages.append(f"{status_prefix} balance check: {reason}")
            break
        if refined in seen_splits:
            messages.append(
                f"{status_prefix} split oscillation detected — keeping "
                f"{_split_str(current_split)}"
            )
            break
        seen_splits.add(refined)

        iteration += 1
        messages.append(
            f"{status_prefix} balance check: swap {reason} — "
            f"trying split {_split_str(refined)}"
        )
        r_new = await verify(
            full_cmd=proj.adjust_cmd_for_projection(
                full_cmd, refined, candidate.kv_quant,
            ),
            context=current_ctx, port=port, gpus=gpus,
            safety_margin_mb=budget.safety_margin,
            ngl=candidate.ngl, env=env, probe_thinking=False,
        )
        messages.append(_fmt_verify(
            status_prefix, iteration, refined, current_ctx, r_new,
        ))
        if not r_new.fits:
            messages.append(f"{status_prefix} refinement OOM — keeping previous")
            break

        current_split = refined
        r = r_new
        last_good = (r, current_split, current_ctx)

    # Build the final result from the last successful run
    r_final, split_final, ctx_final = last_good
    final_candidate = Candidate(
        mode=candidate.mode,
        n_gpus=candidate.n_gpus,
        kv_quant=candidate.kv_quant,
        ngl=candidate.ngl,
        tensor_split=split_final,
        max_context=ctx_final,
        predicted_free_mb=candidate.predicted_free_mb,
        vram_model=candidate.vram_model,
    )
    if thinks_seen is not None:
        # Put the earliest thinking probe back into the verify result
        r_final = VerifyResult(
            fits=r_final.fits,
            measured_free_mb=r_final.measured_free_mb,
            thinks=thinks_seen,
            detail=r_final.detail,
        )
    result = _build_result(
        final_candidate, ctx=ctx_final, verify_r=r_final,
        num_active_gpus=_active_gpu_count(split_final),
    )
    return _VerifyAndRefine(messages=_iter(messages), result=result)


def _refine_split_from_measurement(
    current_split: tuple[float, ...],
    gpus: list[GPU],
    verify_r: VerifyResult,
    budget: Budget,
    vram_model,
    total_layers: int,
    model_size_mb: float,
    current_context: int,
) -> tuple[tuple[float, ...] | None, str]:
    """Propose a layer swap when an active GPU is near OOM.

    Returns ``(new_split, reason)``.  ``new_split`` is ``None`` when no
    swap would improve the balance; ``reason`` is a short human string
    the caller can log so it's visible that a swap was considered.

    Rule 1: refine only when the tightest active GPU has less than
            ``2 × safety_margin`` free — otherwise nothing to fix.
    Rule 2: each candidate swap (bottleneck → dest) is accepted only
            if the predicted post-swap minimum free VRAM exceeds the
            current minimum by more than ``safety_margin``.  Cross-
            class swaps (RTX ↔ P40) are allowed — the math is the
            single arbiter, since slow-class GPUs often have more
            headroom and can rescue a tight fastest-class GPU.
    """
    from .optimizer import _per_gpu_coefficients

    if not verify_r.measured_free_mb:
        return None, "no measurement"

    active = [i for i, r in enumerate(current_split) if r > 0]
    if len(active) < 2:
        return None, "only one active GPU"

    active_free = [(i, verify_r.measured_free_mb[i]) for i in active
                   if i < len(verify_r.measured_free_mb)]
    if len(active_free) < 2:
        return None, "measurement short"
    active_free.sort(key=lambda t: t[1])
    bottleneck, b_free = active_free[0]

    if b_free >= 2 * budget.safety_margin:
        return None, (
            f"tightest GPU CUDA{bottleneck} has {b_free} MB free — "
            f"no OOM danger"
        )

    if current_split[bottleneck] <= 1:
        return None, f"CUDA{bottleneck} already down to 1 layer"

    base_overhead, slope_per_layer = _per_gpu_coefficients(
        vram_model, total_layers, model_size_mb,
    )
    mb_per_layer = model_size_mb / total_layers if total_layers else 0.0

    save_on_bottleneck = mb_per_layer + slope_per_layer[bottleneck] * current_context
    best_dest: int | None = None
    best_new_min_free: float = float(b_free)
    rejected_reasons: list[str] = []
    for dest, d_free in active_free[1:]:
        cost_on_dest = mb_per_layer + slope_per_layer[dest] * current_context
        new_b = b_free + save_on_bottleneck
        new_d = d_free - cost_on_dest
        new_min = min(new_b, new_d)
        if new_min > best_new_min_free + budget.safety_margin:
            best_new_min_free = new_min
            best_dest = dest
        else:
            rejected_reasons.append(
                f"CUDA{bottleneck}→CUDA{dest}: new min would be "
                f"{int(new_min)} MB"
            )

    if best_dest is None:
        rejected_summary = "; ".join(rejected_reasons) or "no candidates"
        return None, (
            f"CUDA{bottleneck} tight at {b_free} MB but no swap improves "
            f"balance ({rejected_summary})"
        )

    new_split = list(current_split)
    new_split[bottleneck] -= 1
    new_split[best_dest] += 1
    return (
        tuple(new_split),
        f"CUDA{bottleneck} ({b_free} MB) → CUDA{best_dest}: "
        f"predicted new min {int(best_new_min_free)} MB",
    )


def _shrink_to_fit(
    candidate: Candidate,
    gpus: list[GPU],
    budget: Budget,
    verify_r: VerifyResult,
    fallback_reduction: float = 0.1,
) -> int:
    """Compute a smaller context that should fit given the failure.

    If we have measurement data, we know exactly how many MiB we
    overshot on the tightest GPU.  Convert that into tokens via the
    model's slope.  Otherwise fall back to a fixed percentage shrink.
    """
    precision = LLAMACPP_CALIBRATION_PRECISION
    if verify_r.measured_free_mb:
        overshoot_mb = 0
        for i, free in enumerate(verify_r.measured_free_mb):
            if i >= len(candidate.tensor_split) or candidate.tensor_split[i] == 0:
                continue
            short = budget.safety_margin - free
            if short > overshoot_mb:
                overshoot_mb = short
        if overshoot_mb > 0:
            total_slope = sum(candidate.vram_model.slope_mb_per_tok)
            if total_slope > 0:
                tokens_to_shed = int(overshoot_mb / total_slope * 1.1)
                new_ctx = candidate.max_context - tokens_to_shed
                new_ctx = max(0, int(new_ctx // precision) * precision)
                return new_ctx
    shrunk = int(candidate.max_context * (1 - fallback_reduction))
    return max(0, int(shrunk // precision) * precision)


def _build_result(
    candidate: Candidate,
    ctx: int,
    verify_r: VerifyResult,
    num_active_gpus: int,
) -> Result:
    return Result(
        variant="base" if candidate.mode == "gpu" else "base",
        mode=candidate.mode,
        context=ctx,
        ngl=candidate.ngl,
        kv_quant=candidate.kv_quant,
        tensor_split=candidate.tensor_split,
        num_gpus=num_active_gpus,
        thinks=bool(verify_r.thinks),
        remaining_free_mb=verify_r.measured_free_mb,
    )


def _active_gpu_count(ts: tuple[float, ...]) -> int:
    return sum(1 for r in ts if r > 0)


# ═══════════════════════════════════════════════════════════════════
# Config writers
# ═══════════════════════════════════════════════════════════════════

async def _write_base_config(
    config_path: Path, model_id: str, result: Result,
) -> AsyncIterator[str]:
    io.update_llamaswap_context(config_path, model_id, result.context)
    io.update_llamaswap_ngl(config_path, model_id, result.ngl)
    io.update_llamaswap_tensor_split(
        config_path, model_id, list(result.tensor_split),
    )
    io.update_llamaswap_cuda_visible(
        config_path, model_id, result.num_gpus, len(result.tensor_split),
    )
    if result.kv_quant != "f16":
        io.update_llamaswap_kv_cache_quant(
            config_path, model_id, result.kv_quant,
        )
    else:
        io.remove_llamaswap_kv_cache_quant(config_path, model_id)
    yield f"Base config written: ctx={format_number(result.context)}, split={_split_str(result.tensor_split)}"


async def _write_speed_config(
    config_path: Path, model_id: str, result: Result,
) -> AsyncIterator[str]:
    split_colon = _split_str(result.tensor_split)
    io.add_llamaswap_speed_variant(
        config_path=config_path,
        model_id=model_id,
        speed_split_cuda0=0,  # legacy, unused when speed_layer_split given
        speed_split_rest=0,
        speed_context=result.context,
        num_gpus=result.num_gpus,
        kv_quant=result.kv_quant,
        speed_layer_split=split_colon,
    )
    yield f"Speed config written: ctx={format_number(result.context)}, split={split_colon}"


def _persist_cache(model: Model, result: Result, gpus: list[GPU]) -> None:
    """Write the base result to the persistent JSON cache."""
    vram_per_gpu = ",".join(str(g.total_mb) for g in gpus)
    add_llamacpp_calibration(
        model_id=model.model_id,
        max_context=result.context,
        native_context=model.native_context,
        gguf_path=str(model.gguf_path),
        quantization=model.quantization,
        gpu_model=", ".join({g.name for g in gpus}),
        model_size_gb=model.size_mb / 1024,
        ngl=result.ngl,
        mode=result.mode,
        vram_per_gpu=vram_per_gpu,  # type: ignore[arg-type]
    )


# ═══════════════════════════════════════════════════════════════════
# Hybrid fallback (reduce ngl to free GPU VRAM for more context)
# ═══════════════════════════════════════════════════════════════════

async def _calibrate_hybrid(
    model: Model,
    gpus: list[GPU],
    budget: Budget,
    full_cmd: str,
    port: int,
    env: Optional[dict[str, str]],
    known_thinking: Optional[bool],
    config_path: Optional[Path],
) -> AsyncIterator[str]:
    """Offload layers to CPU to free GPU VRAM for more context.

    Strategy: for each target context (descending from native), compute
    the smallest ``ngl`` whose fit-params projection still fits, then
    verify.  The old code did this with a binary search per target —
    we can derive ``ngl`` directly from the overrun::

        cpu_layers_needed = overrun_mb / mb_per_layer
        ngl = total_layers - cpu_layers_needed - safety
    """
    from ..gpu_utils import get_free_ram_mb

    # Cap the swap we will count as CPU-RAM headroom.  Enough to absorb
    # a load-time peak when the kernel swaps inactive pages out, but
    # not so much that we'd invite real inference thrashing.
    HYBRID_SWAP_BUDGET_MB = 4096

    def _get_free_swap_mb() -> int:
        """Free swap in MB — treated as a bounded extension of CPU-RAM
        headroom.  The system must still end up with at least
        ``MIN_FREE_RAM_MB`` of *real* RAM available after the model
        loads; the swap headroom only covers transient peaks.
        """
        try:
            import psutil
            return int(psutil.swap_memory().free / (1024 * 1024))
        except (ImportError, OSError):
            return 0

    yield "Entering hybrid mode (reducing ngl)..."
    targets = [c for c in (model.native_context, 131072, 65536, 32768, 16384)
               if c <= model.native_context]

    # Equal split used only for overrun measurement (ngl=99, all-GPU projection).
    # The split doesn't affect the summed overrun so 1:1:...:1 is fine here.
    ts_equal = tuple(float(1) for _ in range(len(gpus)))

    for target in targets:
        # Project oversize at ngl=99
        cmd_f16 = proj.adjust_cmd_for_projection(full_cmd, ts_equal, "f16")
        try:
            point = await proj.project(cmd_f16, model.gguf_path, target, ngl=99)
        except proj.FitParamsError:
            continue

        overrun = sum(
            max(0, used - (g.total_mb - budget.safety_margin))
            for used, g in zip(point.per_gpu_used_mb, gpus)
        )
        if overrun == 0:
            # Weirdly fits at ngl=99 — skip, GPU-only flow should have caught this
            continue

        cpu_layers = int(overrun / model.mb_per_layer * 1.15) + 1
        ngl = max(0, model.total_layers - cpu_layers)
        if ngl <= 0:
            yield f"Hybrid target {format_number(target)}: model too large even with ngl=0"
            continue

        cpu_ram_needed = int(cpu_layers * model.mb_per_layer * 1.1)
        free_ram = get_free_ram_mb() or 0
        swap_usable = min(_get_free_swap_mb(), HYBRID_SWAP_BUDGET_MB)
        # MIN_FREE_RAM_MB must remain free in *real* RAM after the
        # model is loaded — swap only widens the budget above that.
        available = free_ram + swap_usable
        if cpu_ram_needed > available - MIN_FREE_RAM_MB:
            swap_note = f" + {swap_usable} MB swap" if swap_usable else ""
            yield (
                f"Hybrid target {format_number(target)}: RAM insufficient "
                f"({cpu_ram_needed} MB needed, {free_ram} MB free{swap_note})"
            )
            continue

        # Dynamic split: distribute ngl GPU-layers proportional to free VRAM,
        # respecting speed classes and first-GPU handicap — same logic as the
        # GPU-only path.  Works for any hardware (1 GPU, 4 identical, mixed).
        seed = _seed_tensor_split(ngl, list(range(len(gpus))), gpus, budget)
        ts_ngl = tuple(float(x) for x in seed)

        yield f"Hybrid: ngl={ngl}, ctx={format_number(target)}, split={_split_str(ts_ngl)} — verifying..."
        r = await verify(
            full_cmd=proj.adjust_cmd_for_projection(full_cmd, ts_ngl, "f16"),
            context=target,
            port=port,
            gpus=gpus,
            safety_margin_mb=budget.safety_margin,
            ngl=ngl,
            env=env,
            probe_thinking=known_thinking is None,
        )
        yield _fmt_verify("hyb", 1, ts_ngl, target, r)
        if r.fits:
            thinks = known_thinking if known_thinking is not None else bool(r.thinks)
            if config_path:
                io.update_llamaswap_context(config_path, model.model_id, target)
                io.update_llamaswap_ngl(config_path, model.model_id, ngl)
                io.update_llamaswap_tensor_split(
                    config_path, model.model_id, list(ts_ngl),
                )
                io.update_llamaswap_cuda_visible(
                    config_path, model.model_id, len(gpus), len(gpus),
                )
                io.remove_llamaswap_kv_cache_quant(config_path, model.model_id)
            vram_per_gpu = ",".join(str(g.total_mb) for g in gpus)
            add_llamacpp_calibration(
                model_id=model.model_id,
                max_context=target,
                native_context=model.native_context,
                gguf_path=str(model.gguf_path),
                quantization=model.quantization,
                gpu_model=", ".join({g.name for g in gpus}),
                model_size_gb=model.size_mb / 1024,
                ngl=ngl,
                mode="hybrid",
                vram_per_gpu=vram_per_gpu,  # type: ignore[arg-type]
            )
            ts_csv = ",".join(f"{x:g}" for x in ts_ngl if x > 0)
            yield (
                f"__RESULT__:{target}:{ngl}:hybrid:"
                f"{'thinks' if thinks else 'nothink'}:f16:{ts_csv}:{len(gpus)}"
            )
            return

    yield "Hybrid: no configuration found"
    yield "__RESULT__:0:0:error"


# ═══════════════════════════════════════════════════════════════════
# Formatting helpers
# ═══════════════════════════════════════════════════════════════════

def _fmt_verify(
    prefix: str, iteration: int,
    ts: tuple[float, ...], ctx: int, r: VerifyResult,
) -> str:
    status = "✓" if r.fits else "✗"
    head = (
        f"[{prefix}.{iteration}] {_split_str(ts)} | "
        f"ctx {format_number(ctx)} | {status}"
    )
    if r.detail:
        head += f" | {r.detail}"
    return head


def _result_sentinel(r: Result, thinks: bool) -> str:
    ts_csv = ",".join(f"{x:g}" for x in r.tensor_split if x > 0)
    return (
        f"__RESULT__:{r.context}:{r.ngl}:{r.mode}:"
        f"{'thinks' if thinks else 'nothink'}:{r.kv_quant}:"
        f"{ts_csv}:{r.num_gpus}"
    )


def _speed_sentinel(r: Result) -> str:
    split_colon = _split_str(r.tensor_split)
    # Preserve legacy __SPEED__ grammar used by _parse_calibration_result.
    return f"__SPEED__:{split_colon},{r.context},{r.num_gpus},{r.kv_quant}"


async def _iter(msgs: list[str]):
    """Yield each message in order (async generator helper)."""
    for m in msgs:
        yield m
