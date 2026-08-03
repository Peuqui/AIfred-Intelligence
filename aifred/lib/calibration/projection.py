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
    """Extract GPU-relevant flags and build the fit-params argv.

    Always sets ``--fit-print on`` (required since llama.cpp b8857 to get
    the per-device VRAM table; default ``off`` prints only fitted CLI args).
    Translates legacy ``ngl=99`` to ``-ngl all`` — recent fit-params builds
    abort when a numeric ngl is set explicitly together with auto-fit.
    """
    parts = shlex.split(full_cmd)
    if not parts:
        raise FitParamsError("Empty llama-server cmd")
    argv: list[str] = [
        str(_fit_binary(parts[0])),
        "--model", str(gguf_path),
        "-c", str(context),
        "--fit-print", "on",
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
        # fit-params accepts "auto", "all", or an exact number. "all" means
        # all layers on GPUs; large integers (99) trigger an abort.
        ngl_arg = "all" if ngl >= 99 else str(ngl)
        argv.extend(["-ngl", ngl_arg])
    return argv


def _parse_fit_output(text: str) -> dict[int, int]:
    """Parse fit-params output into ``{cuda_id: used_mb}``.

    Output format from llama.cpp b8857+::

        I llama_fit_params: printing estimated memory in MiB to stdout
                            (device, model, context, compute) ...
        CUDA0 31683 661 505
        CUDA1 10739 251 234
        Host  2425  0   84

    ``used_mb = model + context + compute`` per device.  ``Host`` rows are
    ignored — only GPU rows feed the optimizer.  Free-MB is derived later
    from the GPU hardware totals (fit-params no longer reports free).
    """
    result: dict[int, int] = {}
    for match in re.finditer(
        r"^CUDA(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$",
        text, flags=re.MULTILINE,
    ):
        cuda_id = int(match.group(1))
        used = int(match.group(2)) + int(match.group(3)) + int(match.group(4))
        result[cuda_id] = used
    return result


async def _run_fit(
    argv: list[str], timeout: float = 15.0,
    env_override: dict[str, str] | None = None,
) -> dict[int, int]:
    """Spawn llama-fit-params and parse its output.

    ``env_override`` lets the caller pin the GPU set via
    ``CUDA_VISIBLE_DEVICES=<uuid-list>``. When omitted, fit-params sees
    whatever GPUs the parent process has visible.

    Returns ``{cuda_id: used_mb}``.
    """
    env = os.environ.copy()
    if env_override:
        env.update(env_override)
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


def _mmproj_path(full_cmd: str) -> Path | None:
    """Pfad der ``--mmproj``-Datei aus einem llama-server cmd — SSOT fürs
    Parsing (Projektion + Encode-Burn-In im Verifier). ``None`` wenn das
    Flag fehlt."""
    tokens = shlex.split(full_cmd)
    for i, tok in enumerate(tokens[:-1]):
        if tok == "--mmproj":
            return Path(tokens[i + 1])
    return None


def _mmproj_extra_mb(full_cmd: str) -> int:
    """VRAM-Anteile eines Vision-Modells, die llama-fit-params nicht
    modellieren kann — das Tool kennt ``--mmproj`` nicht, weshalb das Flag
    in ``_GPU_FLAGS`` bewusst NICHT weitergereicht wird. Ohne diese
    Korrektur hält die Mathe Vision-Modelle für kleiner als sie sind
    (35B, 2026-07-31: ~1 GB Projektor → zwei aussichtslose
    Single-GPU-Probes am nativen Kontext).

    Zwei Summanden: die mmproj-Gewichte (= Dateigröße, exakt) plus der
    per Burn-In gemessene Encode-Buffer-Peak aus
    :mod:`aifred.lib.mmproj_encode_vram_cache` (0 bis zur ersten Messung
    — dann fängt ihn der adaptive Bias). 0 wenn kein ``--mmproj`` im cmd
    oder die Datei fehlt.
    """
    from ..mmproj_encode_vram_cache import get as _encode_peak_cached

    mm = _mmproj_path(full_cmd)
    if mm is None:
        return 0
    try:
        weights_mb = int(mm.stat().st_size // (1024 * 1024))
    except OSError:
        return 0
    return weights_mb + (_encode_peak_cached(mm) or 0)


def _first_active_slot(full_cmd: str, length: int) -> int:
    """Slot der ersten GPU mit Layern laut ``--tensor-split`` im cmd —
    dort legt llama.cpp die mmproj-Gewichte ab (Main-Device der
    Layer-Kette; empirisch verifiziert am 35B: Split 0:41 → mmproj auf
    Slot 1, Split 41:0 → Slot 0). Ohne tensor-split: Slot 0."""
    m = re.search(r"(?:--tensor-split|-ts)\s+([\d.,]+)", full_cmd)
    if m:
        for i, part in enumerate(m.group(1).split(",")[:length]):
            try:
                if float(part) > 0:
                    return i
            except ValueError:
                break
    return 0


def _draft_path(full_cmd: str) -> Path | None:
    """Pfad des Draft-Sidecar-GGUFs (``--model-draft``/``-md``) aus einem
    llama-server cmd — Spec-Decoding mit separatem Draft-Modell (DSpark,
    EAGLE3, DFlash). ``None`` wenn das Flag fehlt."""
    tokens = shlex.split(full_cmd)
    for i, tok in enumerate(tokens[:-1]):
        if tok in ("--model-draft", "-md", "--spec-draft-model"):
            return Path(tokens[i + 1])
    return None


def _draft_device_slot(full_cmd: str, length: int) -> int:
    """Slot der GPU, die das Draft-Sidecar trägt. Explizit gesetztes
    ``--device-draft CUDAn`` gewinnt; sonst die LETZTE aktive Karte laut
    ``--tensor-split`` — dort liegt der Output-Layer des Hauptmodells,
    und das Draft MUSS auf dessen Device (sonst ggml-Assert
    "pre-allocated tensor (output.weight)…"; empirisch DeepSeek-V4-Flash
    2026-08-03)."""
    m = re.search(r"(?:--device-draft|--spec-draft-device|-devd)\s+CUDA(\d+)", full_cmd)
    if m:
        slot = int(m.group(1))
        return slot if slot < length else max(length - 1, 0)
    last = 0
    m = re.search(r"(?:--tensor-split|-ts)\s+([\d.,]+)", full_cmd)
    if m:
        for i, part in enumerate(m.group(1).split(",")[:length]):
            try:
                if float(part) > 0:
                    last = i
            except ValueError:
                break
    return last


def _draft_extra_mb(full_cmd: str) -> int:
    """VRAM des Draft-Sidecars, den llama-fit-params nicht modelliert —
    ``--model-draft`` steht bewusst nicht in ``_GPU_FLAGS``, und
    fit-params kann Draft-Head-GGUFs auch nicht selbst laden ("failed
    to create llama_context", verifiziert am DSpark-Sidecar 2026-08-03).
    Gleiche Mechanik wie :func:`_mmproj_extra_mb`: Dateigröße (exakt)
    plus konservativer Headroom für Draft-KV + Compute-Puffer
    (``LLAMACPP_DRAFT_SIDECAR_HEADROOM_MB``); den Restfehler fängt der
    adaptive Bias über die realen Verify-Proben, die das Draft im
    selben Prozess ohnehin mitmessen. 0 wenn kein Draft im cmd oder
    die Datei fehlt."""
    from ..config import LLAMACPP_DRAFT_SIDECAR_HEADROOM_MB

    draft = _draft_path(full_cmd)
    if draft is None:
        return 0
    try:
        weights_mb = int(draft.stat().st_size // (1024 * 1024))
    except OSError:
        log_message(f"⚠️ draft projection: file missing: {draft}", category="stats")
        return 0
    return weights_mb + LLAMACPP_DRAFT_SIDECAR_HEADROOM_MB


async def project(
    full_cmd: str, gguf_path: Path, context: int, ngl: int = 99,
    n_gpus: int | None = None,
    env_override: dict[str, str] | None = None,
    gpu_total_mb: tuple[int, ...] | None = None,
) -> VRamPoint:
    """Single fit-params projection at a specific context.

    ``n_gpus`` caps the result tuple length (pads missing CUDAs with 0);
    when ``None`` the tuple length matches what fit-params reported.

    ``env_override`` is forwarded to fit-params — typically used to set
    ``CUDA_VISIBLE_DEVICES=<uuid-list>`` so the projection sees the same
    GPU subset (in the same order) that the LLM will actually use.

    ``gpu_total_mb`` is the per-GPU hardware total in CUDA-visible order.
    Required to compute ``per_gpu_free_mb`` (fit-params no longer reports
    free). When omitted, free defaults to 0 for every GPU.
    """
    argv = _build_fit_cmd(full_cmd, gguf_path, context, ngl=ngl)
    log_message(
        f"fit-params: ctx={context} ngl={ngl} cmd={' '.join(argv[:6])}...",
        category="stats",
    )
    per_gpu_used = await _run_fit(argv, env_override=env_override)

    max_id = max(per_gpu_used) if per_gpu_used else -1
    length = n_gpus if n_gpus is not None else max_id + 1
    used = tuple(per_gpu_used.get(i, 0) for i in range(length))
    # fit-params-blinder mmproj-Anteil auf das Main-Device der Layer-Kette
    # addieren — VOR der free-Ableitung, damit VRamPoints, Intercepts und
    # alle Downstream-Verbraucher die Korrektur automatisch tragen.
    mmproj_mb = _mmproj_extra_mb(full_cmd)
    if mmproj_mb and used:
        slot = _first_active_slot(full_cmd, length)
        if slot < length:
            used = tuple(
                u + mmproj_mb if i == slot else u
                for i, u in enumerate(used)
            )
    # fit-params-blinder Draft-Sidecar-Anteil (DSpark/EAGLE3/DFlash) auf
    # das Draft-Device addieren — gleiche Mechanik wie mmproj: VOR der
    # free-Ableitung, damit alle Downstream-Verbraucher ihn tragen.
    draft_mb = _draft_extra_mb(full_cmd)
    if draft_mb and used:
        slot = _draft_device_slot(full_cmd, length)
        if slot < length:
            used = tuple(
                u + draft_mb if i == slot else u
                for i, u in enumerate(used)
            )
    if gpu_total_mb is not None:
        free = tuple(
            gpu_total_mb[i] - used[i] if i < len(gpu_total_mb) else 0
            for i in range(length)
        )
    else:
        free = tuple(0 for _ in range(length))
    return VRamPoint(context=context, per_gpu_used_mb=used, per_gpu_free_mb=free)


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
