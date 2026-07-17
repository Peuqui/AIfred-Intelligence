"""Shared dataclasses for the llama.cpp calibration pipeline.

All modules in this package consume and produce these types.  Keeping them
in one place avoids circular imports and makes the data flow explicit:

    gpu.py        -> GPU, Budget
    projection.py -> VRamPoint, VRamModel
    optimizer.py  -> Candidate (from VRamModel + Budget)
    verifier.py   -> physically verified Candidate + remaining_budget
    flow.py       -> final Result
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional


@dataclass(frozen=True)
class GPU:
    """A single physical GPU, identified by its permanent NVIDIA UUID.

    The UUID is hardware-bound: surviving slot moves, driver updates and
    enumeration-order changes. Calibration writes ``CUDA_VISIBLE_DEVICES``
    with UUIDs (not indices), so llama-server sees the GPUs in the exact
    order calibration intended — no FASTEST_FIRST/PCI_BUS_ID lottery.

    GPUs are produced by :func:`enumerate_gpus` already sorted by
    ``compute_cap DESC`` (highest compute first). The position in that
    list is calibration's local index, used directly as a tensor-split
    slot — no separate cuda_id / smi_index mapping needed.
    """
    uuid: str           # GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    name: str           # short label e.g. "RTX 8000", "V100"
    compute_cap: float  # 7.5 (Turing), 7.0 (Volta), 6.1 (Pascal), ...
    total_mb: int
    free_mb: int
    # Derived helpers, populated by enumerate_gpus():
    # speed_class: rank of this GPU's compute_cap among visible classes;
    #              0 = highest. Used by the optimizer for homogeneous-fill
    #              ("two RTX 8000 first, then spill to V100").
    # first_in_class: True ONLY for the tightest GPU of the HIGHEST
    #                 compute class (= CUDA device 0 in the pinned fill
    #                 order) — the one card carrying llama.cpp's
    #                 main-device buffers; gets the first-GPU handicap.
    #                 Exactly one GPU per hardware constellation.
    speed_class: int
    first_in_class: bool


@dataclass(frozen=True)
class Model:
    """GGUF metadata needed for calibration.

    mb_per_layer is the naive average (model_size / total_layers) — good
    enough as a starting estimate for layer-split, but NOT used for VRAM
    projection (that comes from llama-fit-params).
    """
    model_id: str
    gguf_path: Path
    native_context: int
    total_layers: int
    size_mb: float
    mb_per_layer: float
    quantization: str


@dataclass(frozen=True)
class Budget:
    """Effective VRAM budget per GPU after baseline reservations.

    per_gpu_free: free VRAM measured before the model loads (nvidia-smi).
    first_gpu_handicap: MiB subtracted from CUDA0 in its speed class.
                       Covers display/system overhead that makes CUDA0
                       hold less than an identically-named sibling.
    safety_margin: MiB kept reserved on every GPU for CUDA kernels and
                   KV fragmentation — never consumed by the optimizer.
    """
    per_gpu_free: tuple[int, ...]
    first_gpu_handicap: int
    safety_margin: int
    # Side-Channel-Reserven (TTS/VLM) pro GPU in MiB, GPU-Reihenfolge wie
    # ``per_gpu_free``. Bei der PLANUNG sind sie bereits aus dem freien
    # VRAM herausgerechnet (gpus.free_mb wurde vor build_budget reduziert)
    # — bei den PROBE-MESSUNGEN aber nicht: Die Side-Channels sind während
    # der Probes per Vertrag entladen, das physisch gemessene "frei"
    # enthält ihren Platz also noch. verify() zieht diesen Vektor von den
    # Messwerten ab, damit Fits-Check und Refine dieselbe Wahrheit sehen
    # wie die Planung. () = keine Reserven (Basis-Kalibrierung).
    gpu_reserve_mb: tuple[int, ...] = ()


@dataclass(frozen=True)
class VRamPoint:
    """One fit-params measurement: per-GPU used/free at a specific context."""
    context: int
    per_gpu_used_mb: tuple[int, ...]
    per_gpu_free_mb: tuple[int, ...]


@dataclass(frozen=True)
class VRamModel:
    """Linear VRAM model fitted from two fit-params points.

        used_mb(ctx) = intercept_mb[i] + slope_mb_per_tok[i] * ctx

    slope_mb_per_tok[i] = 0 on GPUs that are not receiving context
    (e.g. GPUs outside the active tensor-split).

    Identifies the configuration this model was fitted for so candidates
    can be compared across n_gpus / kv_quant variations.
    """
    n_gpus: int
    kv_quant: str
    ngl: int
    tensor_split: tuple[float, ...]
    intercept_mb: tuple[float, ...]
    slope_mb_per_tok: tuple[float, ...]
    low_point: VRamPoint
    high_point: VRamPoint


@dataclass(frozen=True)
class Candidate:
    """A math-derived calibration candidate — not yet physically verified.

    ``max_context`` is the largest ctx at which every active GPU keeps
    ``>= safety_margin`` MiB free given the VRAM model and budget.
    ``predicted_free_mb`` holds the predicted headroom per GPU at that
    ``max_context`` — used for the per-GPU debug output (not just the
    min).  GPUs with no layers in this candidate appear as 0.
    """
    mode: Literal["gpu", "hybrid"]
    n_gpus: int
    kv_quant: str
    ngl: int
    tensor_split: tuple[float, ...]      # integer-valued, len == total GPUs
    max_context: int
    predicted_free_mb: tuple[int, ...]   # per-GPU predicted free at max_context
    vram_model: Optional[VRamModel]


@dataclass(frozen=True)
class Result:
    """Final calibration result — one per variant (base, speed, tts-*)."""
    variant: Literal["base", "speed", "tts-xtts", "tts-moss", "tts-qwen3local", "tts-fishspeech"]
    mode: Literal["gpu", "hybrid"]
    context: int
    ngl: int
    kv_quant: str
    tensor_split: tuple[float, ...]
    num_gpus: int
    thinks: bool
    # VRAM left free on each GPU after the model loaded at `context`
    # — consumed by TTS variants to redo projection with a tighter budget.
    remaining_free_mb: tuple[int, ...] = field(default_factory=tuple)
