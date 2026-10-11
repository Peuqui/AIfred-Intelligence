"""TTS Engine Manager — TTS engine lifecycle (start/stop/detect, refcount, keep-alive).

Docker container start/stop, running-engine detection, VRAM footprint of the
local engines and the pipeline refcount that protects an engine from a
concurrent ``stop_engine``. The LLM<->TTS VRAM coordination lives in
``tts_llm_coordination``.
"""

from __future__ import annotations

import threading
from collections import defaultdict

from .logging_utils import log_message
from .tts_engines import gpu_engine_keys


# HTTP timeout for TTS health checks (seconds).
# Normal response: <100ms. This is only a safety net for hung connections.
TTS_HEALTH_CHECK_TIMEOUT = 5


# =============================================================================
# TTS engine refcount — protects active pipelines from stop_engine() races.
# =============================================================================
#
# Use case: a FreeEcho.2 request acquires a GPU engine at the start of its pipeline and
# releases it after the audio is sent. If the browser (or another channel)
# calls ensure_tts_state() with `wanted_tts` of another engine while the FreeEcho.2 is mid-
# pipeline, the stop_engine() call would tear down the container under the
# FreeEcho.2's feet. The refcount makes ensure_tts_state() skip the stop while any
# active holder still needs the engine.
#
# Counter — NOT a binary lock — so multiple concurrent FreeEcho.2 speakers coexist cleanly.
# Idle behaviour is unchanged: when nothing holds the engine, ensure_tts_state
# may stop it, and the container's KEEP_ALIVE will eventually shut it down on
# its own.
_engine_refs: defaultdict[str, int] = defaultdict(int)
_engine_refs_lock = threading.Lock()


def acquire_tts(engine: str) -> None:
    """Mark a TTS engine as in-use by an active pipeline.

    Idempotent across pipelines — call once per pipeline at start, paired
    with exactly one ``release_tts(engine)`` in a ``finally`` block. The
    counter is allowed to grow above 1 when multiple concurrent pipelines
    use the same engine.
    """
    if engine not in gpu_engine_keys():
        return
    with _engine_refs_lock:
        _engine_refs[engine] += 1


def release_tts(engine: str) -> None:
    """Release one acquisition of a TTS engine.

    Safe to call even if the engine was not acquired (no-op below 0).
    Pipelines must put this in a ``finally`` block to survive crashes
    and external cancellations (e.g. FreeEcho.2 Action-Button stop event).
    """
    if engine not in gpu_engine_keys():
        return
    with _engine_refs_lock:
        _engine_refs[engine] = max(0, _engine_refs[engine] - 1)


def is_tts_in_use(engine: str) -> bool:
    """True if any pipeline currently holds an acquisition for this engine."""
    if engine not in gpu_engine_keys():
        return False
    with _engine_refs_lock:
        return _engine_refs[engine] > 0


def get_tts_refcount(engine: str) -> int:
    """Current acquisition count (debug/inspection helper)."""
    if engine not in gpu_engine_keys():
        return 0
    with _engine_refs_lock:
        return _engine_refs[engine]


async def tts_keepalive_loop(
    engines: list[str],
    interval: int | None = None,
    on_warn: object = None,
) -> None:
    """Ping ``/keep_alive`` on each given GPU TTS engine while a pipeline runs.

    Each ping resets the container-internal idle timer to its full window.
    Used by the FreeEcho.2 channel and the browser pipeline to prevent the
    engine from shutting down mid-flight during long-running inference
    (multi-step web research, large models). Only runs for the duration of
    a pipeline — started as a task at pipeline start, cancelled at the end.

    Args:
        engines: GPU engine keys to keep alive. The container's service URL
            of each is resolved from the TTSEngine registry (SSOT), so this
            covers every GPU engine — xtts, moss, qwen3local, fishspeech, …
        interval: Seconds between pings. Defaults to
            ``TTS_KEEPALIVE_INTERVAL_SECONDS`` from config (5 min).
        on_warn: Optional callable(msg: str) for logging failed pings.
                 If None, failures are silently swallowed.
    """
    import asyncio
    import functools
    import requests
    from .config import (
        TTS_KEEPALIVE_INTERVAL_SECONDS,
        TTS_KEEPALIVE_HTTP_TIMEOUT,
    )
    from .tts_engines import get_engine

    if interval is None:
        interval = TTS_KEEPALIVE_INTERVAL_SECONDS
    loop = asyncio.get_running_loop()
    while True:
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return
        for engine in engines:
            # Service URL straight from the TTSEngine registry (SSOT) —
            # no hardcoded engine→URL map to keep in sync.
            eng = get_engine(engine)
            base_url = eng.service_url if eng else None
            if not base_url:
                continue
            try:
                await loop.run_in_executor(
                    None,
                    functools.partial(
                        requests.get,
                        f"{base_url}/keep_alive",
                        timeout=TTS_KEEPALIVE_HTTP_TIMEOUT,
                    ),
                )
            except requests.exceptions.ConnectionError:
                # Nothing listening = container intentionally down (e.g.
                # restart deferred until after the running inference) — an
                # expected window, not a fault. File-log only, no console
                # warning spam every interval.
                log_message(
                    f"TTS keep-alive: {engine} not listening — container "
                    f"down (deferred restart?), ping skipped"
                )
            except Exception as exc:
                if callable(on_warn):
                    on_warn(f"TTS keep-alive ping for {engine} failed: {exc}")


def _detect_running_tts_engine(timeout: float = TTS_HEALTH_CHECK_TIMEOUT) -> str:
    """Detect which GPU TTS engine is currently running via health check.

    Each engine's :meth:`TTSEngine.is_running` knows the unique signature
    of its own /health endpoint (XTTS has ``custom_voices``, MOSS has
    ``sample_rate``, Qwen3 prefixes ``"model"`` with ``"Qwen/Qwen3-TTS"``).
    We just iterate the registry and ask each in turn — no central if/elif
    cascade that has to learn the new engine.

    Returns engine key if running, empty string if none.
    """
    from .tts_engines import gpu_engines
    for engine in gpu_engines():
        try:
            if engine.is_running():
                return engine.key
        except Exception:
            # Any single engine's health-probe blowing up shouldn't stop
            # the loop — try the next.
            continue
    return ""
