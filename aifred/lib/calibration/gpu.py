"""GPU enumeration, speed-class grouping and baseline-budget estimation.

Speed classes are keyed on **compute capability** (nvidia-smi field
``compute_cap``) — not on GPU name.  Higher compute capability means
newer architecture and, in practice, faster inference per layer, so the
fastest class is filled first regardless of VRAM size.  Within the same
compute capability, ties are broken by total VRAM descending.

CUDA_DEVICE_ORDER=FASTEST_FIRST should be set on the calibration
subprocess so CUDA0 matches the first entry of our enumeration.

The "first-GPU handicap" captures that CUDA0 inside a speed class holds
less free VRAM than identically-specced siblings (display / system
output usually lands on device 0).  Instead of hand-tuning a constant
we *measure* it: the handicap is the free-VRAM delta to the greediest
sibling in the same class, clamped to a reasonable range.
"""

from __future__ import annotations

import logging
from typing import Any

from .. import nvidia_smi
from ..gpu_utils import get_all_gpus_memory_info
from .types import GPU, Budget

logger = logging.getLogger(__name__)


# Minimum handicap applied to the physically-first GPU in its speed class,
# even when baseline nvidia-smi shows no asymmetry.  Prevents the optimizer
# from packing CUDA0 completely full and leaving no headroom for the
# CUDA/driver overhead that only shows up once llama-server actually loads.
_MIN_FIRST_GPU_HANDICAP_MB = 256

# If the measured free-VRAM delta between CUDA0 and its sibling exceeds
# this threshold, it's almost certainly an *external* occupant (TTS
# container, orphaned server) on one of the two GPUs — not the modest
# display/compositor overhead we're trying to model.  External
# occupation is already reflected in per_gpu_free, so treating it as
# CUDA0 system overhead would double-subtract the same VRAM.  In that
# case fall back to the floor so the optimizer doesn't leave a GB of
# headroom unused.  (Display/compositor on an idle system is typically
# 200–500 MiB; real hardware asymmetry well below that.)
_HARDWARE_HANDICAP_THRESHOLD_MB = 500


_GPU_NAME_PREFIXES = ("NVIDIA GeForce ", "NVIDIA ", "Quadro ", "Tesla ")


def _short_name(name: str) -> str:
    for prefix in _GPU_NAME_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def _compute_caps_by_index() -> dict[int, float]:
    """Query ``compute_cap`` per GPU index from nvidia-smi.

    Returns an empty dict on failure; callers fall back to the
    name-based heuristic.  compute_cap strings look like "7.5" or "6.1".
    """
    rows = nvidia_smi.query(fields="index,compute_cap")
    if not rows:
        return {}
    out: dict[int, float] = {}
    for row in rows:
        try:
            out[int(row["index"])] = float(row["compute_cap"])
        except (KeyError, ValueError):
            continue
    return out


def enumerate_gpus() -> list[GPU]:
    """Query nvidia-smi and return GPUs ordered fastest-first.

    Priority:
      1. **Compute capability** (higher = faster inference, class 0).
      2. Total VRAM (tie-breaker within a compute level).
      3. Original nvidia-smi index (final stable tie-breaker).

    ``speed_class`` is an integer in [0, N) where 0 is the fastest
    compute level.  All GPUs sharing the same compute_cap share a
    speed_class.  ``first_in_class`` marks the lowest cuda_id within
    each class — used for the first-GPU handicap.
    """
    info = get_all_gpus_memory_info()
    if not info or not info.get("per_gpu"):
        return []

    compute_by_idx = _compute_caps_by_index()

    raw: list[dict[str, Any]] = [dict(g) for g in info["per_gpu"]]
    for g in raw:
        g["_compute"] = compute_by_idx.get(int(g.get("index", -1)), 0.0)

    # Fastest first: compute DESC, total_mb DESC, index ASC
    raw.sort(
        key=lambda g: (-float(g["_compute"]), -int(g["total_mb"]), int(g["index"])),
    )

    # Assign speed_class: same compute_cap → same class, in encounter order
    class_of_compute: dict[float, int] = {}
    for g in raw:
        cc = float(g["_compute"])
        if cc not in class_of_compute:
            class_of_compute[cc] = len(class_of_compute)

    seen_first_in_class: set[int] = set()
    result: list[GPU] = []
    for cuda_id, g in enumerate(raw):
        name = _short_name(g.get("gpu_model", f"GPU{g['index']}"))
        cc = float(g["_compute"])
        cls = class_of_compute[cc]
        first = cls not in seen_first_in_class
        if first:
            seen_first_in_class.add(cls)
        result.append(GPU(
            cuda_id=cuda_id,
            name=name,
            total_mb=int(g["total_mb"]),
            free_mb=int(g["free_mb"]),
            speed_class=cls,
            first_in_class=first,
        ))
    return result


def group_by_speed_class(gpus: list[GPU]) -> list[list[GPU]]:
    """Group GPUs by speed_class — result[0] is the fastest class."""
    classes: dict[int, list[GPU]] = {}
    for g in gpus:
        classes.setdefault(g.speed_class, []).append(g)
    return [classes[k] for k in sorted(classes)]


def measure_first_gpu_handicap(gpus: list[GPU]) -> int:
    """Empirical handicap for CUDA0 relative to its class siblings.

    Small deltas (< threshold) are treated as real hardware/driver
    asymmetry and fed to the optimizer.  Large deltas indicate an
    external VRAM occupant on one of the GPUs — those are already
    reflected in ``per_gpu_free``, so we fall back to the floor to
    avoid double-subtracting.
    """
    if not gpus:
        return _MIN_FIRST_GPU_HANDICAP_MB
    cuda0 = gpus[0]
    siblings = [g for g in gpus[1:] if g.speed_class == cuda0.speed_class]
    if not siblings:
        return _MIN_FIRST_GPU_HANDICAP_MB
    max_sibling_free = max(g.free_mb for g in siblings)
    measured = max(0, max_sibling_free - cuda0.free_mb)
    if measured > _HARDWARE_HANDICAP_THRESHOLD_MB:
        # External occupant — already baked into per_gpu_free
        return _MIN_FIRST_GPU_HANDICAP_MB
    return max(measured, _MIN_FIRST_GPU_HANDICAP_MB)


def build_budget(gpus: list[GPU], safety_margin: int) -> Budget:
    """Construct the calibration budget from a GPU list."""
    return Budget(
        per_gpu_free=tuple(g.free_mb for g in gpus),
        first_gpu_handicap=measure_first_gpu_handicap(gpus),
        safety_margin=safety_margin,
    )


def total_free_mb(gpus: list[GPU]) -> int:
    return sum(g.free_mb for g in gpus)


def total_vram_mb(gpus: list[GPU]) -> int:
    return sum(g.total_mb for g in gpus)


def find_min_gpus_for_weights(
    model_size_mb: float,
    gpus: list[GPU],
    per_gpu_overhead_mb: int = 1024,
) -> int:
    """Fewest fastest-first GPUs whose combined free VRAM holds the weights.

    Uses ``total_mb`` (not ``free_mb``) minus per-GPU overhead, so the
    answer doesn't shrink just because other processes are temporarily
    using VRAM — calibration cleans those up before loading.
    """
    for n in range(1, len(gpus) + 1):
        capacity = sum(g.total_mb for g in gpus[:n])
        if model_size_mb + per_gpu_overhead_mb * n < capacity:
            return n
    return len(gpus)


def format_gpu_detail(
    gpus: list[GPU], free_override_mb: tuple[int, ...] | None = None,
) -> str:
    """One-line per-GPU summary for log output.

    ``free_override_mb`` lets callers report measured (not baseline) VRAM.
    """
    parts: list[str] = []
    for i, g in enumerate(gpus):
        free = free_override_mb[i] if free_override_mb else g.free_mb
        parts.append(f"{g.name} (CUDA{g.cuda_id}): {free} MB free")
    return ", ".join(parts)
