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

The "first-GPU handicap": the first card of the HIGHEST compute class is
CUDA device 0 in the pinned fill order and carries llama.cpp's main-device
buffers (logits/output tensor, compute workspace, MTP draft) plus any
display/compositor overhead. Exactly that one card is marked
``first_in_class=True`` — empirically the minimum-free GPU of class 0, no
driver-order heuristic involved. Slower classes carry none of these
buffers and get no handicap.
"""

from __future__ import annotations

import logging
import subprocess
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
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        try:
            if len(parts) != 5:
                raise ValueError(f"expected 5 fields, got {len(parts)}")
            rows.append({
                "uuid": parts[0],
                "name": _short_name(parts[1]),
                "compute_cap": float(parts[2]),
                "total_mb": int(parts[3]),
                "free_mb": int(parts[4]),
            })
        except ValueError as e:
            # Eine still verworfene Zeile (z.B. "[N/A]" bei Treiber-Hickup)
            # ließe die GPU aus der Planung verschwinden, obwohl sie CUDA-
            # sichtbar bleibt — Split-Länge und CUDA-Order desyncen still.
            # Laut scheitern; der Kalibrier-Handler fängt das und restartet
            # llama-swap im finally.
            raise RuntimeError(
                f"nvidia-smi returned an unparseable GPU row ({line!r}): {e}"
                " — refusing to plan with an incomplete GPU list"
            ) from e
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


def gpu_label(
    gpu: GPU, position: int, labels: dict[str, str] | None = None
) -> str:
    """nvidia-smi-anchored label for a GPU: ``"GPU<smi_idx> (<name>)"``.

    All calibration display uses this so a card carries the SAME number in
    the log, the llama-swap config comments AND ``nvidia-smi`` — instead of
    the compute-sorted list position, which disagrees for same-compute
    cards (the 3× V100 tie-break by UUID vs. by PCI index). Falls back to
    the caller's ``position`` + name when nvidia-smi is unavailable.

    Pass a precomputed ``labels`` map (from :func:`gpu_uuid_labels`) when
    labelling several GPUs in a loop to avoid re-querying nvidia-smi per
    call.
    """
    if labels is None:
        labels = gpu_uuid_labels()
    return labels.get(gpu.uuid, f"GPU{position} ({gpu.name})")


def format_gpu_positions(
    positions: "Any", gpus: "list[GPU]", labels: dict[str, str] | None = None
) -> str:
    """Format a list of compute-sorted GPU positions as nvidia-smi labels
    for a log line, e.g. ``[0, 2]`` → ``"GPU0 (RTX 8000), GPU2 (RTX 8000)"``.

    Keeps the ``active GPUs [...]`` lines on the same nvidia-smi anchor as
    every other label. Pass a precomputed ``labels`` map to reuse it.
    """
    if labels is None:
        labels = gpu_uuid_labels()
    return ", ".join(gpu_label(gpus[i], i, labels) for i in positions)


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

    # first_in_class: NUR die engste Karte der HÖCHSTEN Compute-Klasse.
    # Sie ist in der Füll-Reihenfolge (compute DESC, via CUDA_VISIBLE_
    # DEVICES gepinnt) CUDA-Device 0 und trägt als einzige die llama.cpp-
    # Main-Device-Puffer (Logits/Output, Compute-Workspace, MTP-Draft) —
    # der Handicap existiert genau EINMAL pro Hardware-Konstellation.
    # Früher wurde pro Klasse eine Karte markiert (Einzelkarten immer):
    # das in Klasse 0 gemessene Handicap wurde dann auch von der ersten
    # V100/P40 abgezogen, die diese Puffer gar nicht hat — der Planer
    # verschenkte dort Layer-Platz. Tie-Break bei gleichem free_mb:
    # lexikographisch kleinste UUID (deterministisch, kein besseres Signal).
    first_uuids: set[str] = set()
    fastest_cc = next(
        (cc for cc, idx in class_of_compute.items() if idx == 0), None,
    )
    if fastest_cc is not None:
        fastest_group = [
            g for g in rows if float(g["compute_cap"]) == fastest_cc
        ]
        tightest = min(fastest_group, key=lambda g: (g["free_mb"], g["uuid"]))
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
