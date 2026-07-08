"""GPU enumeration, speed-class grouping and baseline-budget estimation.

GPUs are identified by their permanent NVIDIA UUID, not by a transient
CUDA index. Sort order is **compute_cap DESC** (highest compute first),
with name + UUID as deterministic tiebreaks.

Why UUID, not indices: ``CUDA_VISIBLE_DEVICES`` accepts UUIDs, and
llama-server then enumerates the GPUs in exactly the order calibration
intended — independent of NVIDIA's FASTEST_FIRST heuristic, the PCI bus
layout or any subsequent slot moves. The whole CUDA-vs-nvidia-smi index
mapping that haunted earlier versions disappears.

speed_class groups GPUs of identical compute capability for the
optimizer's homogeneous-fill strategy (e.g. "two RTX 8000 first, then
spill to V100").

The "first-GPU handicap": within a class, one GPU typically holds less
free VRAM than its identical sibling because it carries the
display/compositor overhead. We mark that one ``first_in_class=True``
empirically by picking the minimum-free GPU per class — no driver-order
heuristic involved.
"""

from __future__ import annotations

import logging
import subprocess
from collections import defaultdict
from typing import Any

from .types import GPU, Budget

logger = logging.getLogger(__name__)


# Minimum handicap applied to the display-carrying GPU in its class,
# even when the baseline free-VRAM delta to its sibling is smaller.
# Prevents the optimizer from packing that GPU completely full and
# leaving no headroom for the CUDA/driver overhead that only shows up
# once llama-server actually loads.
_MIN_FIRST_GPU_HANDICAP_MB = 256

# If the measured free-VRAM delta between the tightest and the most-free
# sibling exceeds this threshold, it's almost certainly an *external*
# occupant (TTS container, orphaned server) on one of the two GPUs —
# not the modest display/compositor overhead. External occupation is
# already reflected in per_gpu_free, so treating it as system overhead
# would double-subtract. Fall back to the floor in that case.
# (Display/compositor on an idle system is typically 200–500 MiB.)
_HARDWARE_HANDICAP_THRESHOLD_MB = 500


_GPU_NAME_PREFIXES = ("NVIDIA GeForce ", "NVIDIA ", "Quadro ", "Tesla ")
# Suffixes that just repeat info (form factor / VRAM size) already
# implied by the model number — strip for shorter log lines.
_GPU_NAME_SUFFIXES = (
    "-PCIE-32GB", "-PCIe-32GB", "-PCIE-16GB", "-PCIe-16GB",
    "-PCIE-80GB", "-PCIe-80GB", "-PCIE-40GB", "-PCIe-40GB",
    "-SXM2-32GB", "-SXM2-16GB", "-SXM4-40GB", "-SXM4-80GB",
)


def _short_name(name: str) -> str:
    for prefix in _GPU_NAME_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    for suffix in _GPU_NAME_SUFFIXES:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    return name


def _query_nvidia_smi() -> list[dict[str, Any]]:
    """Single nvidia-smi query for all GPU fields needed by calibration."""
    try:
        result = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=uuid,gpu_name,compute_cap,memory.total,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            logger.warning(f"nvidia-smi exited {result.returncode}: {result.stderr[:200]}")
            return []
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning(f"nvidia-smi unavailable: {e}")
        return []

    rows: list[dict[str, Any]] = []
    for line in result.stdout.strip().split("\n"):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 5:
            continue
        try:
            rows.append({
                "uuid": parts[0],
                "name": _short_name(parts[1]),
                "compute_cap": float(parts[2]),
                "total_mb": int(parts[3]),
                "free_mb": int(parts[4]),
            })
        except ValueError:
            continue
    return rows


def gpu_uuid_labels() -> dict[str, str]:
    """Map each GPU UUID → a human-readable ``"GPU<idx> (<short name>)"``.

    The ``<idx>`` is the nvidia-smi row position, i.e. the PCI-bus index
    the user sees when running ``nvidia-smi`` — so ``GPU0``/``GPU2``/…
    line up with that tool. Used to annotate the (UUID-based,
    reboot-stable) llama-swap config with readable comments so the
    sperrige UUIDs stay legible without giving up their reboot safety.

    Returns an empty dict when nvidia-smi is unavailable — the caller
    treats that as "skip the cosmetic annotation", never a hard error.
    """
    return {
        row["uuid"]: f"GPU{idx} ({row['name']})"
        for idx, row in enumerate(_query_nvidia_smi())
    }


def enumerate_gpus() -> list[GPU]:
    """Return all visible GPUs, sorted by compute_cap DESC, name, UUID.

    Position 0 = highest compute capability. Within the same compute
    class, ties are broken by GPU name then UUID, both ascending — fully
    deterministic, no driver heuristics involved.
    """
    rows = _query_nvidia_smi()
    if not rows:
        return []

    # Deterministic sort: compute_cap DESC, total_mb DESC, name, uuid.
    # compute_cap first because newer architectures (Tensor Cores etc.)
    # outweigh raw VRAM for inference speed; within the same compute
    # class, larger VRAM cards come first because they hold more of a
    # given model on a single GPU (less inter-GPU transfer overhead).
    rows.sort(key=lambda g: (
        -float(g["compute_cap"]),
        -int(g["total_mb"]),
        g["name"],
        g["uuid"],
    ))

    # speed_class: same compute_cap → same class, in encounter order.
    # encounter order == compute_cap DESC after the sort above, so class 0
    # is the highest compute class.
    class_of_compute: dict[float, int] = {}
    for g in rows:
        cc = float(g["compute_cap"])
        if cc not in class_of_compute:
            class_of_compute[cc] = len(class_of_compute)

    # first_in_class: the GPU per compute-class with the lowest free_mb.
    # Empirical detection of the display-carrying card — works for any
    # GPU layout without name heuristics.
    by_class: dict[float, list[dict[str, Any]]] = defaultdict(list)
    for g in rows:
        by_class[float(g["compute_cap"])].append(g)
    first_uuids: set[str] = set()
    for group in by_class.values():
        if len(group) == 1:
            first_uuids.add(group[0]["uuid"])
            continue
        # Tightest free GPU is treated as display-carrying. If two are
        # tied, pick the one with the lexicographically smaller UUID —
        # deterministic and we don't have a better signal.
        tightest = min(group, key=lambda g: (g["free_mb"], g["uuid"]))
        first_uuids.add(tightest["uuid"])

    return [
        GPU(
            uuid=str(r["uuid"]),
            name=str(r["name"]),
            compute_cap=float(r["compute_cap"]),
            total_mb=int(r["total_mb"]),
            free_mb=int(r["free_mb"]),
            speed_class=class_of_compute[float(r["compute_cap"])],
            first_in_class=r["uuid"] in first_uuids,
        )
        for r in rows
    ]


def group_by_speed_class(gpus: list[GPU]) -> list[list[GPU]]:
    """Group GPUs by speed_class — result[0] is the highest class."""
    classes: dict[int, list[GPU]] = {}
    for g in gpus:
        classes.setdefault(g.speed_class, []).append(g)
    return [classes[k] for k in sorted(classes)]


def measure_first_gpu_handicap(gpus: list[GPU]) -> int:
    """Empirical handicap for the display-carrying GPU vs its sibling.

    Within the highest compute class, compute the free-VRAM delta
    between the tightest GPU (= display-carrying, ``first_in_class``)
    and the most-free sibling. Small deltas are real driver overhead.
    Large deltas (> threshold) indicate external occupants and we fall
    back to the floor to avoid double-subtracting (the external
    occupation is already reflected in per_gpu_free).
    """
    if not gpus:
        return _MIN_FIRST_GPU_HANDICAP_MB
    fastest_class = [g for g in gpus if g.speed_class == 0]
    if len(fastest_class) < 2:
        return _MIN_FIRST_GPU_HANDICAP_MB
    first = next((g for g in fastest_class if g.first_in_class), fastest_class[0])
    siblings = [g for g in fastest_class if g.uuid != first.uuid]
    if not siblings:
        return _MIN_FIRST_GPU_HANDICAP_MB
    max_sibling_free = max(g.free_mb for g in siblings)
    measured = max(0, max_sibling_free - first.free_mb)
    if measured > _HARDWARE_HANDICAP_THRESHOLD_MB:
        return _MIN_FIRST_GPU_HANDICAP_MB
    return max(measured, _MIN_FIRST_GPU_HANDICAP_MB)


def build_budget(gpus: list[GPU], safety_margin: int) -> Budget:
    """Assemble the per-GPU VRAM budget used by the optimizer."""
    return Budget(
        per_gpu_free=tuple(g.free_mb for g in gpus),
        first_gpu_handicap=measure_first_gpu_handicap(gpus),
        safety_margin=safety_margin,
    )


def total_free_mb(gpus: list[GPU]) -> int:
    return sum(g.free_mb for g in gpus)


def find_min_gpus_for_weights(model_size_mb: float, gpus: list[GPU]) -> int:
    """Smallest n such that the n largest GPUs (by free_mb) hold the model.

    Independent of compute order — purely a VRAM-fit check. The actual
    GPU selection for that count is decided later by the optimizer.
    """
    if not gpus:
        return 1
    by_size = sorted(gpus, key=lambda g: -g.free_mb)
    cumulative = 0
    for i, g in enumerate(by_size, start=1):
        cumulative += g.free_mb
        if cumulative >= model_size_mb:
            return i
    return len(gpus)


def cuda_visible_devices(gpus: list[GPU]) -> str:
    """Build a ``CUDA_VISIBLE_DEVICES`` value from a list of GPUs (UUIDs).

    UUIDs are passed in the order they appear in ``gpus`` — that order
    becomes the CUDA enumeration order seen by the launched process,
    so ``gpus[0]`` is CUDA0, ``gpus[1]`` is CUDA1, etc.
    """
    return ",".join(g.uuid for g in gpus)
