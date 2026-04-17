"""Layer-split optimizer — fill-fastest-first, per-GPU VRAM model.

Rule: put as many layers as possible on the fastest GPUs, then spill
the overflow to the next speed class.  Do not distribute proportionally.
Inference speed is dominated by the slowest GPU that holds any weights,
so every layer we keep off a P40 is throughput saved.

Asymmetry handling: fit-params produces a *per-GPU* slope (MB VRAM
growth per context token).  CUDA0 typically shows a higher slope than
identical siblings because of display/CUDA-init overhead; we use the
measured per-GPU slope directly instead of averaging across GPUs.  The
optimizer therefore naturally places fewer layers on CUDA0 than on its
sibling RTX — proper math, no hand-tuned constants.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .types import Budget, GPU, VRamModel

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OptResult:
    tensor_split: tuple[float, ...]      # integer layers per GPU
    context: int                         # context this split supports
    per_gpu_predicted_free_mb: tuple[int, ...]
    reached_target: bool                 # True iff all layers got placed


def _per_gpu_coefficients(
    vmodel: VRamModel, total_layers: int, model_size_mb: float,
) -> tuple[list[float], list[float]]:
    """Extract (base_overhead_mb, slope_mb_per_tok_per_layer) per GPU.

    The fit-params VRamModel was measured at ``vmodel.tensor_split``.
    For each GPU we decompose::

        intercept_i = base_overhead_i + layers_at_meas_i * mb_per_layer
        slope_i     = layers_at_meas_i * slope_per_layer_per_tok_i

    ``base_overhead_i`` captures CUDA-init/driver/display cost that
    doesn't scale with layer count.  It's typically larger on CUDA0.
    ``slope_per_layer_per_tok_i`` captures KV-cache growth per layer
    per token; if fit-params sees asymmetry between identical GPUs
    (e.g. extra context-allocation overhead on CUDA0), it shows up here.
    """
    ts = list(vmodel.tensor_split)
    if not ts or sum(ts) == 0:
        ts = [1.0] * len(vmodel.intercept_mb)
    scale = sum(ts)
    layers_at_meas = [ts[i] / scale * total_layers for i in range(len(ts))]

    mb_per_layer = model_size_mb / total_layers if total_layers else 0.0

    base_overhead: list[float] = []
    slope_per_layer: list[float] = []
    for i in range(len(vmodel.intercept_mb)):
        base_i = max(
            0.0,
            vmodel.intercept_mb[i] - layers_at_meas[i] * mb_per_layer,
        )
        base_overhead.append(base_i)
        if layers_at_meas[i] > 0:
            slope_per_layer.append(
                vmodel.slope_mb_per_tok[i] / layers_at_meas[i]
            )
        else:
            slope_per_layer.append(0.0)

    # GPUs that weren't active in the measurement (layers=0) have no
    # measured slope.  Fall back to the mean of active GPUs — better
    # than zero, which would let the optimizer think P40 is infinite.
    active_slopes = [s for s in slope_per_layer if s > 0]
    mean_slope = sum(active_slopes) / len(active_slopes) if active_slopes else 0.0
    slope_per_layer = [s if s > 0 else mean_slope for s in slope_per_layer]

    return base_overhead, slope_per_layer


def _max_layers_on_gpu(
    gpu_idx: int,
    target_context: int,
    mb_per_layer: float,
    slope_per_layer_per_tok: float,
    base_overhead_mb: float,
    budget: Budget,
    extra_handicap_mb: int,
) -> int:
    """Max integer layers fitting on this GPU at ``target_context``.

    Solves::

        free - (base + extra_handicap) - layers*(mb_per_layer +
                                                slope*target_context)
            >= safety_margin

    for layers.  ``extra_handicap_mb`` is the first-in-class floor — a
    small conservative reserve for systems where fit-params cannot see
    display/driver overhead (e.g. freshly-booted system with nothing
    attached to CUDA0 yet).
    """
    allowance = (
        budget.per_gpu_free[gpu_idx]
        - base_overhead_mb
        - extra_handicap_mb
        - budget.safety_margin
    )
    if allowance <= 0:
        return 0
    cost_per_layer = mb_per_layer + slope_per_layer_per_tok * target_context
    if cost_per_layer <= 0:
        return 0
    return max(0, int(allowance / cost_per_layer))


def _predicted_free(
    layers: list[int],
    context: int,
    base_overhead_mb: list[float],
    slope_per_layer_per_tok: list[float],
    mb_per_layer: float,
    extra_handicap_mb: tuple[int, ...],
    budget: Budget,
) -> tuple[int, ...]:
    """Per-GPU predicted free VRAM after loading model+KV at ``context``."""
    out: list[int] = []
    for i, li in enumerate(layers):
        used = (
            base_overhead_mb[i]
            + li * mb_per_layer
            + li * slope_per_layer_per_tok[i] * context
        )
        free = int(budget.per_gpu_free[i] - used - extra_handicap_mb[i])
        out.append(free)
    return tuple(out)


def _context_ceiling_for_split(
    layers: list[int],
    base_overhead_mb: list[float],
    slope_per_layer_per_tok: list[float],
    mb_per_layer: float,
    extra_handicap_mb: tuple[int, ...],
    budget: Budget,
    target_context: int,
    precision: int = 256,
) -> int:
    """Highest context (multiple of ``precision``) this layer split supports."""
    limits: list[float] = []
    for i, li in enumerate(layers):
        if li <= 0 or slope_per_layer_per_tok[i] <= 0:
            continue
        allowance = (
            budget.per_gpu_free[i]
            - base_overhead_mb[i]
            - li * mb_per_layer
            - extra_handicap_mb[i]
            - budget.safety_margin
        )
        if allowance <= 0:
            limits.append(0.0)
            continue
        limits.append(allowance / (li * slope_per_layer_per_tok[i]))

    if not limits:
        return 0
    ceil = min(min(limits), float(target_context))
    return max(0, int(ceil // precision) * precision)


def fill_fastest_first(
    model: VRamModel,
    budget: Budget,
    gpus: list[GPU],
    active_gpus: list[int],
    total_layers: int,
    model_size_mb: float,
    target_context: int,
) -> OptResult:
    """Pack layers onto fastest GPUs first, spill to slower ones.

    Ordering: ``(speed_class, cuda_id)`` ascending — fastest class first,
    CUDA0 before CUDA1 within a class, etc.  Each GPU takes the maximum
    integer layer count that fits at ``target_context``; any remaining
    layers continue to the next GPU.

    Per-GPU slope from fit-params naturally puts fewer layers on CUDA0
    than on identical siblings (display/CUDA overhead is baked in).
    """
    base_overhead, slope_per_layer = _per_gpu_coefficients(
        model, total_layers, model_size_mb,
    )
    mb_per_layer = model_size_mb / total_layers if total_layers else 0.0

    extra_handicap = tuple(
        budget.first_gpu_handicap if gpus[i].first_in_class else 0
        for i in range(len(gpus))
    )

    ordered = sorted(active_gpus, key=lambda i: (gpus[i].speed_class, i))

    layers = [0] * len(gpus)
    remaining = total_layers
    for i in ordered:
        if remaining == 0:
            break
        max_li = _max_layers_on_gpu(
            i, target_context, mb_per_layer, slope_per_layer[i],
            base_overhead[i], budget, extra_handicap[i],
        )
        take = min(max_li, remaining)
        layers[i] = take
        remaining -= take

    # Overshoot tolerance: if a handful of layers didn't fit by strict
    # math, dump them on the fastest-class GPUs that still have the most
    # headroom.  llama-server interprets -ts as a ratio so slight
    # overshoot is harmless in practice (saw "23:24:0:0" work in the
    # field even though strict math said 47/48 layers).  We only allow
    # this when the model-size-per-GPU has actually been budgeted —
    # i.e. ``remaining`` is small (≤ 2) relative to total_layers.
    overshoot_budget = max(2, total_layers // 24)  # ~4% of layers
    if 0 < remaining <= overshoot_budget:
        fastest_class = min(gpus[i].speed_class for i in active_gpus)
        fast_gpus = [i for i in ordered if gpus[i].speed_class == fastest_class]
        # Sort by "headroom remaining after first pass" descending
        fast_gpus.sort(
            key=lambda i: budget.per_gpu_free[i]
            - base_overhead[i]
            - extra_handicap[i]
            - layers[i] * (mb_per_layer + slope_per_layer[i] * target_context),
            reverse=True,
        )
        cursor = 0
        while remaining > 0 and fast_gpus:
            layers[fast_gpus[cursor % len(fast_gpus)]] += 1
            remaining -= 1
            cursor += 1

    reached = remaining == 0
    actual_ctx = _context_ceiling_for_split(
        layers, base_overhead, slope_per_layer,
        mb_per_layer, extra_handicap, budget, target_context,
    )
    predicted_free = _predicted_free(
        layers, actual_ctx, base_overhead, slope_per_layer,
        mb_per_layer, extra_handicap, budget,
    )

    return OptResult(
        tensor_split=tuple(float(x) for x in layers),
        context=actual_ctx,
        per_gpu_predicted_free_mb=predicted_free,
        reached_target=reached,
    )
