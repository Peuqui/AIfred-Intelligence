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
    proc_env["CUDA_DEVICE_ORDER"] = "FASTEST_FIRST"
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
    except OSError as e:
        os.close(fd)
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
) -> bool:
    """Block until ``/health`` returns 200 or the process dies."""
    url = f"http://localhost:{port}/health"
    start = asyncio.get_event_loop().time()
    while (asyncio.get_event_loop().time() - start) < timeout:
        if process.poll() is not None:
            return False
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(url, timeout=2.0)
                if r.status_code == 200:
                    return True
        except (httpx.RequestError, httpx.TimeoutException):
            pass
        await asyncio.sleep(1.0)
    return False


async def _test_inference(port: int, timeout: float = 120.0) -> bool:
    """Send one tiny request — catches OOM at CUDA-kernel-allocation time.

    On Pascal with tight VRAM the server can pass /health but crash on
    first real inference due to additional kernel allocations.  Giving
    the test 120s handles thinking-mode models that reason before
    producing content.
    """
    url = f"http://localhost:{port}/v1/chat/completions"
    payload = {
        "model": "test",
        "messages": [{"role": "user", "content": "say ok"}],
        "max_tokens": 2,
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
    info = get_all_gpus_memory_info()
    if not info or not info.get("per_gpu"):
        return ()
    # nvidia-smi returns PCI order; we need CUDA (FASTEST_FIRST) order.
    # gpus list is already CUDA-ordered — find each by total_mb match.
    raw = sorted(info["per_gpu"], key=lambda g: g["total_mb"], reverse=True)
    if len(raw) != len(gpus):
        return tuple(int(g["free_mb"]) for g in raw)
    return tuple(int(g["free_mb"]) for g in raw)


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
) -> VerifyResult:
    """Run one physical test: start → inference → measure → kill.

    ``fits`` is True iff every GPU kept >= ``safety_margin_mb`` free.
    ``health_timeout`` overrides the default health-check window — hybrid-mode
    callers should pass a large value because mlock + CPU-offload of a 100+ GB
    GGUF can take multiple minutes before the server is ready.
    """
    from ...backends.ollama import wait_for_vram_stable

    process = await _start_server(full_cmd, context, port, ngl, env)
    if not process:
        return VerifyResult(False, (), None, "OOM (spawn failed)")

    thinks: Optional[bool] = None
    try:
        effective_timeout = health_timeout if health_timeout is not None else LLAMACPP_HEALTH_TIMEOUT
        ready = await _wait_ready(port, effective_timeout, process)
        if not ready:
            output = _read_log(process)
            _kill(process)
            _cleanup_log(process)
            await wait_for_vram_stable(max_wait_seconds=10.0)
            if output:
                logger.error(f"llama-server not ready. Log tail:\n{output[-2000:]}")
            return VerifyResult(False, (), None, "OOM (health timeout)")

        if not await _test_inference(port):
            _kill(process)
            _cleanup_log(process)
            await wait_for_vram_stable(max_wait_seconds=10.0)
            return VerifyResult(False, (), None, "OOM (inference crash)")

        measured = _measured_free(gpus)
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

        if probe_thinking and fits:
            thinks = await _probe_thinking(port)

        _kill(process)
        _cleanup_log(process)
        await wait_for_vram_stable(max_wait_seconds=10.0)

        return VerifyResult(
            fits=fits,
            measured_free_mb=measured if fits else (),
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
