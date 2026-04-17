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
from typing import Literal


@dataclass(frozen=True)
class GPU:
    """A single CUDA device as seen by calibration.

    cuda_id is the CUDA_DEVICE_ORDER=FASTEST_FIRST index (CUDA0 = fastest).
    speed_class groups GPUs by model name: 0 = fastest group, 1 = next, ...
    first_in_class is True for the physical CUDA0 within its group — that
    GPU typically carries display/system overhead and should be given a
    small VRAM handicap in the optimizer.
    """
    cuda_id: int
    name: str
    total_mb: int
    free_mb: int
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

    def predict_free_mb(
        self, ctx: int, gpu_total_mb: tuple[int, ...],
    ) -> tuple[float, ...]:
        """Predict per-GPU free MiB at the given context."""
        return tuple(
            gpu_total_mb[i] - (self.intercept_mb[i] + self.slope_mb_per_tok[i] * ctx)
            for i in range(len(self.intercept_mb))
        )


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
    vram_model: VRamModel

    @property
    def projected_min_free_mb(self) -> int:
        active = [f for f, r in zip(self.predicted_free_mb, self.tensor_split) if r > 0]
        return min(active) if active else 0


@dataclass(frozen=True)
class Result:
    """Final calibration result — one per variant (base, speed, tts-*)."""
    variant: Literal["base", "speed", "tts-xtts", "tts-moss"]
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
