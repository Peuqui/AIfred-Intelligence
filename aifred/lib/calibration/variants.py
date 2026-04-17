"""Speed and TTS variant derivation from the base candidate table.

The base calibration produces a table of ``Candidate``s — one per
``(n_gpus, kv_quant)`` cell it could project.  Speed and TTS variants
reuse that table so we don't pay for another fit-params sweep.

Speed variant
    Pick the smallest ``n_gpus`` inside the fastest speed class whose
    Candidate reaches at least ``MIN_USEFUL_CONTEXT_TOKENS``.  Prefer
    the lowest KV-quant that already satisfies ``F16_KV_PREFER_THRESHOLD``.

TTS variants
    Re-run projection with a shrunken ``Budget`` (TTS model occupies
    VRAM on one GPU).  Since the VRAM math is linear, we only need a
    single fit-params sweep at the reduced budget.  Everything else
    (optimizer, physical verification) is reused from the base flow.
"""

from __future__ import annotations

import logging

from .types import Budget, Candidate, GPU

logger = logging.getLogger(__name__)


def pick_base_candidate(
    candidates: list[Candidate],
    native_context: int,
    f16_prefer_threshold: int,  # kept for signature stability; unused now
    min_useful_context: int,
) -> Candidate | None:
    """Pick the best base candidate with a hard KV-quality ordering.

    Quality matters more than GPU count for the base variant, so we run
    a tiered search:

      1. ``f16 + max_context >= native_context`` — if anything qualifies,
         pick the one with the *fewest* GPUs (shifts the model toward
         the fastest cards).  Ties broken by projected headroom.
      2. ``q8_0 + max_context >= native_context`` — same selection.
      3. ``f16`` with the highest ``max_context`` still >= min_useful.
      4. ``q8_0`` with the highest ``max_context`` still >= min_useful.
      5. ``q4_0`` only if it was explicitly included in the sweep.

    Q4 is intentionally last-resort: it costs quality for marginal VRAM
    savings and we'd rather add a GPU than switch to Q4.
    """
    del f16_prefer_threshold  # no longer part of the ranking
    usable = [c for c in candidates if c.max_context >= min_useful_context]
    if not usable:
        return None

    def _best_of(kv: str, native_only: bool) -> Candidate | None:
        pool = [c for c in usable if c.kv_quant == kv]
        if native_only:
            pool = [c for c in pool if c.max_context >= native_context]
        if not pool:
            return None
        # Fewer GPUs first (fastest-first shift), then more projected headroom
        pool.sort(key=lambda c: (c.n_gpus, -c.projected_min_free_mb))
        return pool[0]

    # Stage 1–2: native context, quality order
    for kv in ("f16", "q8_0", "q4_0"):
        cand = _best_of(kv, native_only=True)
        if cand is not None:
            return cand

    # Stage 3–5: no one reaches native — pick best by quality then context
    for kv in ("f16", "q8_0", "q4_0"):
        pool = [c for c in usable if c.kv_quant == kv]
        if not pool:
            continue
        pool.sort(key=lambda c: (-c.max_context, c.n_gpus))
        return pool[0]

    return None


def pick_speed_candidate(
    candidates: list[Candidate],
    gpus: list[GPU],
    min_useful_context: int,
    base_n_gpus: int,
) -> Candidate | None:
    """Smallest n_gpus inside the fastest speed class that is usable.

    Returns ``None`` when no candidate with ``n_gpus < base_n_gpus``
    reaches ``min_useful_context`` — speed variant is useless otherwise.
    Only GPUs from speed_class 0 count toward the limit.
    """
    fast_class_size = sum(1 for g in gpus if g.speed_class == 0)
    limit = min(fast_class_size, base_n_gpus - 1)
    if limit < 1:
        return None

    eligible = [
        c for c in candidates
        if 1 <= c.n_gpus <= limit and c.max_context >= min_useful_context
    ]
    if not eligible:
        return None

    # Prefer smallest n_gpus (less tensor-split overhead), then f16 when
    # it already meets min_useful_context.
    eligible.sort(key=lambda c: (c.n_gpus, {"f16": 0, "q8_0": 1, "q4_0": 2}.get(c.kv_quant, 3)))
    return eligible[0]


def shrink_budget_for_tts(
    budget: Budget,
    occupied_before_tts: tuple[int, ...],
    occupied_with_tts: tuple[int, ...],
) -> Budget:
    """Return a new Budget with TTS VRAM subtracted per GPU.

    The difference between post-TTS-load occupation and pre-TTS-load
    occupation is what TTS costs us; subtracting it from per_gpu_free
    gives the budget the LLM actually has.
    """
    new_free: list[int] = []
    for i in range(len(budget.per_gpu_free)):
        before = occupied_before_tts[i] if i < len(occupied_before_tts) else 0
        after = occupied_with_tts[i] if i < len(occupied_with_tts) else 0
        tts_cost = max(0, after - before)
        new_free.append(max(0, budget.per_gpu_free[i] - tts_cost))
    return Budget(
        per_gpu_free=tuple(new_free),
        first_gpu_handicap=budget.first_gpu_handicap,
        safety_margin=budget.safety_margin,
    )
