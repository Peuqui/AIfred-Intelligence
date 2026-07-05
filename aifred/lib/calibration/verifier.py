"""Physical verification: start llama-server and measure real VRAM.

Projection gets us close; this module closes the gap by starting the
actual server once, running a short inference to force CUDA kernel
allocation, measuring free VRAM per GPU, and reporting back whether
the chosen (context, tensor-split, ngl, kv-quant) configuration fits.

Thinking-capability can be piggybacked on the same server start to
avoid a second model load.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional

import httpx

from ..config import LLAMACPP_HEALTH_TIMEOUT, THINKING_PROBE_TEMPERATURE
from ..formatting import format_number
from ..gpu_utils import get_all_gpus_memory_info
from ..logging_utils import log_message
from .llamaswap_io import set_context, set_ngl
from .types import GPU

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VerifyResult:
    fits: bool
    measured_free_mb: tuple[int, ...]  # per GPU, in CUDA-order; () when fits=False
    thinks: Optional[bool]             # None if no thinking probe run
    detail: str                        # one-line log-friendly summary
    # CUDA index of the GPU that hit OOM during model load (parsed from
    # llama-server stderr). ``None`` means we have no information — fits
    # might be True, or the server died for a non-OOM reason, or the
    # log tail didn't contain a parseable CUDA-OOM line.
    oom_cuda_id: Optional[int] = None


_OOM_DEVICE_PATTERNS = (
    # "allocating 36864.00 MiB on device 2: cudaMalloc failed: out of memory"
    re.compile(r"on device (\d+):[^\n]*cudaMalloc\s+failed", re.IGNORECASE),
    # "failed to allocate CUDA2 buffer of size ..."
    re.compile(r"failed to allocate\s+CUDA(\d+)\s+buffer", re.IGNORECASE),
    # "CUDA error: out of memory ... device 2"
    re.compile(r"CUDA error:[^\n]*device\s+(\d+)", re.IGNORECASE),
)


def _parse_oom_cuda_id(log_text: str) -> Optional[int]:
    """Find the CUDA device index that hit OOM in llama-server output.

    Scans the log tail for one of the known OOM patterns. Returns the
    first match's device index, or ``None`` if no pattern matches.
    """
    for pattern in _OOM_DEVICE_PATTERNS:
        m = pattern.search(log_text)
        if m:
            try:
                return int(m.group(1))
            except (ValueError, IndexError):
                continue
    return None


def _adjust_cmd(
    full_cmd: str, context: int, port: int, ngl: Optional[int],
) -> str:
    cmd = full_cmd.replace("${PORT}", str(port))
    cmd = set_context(cmd, context)
    if ngl is not None:
        cmd = set_ngl(cmd, ngl)
    # Calibration safety: single slot + disabled fit routine (fit
    # crashes on Pascal under tight VRAM).
    if "-np " not in cmd:
        cmd = cmd.replace(" --port", " -np 1 --port")
    if "-fit " not in cmd:
        cmd = cmd.replace(" --port", " -fit off --port")
    return cmd


async def _start_server(
    full_cmd: str,
    context: int,
    port: int,
    ngl: Optional[int],
    env: Optional[dict[str, str]],
) -> Optional[subprocess.Popen]:
    cmd_str = _adjust_cmd(full_cmd, context, port, ngl)
    args = shlex.split(cmd_str)
    log_message(f"llama-server start: ctx={context} ngl={ngl}", category="stats")
    log_message(f"llama-server cmd: {cmd_str}", category="stats")

    proc_env = os.environ.copy()
    if env:
        proc_env.update(env)

    fd, log_path = tempfile.mkstemp(suffix=".log", prefix="llama_")
    try:
        process = subprocess.Popen(
            args,
            stdout=fd,
            stderr=subprocess.STDOUT,
            env=proc_env,
            preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN),
        )
    except Exception as e:
        # Catch broader than OSError: PermissionError on /tmp quota,
        # NotADirectoryError on unusual mounts, etc. — all of these would
        # have leaked the tmpfile previously.
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(log_path)
        except OSError:
            pass
        logger.error(f"Failed to start llama-server: {e}")
        return None

    os.close(fd)
    process._server_log = log_path  # type: ignore[attr-defined]
    return process


async def _wait_ready(
    port: int, timeout: float, process: subprocess.Popen,
    gpus: Optional[list[GPU]] = None,
) -> tuple[bool, str, tuple[int, ...]]:
    """Block until ``/health`` returns 200 or the process dies.

    Returns (ready, reason, load_min_free) so callers can distinguish a
    true polling timeout from a process death (which is almost always
    real OOM).

    ``load_min_free``: tiefster beobachteter Frei-Stand pro GPU während
    der Load-Phase (Reihenfolge wie ``gpus``; () wenn ``gpus`` fehlt oder
    keine Messung gelang). Stirbt der Server mit OOM, ist das die EINZIGE
    Messung, die es je gab — ohne sie zeigt das Kalibrier-Log nur die
    Split-Schieberei, aber nie, wie eng es auf welcher Karte real wurde.
    NUR fürs Reporting gedacht: Die Werte sind ein Lade-Zwischenstand
    (Karten, die noch gar nicht befüllt waren, wirken leer) und dürfen
    nicht als Steady-State in Refine-Entscheidungen einfließen.
    """
    url = f"http://localhost:{port}/health"
    start = asyncio.get_event_loop().time()
    min_free: dict[int, int] = {}

    def _sample() -> None:
        if not gpus:
            return
        measured = _measured_free(gpus)
        for i, v in enumerate(measured):
            if i not in min_free or v < min_free[i]:
                min_free[i] = v

    def _collected() -> tuple[int, ...]:
        if not gpus or len(min_free) != len(gpus):
            return ()
        return tuple(min_free[i] for i in range(len(gpus)))

    from ..calibration_gate import is_cancel_requested

    while (asyncio.get_event_loop().time() - start) < timeout:
        # User-Abbruch reicht bis in den Minuten-Load hinein: Der Aufrufer
        # (verify) killt den Test-Server im not-ready-Pfad sofort — ohne
        # diesen Check würde "cancel" erst nach dem fertigen Modell-Load
        # greifen (2-5 min bei 100+-GB-Modellen).
        if is_cancel_requested():
            return False, "cancelled by user", _collected()
        rc = process.poll()
        if rc is not None:
            elapsed = int(asyncio.get_event_loop().time() - start)
            return (
                False,
                f"server died after {elapsed}s (exit {rc})",
                _collected(),
            )
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(url, timeout=2.0)
                if r.status_code == 200:
                    return True, "", _collected()
        except (httpx.RequestError, httpx.TimeoutException):
            pass
        await asyncio.to_thread(_sample)
        await asyncio.sleep(1.0)
    return False, f"polling timeout ({int(timeout)}s, server not ready)", _collected()


async def _test_inference(port: int, timeout: float = 120.0) -> bool:
    """Run a non-trivial inference to provoke peak VRAM allocation.

    A 2-token "say ok" probe is enough to catch hard OOM at CUDA-kernel
    init, but not enough to surface peak activation memory: layers fully
    allocate their compute buffers only when handling a longer batch.
    We send a meaningful prompt and ask for ~64 tokens of generation so
    the server hits a steady state before we measure VRAM.
    """
    url = f"http://localhost:{port}/v1/chat/completions"
    payload = {
        "model": "test",
        "messages": [{
            "role": "user",
            "content": (
                "Write a short paragraph about how GPUs are used in machine "
                "learning. Mention CUDA, tensor cores and memory bandwidth."
            ),
        }],
        "max_tokens": 64,
        "temperature": 0.7,
    }
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, timeout=timeout)
            if r.status_code != 200:
                return False
            msg = r.json().get("choices", [{}])[0].get("message", {})
            return bool(msg.get("content") or msg.get("reasoning_content"))
    except (httpx.HTTPError, ValueError, KeyError):
        return False


async def _probe_thinking(port: int) -> bool:
    """Ask a math question and check for reasoning_content / <think>."""
    url = f"http://localhost:{port}/v1/chat/completions"
    payload = {
        "model": "test",
        "messages": [{
            "role": "user",
            "content": "What is 2+3? Think step by step.",
        }],
        "max_tokens": 200,
        "temperature": THINKING_PROBE_TEMPERATURE,
    }
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, timeout=30.0)
            if r.status_code != 200:
                return False
            msg = r.json().get("choices", [{}])[0].get("message", {})
            content = msg.get("content", "")
            return bool(msg.get("reasoning_content")) or "<think>" in content
    except (httpx.HTTPError, ValueError, KeyError):
        return False


def _read_log(process: subprocess.Popen) -> str:
    path = getattr(process, "_server_log", None)
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _cleanup_log(process: subprocess.Popen) -> None:
    path = getattr(process, "_server_log", None)
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


def _kill(process: subprocess.Popen) -> None:
    """Terminate the llama-server child process.

    SIGTERM → wait → SIGKILL → wait. The post-SIGKILL wait must be generous
    enough to cover mmap-cleanup of huge models (--mlock / --direct-io on
    100+ GB GGUF files can take 30+ seconds for the kernel to tear down).
    If reaping still fails, swallow the timeout: the kernel will eventually
    reap the zombie via init, and propagating the exception would crash the
    entire calibration run instead of just discarding the failed config.
    """
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                logger.warning(
                    f"llama-server pid {process.pid} did not reap within 60s "
                    f"after SIGKILL — leaving as zombie, init will reap it."
                )


def _measured_free(gpus: list[GPU]) -> tuple[int, ...]:
    """Return per-GPU free MiB in the order ``gpus`` was passed in.

    Matches nvidia-smi entries to ``gpus`` by UUID — robust against
    PCI-bus reorderings, identical-card pairs and any nvidia-smi
    enumeration quirks.
    """
    info = get_all_gpus_memory_info()
    if not info or not info.get("per_gpu"):
        return ()
    free_by_uuid: dict[str, int] = {}
    for g in info["per_gpu"]:
        uuid = str(g.get("uuid", "")).strip()
        if uuid:
            try:
                free_by_uuid[uuid] = int(g["free_mb"])
            except (KeyError, ValueError):
                continue
    # If any GPU we asked about is missing from this nvidia-smi snapshot,
    # treat the whole measurement as invalid. Falling back to g.free_mb
    # would mix post-load readings with the pre-load baseline captured
    # at calibration start and could yield a false-positive fit.
    if not all(g.uuid in free_by_uuid for g in gpus):
        return ()
    return tuple(free_by_uuid[g.uuid] for g in gpus)


async def verify(
    full_cmd: str,
    context: int,
    port: int,
    gpus: list[GPU],
    safety_margin_mb: int,
    ngl: Optional[int] = None,
    env: Optional[dict[str, str]] = None,
    probe_thinking: bool = False,
    health_timeout: Optional[float] = None,
    reserve_mb: tuple[int, ...] = (),
) -> VerifyResult:
    """Run one physical test: start → inference → measure → kill.

    ``fits`` is True iff every GPU kept >= ``safety_margin_mb`` free.
    ``health_timeout`` overrides the default health-check window — hybrid-mode
    callers should pass a large value because mlock + CPU-offload of a 100+ GB
    GGUF can take multiple minutes before the server is ready.

    ``reserve_mb`` (per GPU, same order as ``gpus``): Side-Channel-Reserven
    (TTS/VLM), die während der Probe physisch NICHT belegt sind, im Betrieb
    aber zurückkehren. Wird direkt von den Messwerten abgezogen, BEVOR
    irgendeine Entscheidung fällt — Fits-Check, Refine und Shrink sehen so
    dieselbe Wahrheit wie die reserve-bewusste Planung. Ohne diesen Abzug
    optimiert der Refine-Loop den Kontext in den (scheinbar freien)
    Reserve-Platz hinein und das Profil OOMt im Betrieb, sobald TTS/VLM
    ihren Platz zurückfordern.
    """
    from ...backends.ollama import wait_for_vram_stable
    from ..calibration_gate import is_cancel_requested

    # Cancel-Gate VOR dem Server-Spawn: Nach einem abgebrochenen Schritt
    # würde der Refine-Loop sonst noch einen Shift + neuen Minuten-Load
    # starten, bevor der äußere 2-s-Check den Lauf beendet.
    if is_cancel_requested():
        return VerifyResult(False, (), None, "cancelled by user")

    process = await _start_server(full_cmd, context, port, ngl, env)
    if not process:
        return VerifyResult(False, (), None, "spawn failed")

    thinks: Optional[bool] = None
    try:
        effective_timeout = health_timeout if health_timeout is not None else LLAMACPP_HEALTH_TIMEOUT
        ready, reason, load_min_free = await _wait_ready(
            port, effective_timeout, process, gpus=gpus,
        )
        if not ready:
            output = _read_log(process)
            _kill(process)
            _cleanup_log(process)
            await wait_for_vram_stable(max_wait_seconds=10.0)
            oom_cuda_id: Optional[int] = None
            if output and not reason.startswith("cancelled"):
                logger.error(f"llama-server not ready. Log tail:\n{output[-2000:]}")
                # Distinguish OOM vs other crashes by scanning the log tail.
                tail = output[-4000:]
                tail_lower = tail.lower()
                if "out of memory" in tail_lower or "cudamalloc" in tail_lower or "cuda error" in tail_lower:
                    reason = f"OOM during load ({reason})"
                    oom_cuda_id = _parse_oom_cuda_id(tail)
                    if oom_cuda_id is not None:
                        reason += f" — CUDA{oom_cuda_id}"
            # Load-Phase-Messwerte ins Reporting: Ohne sie zeigt das Log bei
            # einem Load-OOM nur die Split-Schieberei, aber nie, wie eng es
            # auf welcher Karte real wurde. Bewusst NUR im detail-Text —
            # als measured_free_mb würden diese Lade-Zwischenstände den
            # measurement-based Refine fehlleiten (noch unbefüllte Karten
            # wirken leer).
            if load_min_free:
                reason += " | min free during load: " + ", ".join(
                    f"{g.name}: {format_number(load_min_free[i])} MB"
                    for i, g in enumerate(gpus) if i < len(load_min_free)
                )
            return VerifyResult(False, (), None, reason, oom_cuda_id=oom_cuda_id)

        if not await _test_inference(port):
            _kill(process)
            _cleanup_log(process)
            await wait_for_vram_stable(max_wait_seconds=10.0)
            return VerifyResult(False, (), None, "OOM (inference crash)")

        # Wait for VRAM to actually stabilise after inference — without
        # this, nvidia-smi can return mid-cleanup numbers (one GPU still
        # holding activations, another already freed). wait_for_vram_stable
        # polls until consecutive readings agree, so it returns as soon
        # as the picture is stable instead of a fixed sleep.
        await wait_for_vram_stable(max_wait_seconds=8.0)
        measured = _measured_free(gpus)
        reserve_applied = False
        if measured and reserve_mb and any(reserve_mb):
            # Effektives Frei = gemessen − Side-Channel-Reserve. Kann
            # negativ werden (Probe hat den Reserve-Platz gefressen) —
            # genau dann MUSS fits=False herauskommen.
            measured = tuple(
                f - (reserve_mb[i] if i < len(reserve_mb) else 0)
                for i, f in enumerate(measured)
            )
            reserve_applied = True
        if not measured:
            fits = True
            detail = "VRAM unknown"
        else:
            min_free = min(measured)
            fits = min_free >= safety_margin_mb
            detail = ", ".join(
                f"{g.name}: {measured[i]} MB"
                for i, g in enumerate(gpus) if i < len(measured)
            )
            if reserve_applied:
                detail += " (eff., side-channel reserve deducted)"

        if probe_thinking and fits:
            thinks = await _probe_thinking(port)

        _kill(process)
        _cleanup_log(process)
        await wait_for_vram_stable(max_wait_seconds=10.0)

        # Always return measured values (even when fits=False) — callers
        # like the AI calibration agent need to see *which* GPU is tight
        # to decide if it's a real OOM or just a margin miss.
        return VerifyResult(
            fits=fits,
            measured_free_mb=measured,
            thinks=thinks,
            detail=detail,
        )
    except BaseException:
        _kill(process)
        _cleanup_log(process)
        raise


async def kill_orphan_on_port(port: int) -> None:
    """Best-effort cleanup of a leftover llama-server holding ``port``."""
    try:
        result = subprocess.run(
            ["fuser", "-k", f"{port}/tcp"],
            capture_output=True, timeout=5, check=False,
        )
        if result.returncode == 0:
            logger.info(f"Killed orphan on port {port}")
    except (OSError, subprocess.SubprocessError):
        pass


# Regex exposed for callers that inspect llama-server logs directly
_MEMORY_BREAKDOWN_RE = re.compile(
    r"llama_memory_breakdown_print:\s*\|\s+-\s+(CUDA\d+)\s+\([^)]+\)\s*\|\s*"
    r"(\d+)\s*=\s*(\d+)\s*\+\s*\(\s*(\d+)"
)
