"""Run llama-fit-params and fit a linear VRAM model per GPU.

llama-fit-params is a companion binary to llama-server that reads the
GGUF header, applies the same memory-planning math the server uses and
prints per-GPU totals — without ever loading the weights.  It runs in
~1–2 seconds and takes no GPU compute, so we can fan out many projections
in parallel.

Two measurements at different contexts give us a linear VRAM model per
GPU::

        used_mb(ctx) = intercept + slope * ctx

From that model, ``max_context_for_budget`` solves analytically for the
largest context that keeps every active GPU above the safety margin.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
from pathlib import Path
from typing import Iterable

from ..logging_utils import log_message
from .llamaswap_io import has_tensor_split, set_tensor_split
from .types import VRamModel, VRamPoint

logger = logging.getLogger(__name__)


# Flags that influence GPU VRAM projection — forwarded to fit-params.
# All other flags (port, mlock, threads, sampling, ...) are irrelevant
# because fit-params never spawns a CUDA context for real inference.
_GPU_FLAGS: frozenset[str] = frozenset({
    "-ngl", "--flash-attn", "-ctk", "-ctv",
    "-ts", "--tensor-split", "-sm", "--split-mode",
    "-np", "-ub", "-b",
    "--rpc",
})


# Limits parallel fit-params invocations so we don't spawn dozens of
# CUDA init sequences at once; 4 is a sweet spot on the MiniPC's 4 GPUs.
_MAX_PARALLEL_FIT = 4


class FitParamsError(RuntimeError):
    """Raised when llama-fit-params cannot be parsed or exits non-zero."""


def _fit_binary(server_bin: str) -> Path:
    """Derive llama-fit-params path from the llama-server binary path."""
    return Path(server_bin).parent / "llama-fit-params"


def _build_fit_cmd(
    full_cmd: str, gguf_path: Path, context: int,
    ngl: int | None = None,
) -> list[str]:
    """Extract GPU-relevant flags and build the fit-params argv."""
    parts = shlex.split(full_cmd)
    if not parts:
        raise FitParamsError("Empty llama-server cmd")
    argv: list[str] = [
        str(_fit_binary(parts[0])),
        "--model", str(gguf_path),
        "-c", str(context),
    ]
    i = 1  # skip binary
    while i < len(parts):
        if parts[i] in _GPU_FLAGS and i + 1 < len(parts):
            if ngl is not None and parts[i] == "-ngl":
                i += 2
                continue
            argv.extend([parts[i], parts[i + 1]])
            i += 2
        else:
            i += 1
    if ngl is not None:
        argv.extend(["-ngl", str(ngl)])
    return argv


def _parse_fit_output(text: str) -> dict[int, tuple[int, int]]:
    """Parse fit-params output into ``{cuda_id: (used_mb, free_mb)}``.

    Understands both formats:
      * multi-GPU:  ``CUDA0 (RTX 8000):  45355 total, 43710 used, 1478 free``
      * single-GPU: ``projected to use 15011 MiB ... will leave 8273 >= 1024``
    """
    result: dict[int, tuple[int, int]] = {}

    for match in re.finditer(
        r"(CUDA(\d+))\s+\([^)]+\)\s*:\s*\d+\s+total\s*,\s*"
        r"(\d+)\s+used\s*,\s*(-?\d+)\s+free",
        text,
    ):
        cuda_id = int(match.group(2))
        result[cuda_id] = (int(match.group(3)), int(match.group(4)))

    if not result:
        proj = re.search(
            r"projected to use\s+(\d+)\s+MiB.*?vs\.\s+(\d+)\s+MiB", text,
        )
        leave = re.search(r"will leave\s+(-?\d+)", text)
        if proj:
            used = int(proj.group(1))
            free = int(leave.group(1)) if leave else int(proj.group(2)) - used
            result[0] = (used, free)

    return result


async def _run_fit(
    argv: list[str], timeout: float = 15.0,
) -> dict[int, tuple[int, int]]:
    """Spawn llama-fit-params and parse its output."""
    env = os.environ.copy()
    env["CUDA_DEVICE_ORDER"] = "FASTEST_FIRST"
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise FitParamsError(f"fit-params timeout: {' '.join(argv)}") from exc

    text = stdout.decode("utf-8", errors="replace")
    parsed = _parse_fit_output(text)
    if not parsed:
        head = text.strip().splitlines()[:3]
        raise FitParamsError(
            f"fit-params: no GPU projection in output: {head}"
        )
    return parsed


async def project(
    full_cmd: str, gguf_path: Path, context: int, ngl: int = 99,
    n_gpus: int | None = None,
) -> VRamPoint:
    """Single fit-params projection at a specific context.

    ``n_gpus`` caps the result tuple length (pads missing CUDAs with 0);
    when ``None`` the tuple length matches what fit-params reported.
    """
    argv = _build_fit_cmd(full_cmd, gguf_path, context, ngl=ngl)
    log_message(
        f"fit-params: ctx={context} ngl={ngl} cmd={' '.join(argv[:6])}...",
        category="stats",
    )
    per_gpu = await _run_fit(argv)

    max_id = max(per_gpu) if per_gpu else -1
    length = n_gpus if n_gpus is not None else max_id + 1
    used = tuple(per_gpu.get(i, (0, 0))[0] for i in range(length))
    free = tuple(per_gpu.get(i, (0, 0))[1] for i in range(length))
    return VRamPoint(context=context, per_gpu_used_mb=used, per_gpu_free_mb=free)


async def project_parallel(
    calls: Iterable[tuple[str, Path, int, int]],
) -> list[VRamPoint | None]:
    """Run many ``project`` calls with a bounded concurrency.

    ``calls`` items: ``(full_cmd, gguf_path, context, ngl)``.
    Returns results in submission order.  Failed projections are
    replaced with ``None`` in the list so the caller can decide how to
    handle partial failures (we prefer that over crashing the whole sweep).
    """
    sem = asyncio.Semaphore(_MAX_PARALLEL_FIT)

    async def _one(
        full_cmd: str, gguf: Path, ctx: int, ngl: int,
    ) -> VRamPoint | None:
        async with sem:
            try:
                return await project(full_cmd, gguf, ctx, ngl=ngl)
            except FitParamsError as e:
                logger.warning(f"fit-params failed (ctx={ctx}, ngl={ngl}): {e}")
                return None

    results: list[VRamPoint | None] = list(
        await asyncio.gather(*(_one(*c) for c in calls))
    )
    return results


def fit_linear_model(
    low: VRamPoint, high: VRamPoint,
    n_gpus: int, kv_quant: str, ngl: int,
    tensor_split: tuple[float, ...],
) -> VRamModel:
    """Fit per-GPU (intercept, slope) from two VRamPoints."""
    dctx = high.context - low.context
    if dctx <= 0:
        raise ValueError(
            f"Cannot fit model: low.context={low.context} >= "
            f"high.context={high.context}"
        )
    length = max(len(low.per_gpu_used_mb), len(high.per_gpu_used_mb))

    def _at(pt: VRamPoint, i: int) -> int:
        return pt.per_gpu_used_mb[i] if i < len(pt.per_gpu_used_mb) else 0

    slopes: list[float] = []
    intercepts: list[float] = []
    for i in range(length):
        d = _at(high, i) - _at(low, i)
        slope = max(0.0, d / dctx)  # VRAM should only grow with context
        intercept = _at(low, i) - slope * low.context
        slopes.append(slope)
        intercepts.append(intercept)

    return VRamModel(
        n_gpus=n_gpus,
        kv_quant=kv_quant,
        ngl=ngl,
        tensor_split=tensor_split,
        intercept_mb=tuple(intercepts),
        slope_mb_per_tok=tuple(slopes),
        low_point=low,
        high_point=high,
    )


def max_context_for_budget(
    model: VRamModel,
    gpu_total_mb: tuple[int, ...],
    baseline_used_mb: tuple[int, ...],
    per_gpu_handicap_mb: tuple[int, ...],
    safety_margin_mb: int,
    ceiling: int,
    precision: int = 256,
) -> tuple[int, int]:
    """Solve analytically for the largest context the budget allows.

    For every GPU ``i`` with slope > 0 and an active split share we need::

        baseline_used[i] + intercept[i] + slope[i]*ctx
            <= gpu_total_mb[i] - safety_margin - handicap[i]

    The minimum of the per-GPU ``ctx`` limits is the answer, rounded
    down to ``precision`` tokens (matches llama.cpp's internal rounding).
    Returns ``(ctx, tightest_free_mb_predicted_at_ctx)``.
    """
    ctx_limits: list[float] = []
    for i, slope in enumerate(model.slope_mb_per_tok):
        if slope <= 0:
            continue
        if i >= len(model.tensor_split) or model.tensor_split[i] <= 0:
            continue
        allowance = (
            gpu_total_mb[i]
            - baseline_used_mb[i]
            - model.intercept_mb[i]
            - safety_margin_mb
            - per_gpu_handicap_mb[i]
        )
        if allowance <= 0:
            ctx_limits.append(0.0)
        else:
            ctx_limits.append(allowance / slope)

    if not ctx_limits:
        return 0, 0

    max_ctx = min(min(ctx_limits), float(ceiling))
    max_ctx_int = max(0, int(max_ctx // precision) * precision)

    # Predicted min free at chosen ctx (for ranking candidates)
    min_free = min(
        (
            gpu_total_mb[i]
            - baseline_used_mb[i]
            - model.intercept_mb[i]
            - model.slope_mb_per_tok[i] * max_ctx_int
        )
        for i in range(len(model.slope_mb_per_tok))
        if model.slope_mb_per_tok[i] > 0
        and i < len(model.tensor_split)
        and model.tensor_split[i] > 0
    )

    return max_ctx_int, int(min_free)


def adjust_cmd_for_projection(
    full_cmd: str, tensor_split: tuple[float, ...], kv_quant: str,
) -> str:
    """Return a cmd with the desired tensor-split and KV-quant set.

    Used right before ``project()`` so every sweep cell has a consistent
    cmd template.  KV quant "f16" strips ``-ctk/-ctv`` (project unbiased).
    """
    # Avoid import loop with llamaswap_io.set_kv_quant
    from .llamaswap_io import set_kv_quant

    cmd = full_cmd
    if tensor_split:
        cmd = set_tensor_split(cmd, list(tensor_split))
    elif not has_tensor_split(cmd):
        # Fit-params handles single-GPU fine without -ts, nothing to do
        pass
    cmd = set_kv_quant(cmd, kv_quant)
    return cmd
