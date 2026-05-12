"""GPU enumeration, speed-class grouping and baseline-budget estimation.

The GPU order is taken **directly from llama.cpp's own view** (via
``llama-fit-params --version`` parsing). This is the order llama-server
will see at inference time when ``CUDA_DEVICE_ORDER=FASTEST_FIRST`` is
set — and we set it both in the calibration subprocess and in every
llama-swap config entry, so calibration index N == inference index N.

Why not enumerate via nvidia-smi and sort ourselves?  Because NVIDIA's
FASTEST_FIRST heuristic isn't documented and changes between driver
versions (e.g. it weighs HBM2 memory-bandwidth higher than compute
capability, putting V100 before RTX 8000 even though cc 7.0 < 7.5).
Mirroring that heuristic in Python would be guesswork.  llama.cpp's
``ggml_cuda_init`` enumeration is the source of truth.

Speed classes still group GPUs by compute capability for tensor-split
heuristics (same-class siblings get equal-ish layer counts).

The "first-GPU handicap" captures that CUDA0 inside a speed class holds
less free VRAM than identically-specced siblings (display / system
output usually lands on device 0).  Instead of hand-tuning a constant
we *measure* it: the handicap is the free-VRAM delta to the greediest
sibling in the same class, clamped to a reasonable range.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from ..gpu_utils import get_all_gpus_memory_info
from .types import GPU, Budget

# Path to llama-fit-params binary (companion of llama-server, used here
# only for its CUDA-device enumeration in --version output — cheap, no
# model load).
_LLAMA_FIT_PARAMS = Path("/home/mp/llama.cpp/build/bin/llama-fit-params")

# Regex for ggml_cuda_init device lines like:
#   Device 0: Tesla V100-PCIE-32GB, compute capability 7.0, VMM: yes, VRAM: 32494 MiB
_DEVICE_LINE_RE = re.compile(
    r"Device\s+(\d+):\s+(.+?),\s+compute capability\s+([\d.]+),"
    r".*?VRAM:\s+(\d+)\s+MiB"
)

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


def _query_llamacpp_device_order() -> list[dict[str, Any]]:
    """Run ``llama-fit-params --version`` and parse its CUDA device list.

    Returns a list of dicts in llama.cpp's own enumeration order
    (CUDA_DEVICE_ORDER=FASTEST_FIRST applied):
        [{"cuda_id": int, "name": str, "compute_cap": float, "total_mb": int}, ...]

    Raises RuntimeError on any failure — there is no fallback. If
    llama-fit-params is broken, calibration cannot trust any subsequent
    tensor-split allocation, so failing loudly is the right behaviour.
    """
    if not _LLAMA_FIT_PARAMS.exists():
        raise RuntimeError(
            f"llama-fit-params binary not found at {_LLAMA_FIT_PARAMS}"
        )
    env = {**os.environ, "CUDA_DEVICE_ORDER": "FASTEST_FIRST"}
    try:
        proc = subprocess.run(
            [str(_LLAMA_FIT_PARAMS), "--version"],
            env=env, capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("llama-fit-params --version timed out") from exc
    # ggml_cuda_init writes to stderr
    output = (proc.stderr or "") + (proc.stdout or "")
    devices: list[dict[str, Any]] = []
    for match in _DEVICE_LINE_RE.finditer(output):
        devices.append({
            "cuda_id": int(match.group(1)),
            "name": match.group(2).strip(),
            "compute_cap": float(match.group(3)),
            "total_mb": int(match.group(4)),
        })
    if not devices:
        head = output[:500].replace("\n", " | ")
        raise RuntimeError(
            f"llama-fit-params output contained no ggml_cuda_init device "
            f"lines. First 500 chars: {head!r}"
        )
    return devices


def _match_smi_to_llamacpp(
    llamacpp_devs: list[dict[str, Any]],
    smi_devs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Match nvidia-smi entries onto llama.cpp's enumeration order.

    Returns a list parallel to ``llamacpp_devs`` where each entry's
    ``total_mb`` is replaced with nvidia-smi's authoritative total (the
    value llama.cpp prints in ``ggml_cuda_init`` is *free* at init time,
    not capacity), and ``free_mb`` + ``smi_index`` are added.

    Matching is by GPU short name only; within a group of identical
    cards, both nvidia-smi (PCI_BUS_ID) and llama.cpp (FASTEST_FIRST,
    tie-broken by PCI bus) produce the same ordering, so encounter-order
    pairing within each name group is correct.
    """
    # Pool nvidia-smi entries by short_name, preserving smi index order.
    pool: dict[str, list[dict[str, Any]]] = {}
    for g in smi_devs:
        key = _short_name(str(g.get("gpu_model", "")))
        pool.setdefault(key, []).append(g)

    matched: list[dict[str, Any]] = []
    for dev in llamacpp_devs:
        key = _short_name(dev["name"])
        candidates = pool.get(key)
        if not candidates:
            raise RuntimeError(
                f"No nvidia-smi entry matches llama.cpp Device "
                f"{dev['cuda_id']}: {dev['name']!r}"
            )
        smi_entry = candidates.pop(0)
        matched.append({
            **dev,
            "total_mb": int(smi_entry["total_mb"]),  # authoritative
            "free_mb": int(smi_entry["free_mb"]),
            "smi_index": int(smi_entry.get("index", -1)),
        })
    return matched


def enumerate_gpus() -> list[GPU]:
    """Return GPUs in llama.cpp's own (FASTEST_FIRST) enumeration order.

    Source of truth: ``llama-fit-params --version`` output. This matches
    what llama-server will see at inference time when the calibration
    subprocess and the llama-swap config both set
    ``CUDA_DEVICE_ORDER=FASTEST_FIRST`` (they do).

    Free-VRAM comes from nvidia-smi and is mapped onto llama.cpp's order
    by (gpu_model, total_mb) — unambiguous for heterogeneous setups,
    stable within groups of identical cards.

    ``speed_class`` is still based on compute capability (used by the
    optimizer to group same-class siblings for layer-balancing), but
    plays no role in the GPU index order itself.
    """
    info = get_all_gpus_memory_info()
    if not info or not info.get("per_gpu"):
        return []

    llamacpp_devs = _query_llamacpp_device_order()
    merged = _match_smi_to_llamacpp(llamacpp_devs, list(info["per_gpu"]))

    # Assign speed_class: same compute_cap → same class, in llama.cpp encounter order
    class_of_compute: dict[float, int] = {}
    for d in merged:
        cc = float(d["compute_cap"])
        if cc not in class_of_compute:
            class_of_compute[cc] = len(class_of_compute)

    seen_first_in_class: set[int] = set()
    result: list[GPU] = []
    for cuda_id, d in enumerate(merged):
        cc = float(d["compute_cap"])
        cls = class_of_compute[cc]
        first = cls not in seen_first_in_class
        if first:
            seen_first_in_class.add(cls)
        result.append(GPU(
            cuda_id=cuda_id,
            name=_short_name(d["name"]),
            total_mb=int(d["total_mb"]),
            free_mb=int(d["free_mb"]),
            speed_class=cls,
            first_in_class=first,
            smi_index=int(d.get("smi_index", -1)),
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
