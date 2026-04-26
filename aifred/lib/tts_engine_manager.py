"""TTS Engine Manager — Single Source of Truth for TTS engine lifecycle.

Handles VRAM management, Docker container start/stop, and LLM backend
restart when switching between TTS engines. Used by both the browser UI
(Reflex state) and headless channels (FreeEcho.2, future voice control).

Central function: ensure_tts_state(wanted_tts, backend_type)
- Both browser and Puck call this with what they want.
- It checks what's running, and adjusts if needed.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Generator

from .logging_utils import log_message


# Engines that require a Docker container with GPU VRAM
GPU_ENGINES = {"xtts", "moss"}

# Engines that run without Docker / without VRAM
LIGHTWEIGHT_ENGINES = {"piper", "edge", "espeak", "dashscope"}

# HTTP timeout for TTS health checks (seconds).
# Normal response: <100ms. This is only a safety net for hung connections.
TTS_HEALTH_CHECK_TIMEOUT = 5


# =============================================================================
# TTS engine refcount — protects active pipelines from stop_engine() races.
# =============================================================================
#
# Use case: a Puck request acquires "xtts" at the start of its pipeline and
# releases it after the audio is sent. If the browser (or another channel)
# calls ensure_tts_state() with `wanted_tts != "xtts"` while the Puck is mid-
# pipeline, the stop_engine() call would tear down the container under the
# Puck's feet. The refcount makes ensure_tts_state() skip the stop while any
# active holder still needs the engine.
#
# Counter — NOT a binary lock — so multiple concurrent Pucks coexist cleanly.
# Idle behaviour is unchanged: when nothing holds the engine, ensure_tts_state
# may stop it, and the container's KEEP_ALIVE will eventually shut it down on
# its own.
_engine_refs: dict[str, int] = {engine: 0 for engine in GPU_ENGINES}
_engine_refs_lock = threading.Lock()


def acquire_tts(engine: str) -> None:
    """Mark a TTS engine as in-use by an active pipeline.

    Idempotent across pipelines — call once per pipeline at start, paired
    with exactly one ``release_tts(engine)`` in a ``finally`` block. The
    counter is allowed to grow above 1 when multiple concurrent pipelines
    use the same engine.
    """
    if engine not in _engine_refs:
        return
    with _engine_refs_lock:
        _engine_refs[engine] += 1


def release_tts(engine: str) -> None:
    """Release one acquisition of a TTS engine.

    Safe to call even if the engine was not acquired (no-op below 0).
    Pipelines must put this in a ``finally`` block to survive crashes
    and external cancellations (e.g. Puck Action-Button stop event).
    """
    if engine not in _engine_refs:
        return
    with _engine_refs_lock:
        _engine_refs[engine] = max(0, _engine_refs[engine] - 1)


def is_tts_in_use(engine: str) -> bool:
    """True if any pipeline currently holds an acquisition for this engine."""
    if engine not in _engine_refs:
        return False
    with _engine_refs_lock:
        return _engine_refs[engine] > 0


def get_tts_refcount(engine: str) -> int:
    """Current acquisition count (debug/inspection helper)."""
    if engine not in _engine_refs:
        return 0
    with _engine_refs_lock:
        return _engine_refs[engine]


async def tts_keepalive_loop(
    engines: list[str],
    interval: int | None = None,
    on_warn: object = None,
) -> None:
    """Ping ``/keep_alive`` on each given GPU TTS engine while a pipeline runs.

    Each ping resets the container-internal idle timer (XTTS_KEEP_ALIVE /
    MOSS_KEEP_ALIVE) to its full window. Used by the Puck channel and the
    browser pipeline to prevent the engine from shutting down mid-flight
    during long-running inference (multi-step web research, large models).

    Args:
        engines: List of GPU engine names ("xtts" / "moss") to keep alive.
        interval: Seconds between pings. Defaults to
            ``TTS_KEEPALIVE_INTERVAL_SECONDS`` from config (5 min).
        on_warn: Optional callable(msg: str) for logging failed pings.
                 If None, failures are silently swallowed.
    """
    import asyncio
    import requests
    from .config import (
        XTTS_SERVICE_URL,
        MOSS_TTS_SERVICE_URL,
        TTS_KEEPALIVE_INTERVAL_SECONDS,
        TTS_KEEPALIVE_HTTP_TIMEOUT,
    )

    if interval is None:
        interval = TTS_KEEPALIVE_INTERVAL_SECONDS
    urls = {"xtts": XTTS_SERVICE_URL, "moss": MOSS_TTS_SERVICE_URL}
    loop = asyncio.get_running_loop()
    while True:
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return
        for engine in engines:
            base_url = urls.get(engine)
            if not base_url:
                continue
            try:
                await loop.run_in_executor(
                    None,
                    lambda u=base_url: requests.get(
                        f"{u}/keep_alive", timeout=TTS_KEEPALIVE_HTTP_TIMEOUT
                    ),
                )
            except Exception as exc:
                if callable(on_warn):
                    on_warn(f"TTS keep-alive ping for {engine} failed: {exc}")


@dataclass
class TTSState:
    """Result of ensure_tts_state check."""

    success: bool = True
    changed: bool = False           # True if VRAM state was modified
    deferred: bool = False          # True if LLM is loaded and caller should inferize first
    messages: list[str] = field(default_factory=list)
    moss_device: str = ""


def _detect_running_tts_engine(timeout: float = TTS_HEALTH_CHECK_TIMEOUT) -> str:
    """Detect which GPU TTS engine is currently running via health check.

    Returns engine key ("xtts" or "moss") if running, empty string if none.
    Normal response time: <100ms. Timeout is only a safety net for hung connections.
    """
    import requests

    from .config import XTTS_SERVICE_URL, MOSS_TTS_SERVICE_URL

    # Identify services by fields unique to each TTS engine. A shared port
    # (e.g. misconfigured Whisper on 5055) would also answer model_loaded=true
    # but lacks the engine-specific field.
    #   XTTS  : "custom_voices"
    #   MOSS  : "voices" + "sample_rate"
    try:
        r = requests.get(f"{XTTS_SERVICE_URL}/health", timeout=timeout)
        if r.ok:
            data = r.json()
            if data.get("model_loaded") and "custom_voices" in data:
                return "xtts"
    except Exception:
        pass

    try:
        r = requests.get(f"{MOSS_TTS_SERVICE_URL}/health", timeout=timeout)
        if r.ok:
            data = r.json()
            if data.get("model_loaded") and "sample_rate" in data:
                return "moss"
    except Exception:
        pass

    return ""


def _is_llm_loaded(backend_type: str) -> bool:
    """Check if an LLM is currently loaded in VRAM."""
    if backend_type != "llamacpp":
        return False
    try:
        import requests
        from .config import DEFAULT_LLAMACPP_URL
        base_url = DEFAULT_LLAMACPP_URL.removesuffix("/v1")
        r = requests.get(f"{base_url}/running", timeout=TTS_HEALTH_CHECK_TIMEOUT)
        if r.ok:
            data = r.json()
            # Response is {"running": [...]} — check the list, not the dict
            models = data.get("running", []) if isinstance(data, dict) else data
            return len(models) > 0
    except Exception:
        pass
    return False


def stop_engine(engine: str) -> tuple[bool, str]:
    """Stop a running TTS engine container."""
    from .process_utils import stop_xtts_container, stop_moss_container

    if engine == "xtts":
        return stop_xtts_container()
    elif engine == "moss":
        return stop_moss_container()
    return True, ""


# Background TTS stop — runs parallel to LLM inference (Case 2b)
_tts_stop_thread: threading.Thread | None = None


def _start_async_tts_stop(engine: str) -> None:
    """Start TTS container stop in background (parallel to LLM inference).

    Docker compose down is CPU/IO, LLM inference is GPU — no interference.
    """
    global _tts_stop_thread
    _tts_stop_thread = threading.Thread(
        target=stop_engine, args=(engine,), daemon=True, name=f"tts-stop-{engine}",
    )
    _tts_stop_thread.start()
    log_message(f"Background TTS stop started: {engine}")


def _await_tts_stop(timeout: float = 30) -> None:
    """Wait for background TTS stop to complete (if one is in progress)."""
    global _tts_stop_thread
    if _tts_stop_thread is not None and _tts_stop_thread.is_alive():
        log_message("Waiting for background TTS stop to complete...")
        _tts_stop_thread.join(timeout=timeout)
    _tts_stop_thread = None


def ensure_tts_state(
    wanted_tts: str,
    backend_type: str = "llamacpp",
    xtts_force_cpu: bool = False,
    check_defer: bool = False,
) -> Generator[str, None, TTSState]:
    """Ensure VRAM state matches TTS requirements. Yields status after each step.

    This is the SINGLE SOURCE OF TRUTH for TTS state management.
    Both browser and Puck call this with what they want.

    Args:
        wanted_tts: Desired TTS engine ("xtts", "moss", or "" for none)
        backend_type: Active LLM backend ("llamacpp", "ollama", etc.)
        xtts_force_cpu: Force XTTS to CPU mode (no VRAM needed)
        check_defer: If True and LLM is loaded, return deferred=True
                     so caller can inferize first before switching.
                     (Puck optimization: use existing LLM, switch after)

    Yields:
        Status messages after each blocking step.

    Returns:
        TTSState with success, changed, deferred flags.
    """
    # XTTS CPU mode doesn't need GPU VRAM
    if wanted_tts == "xtts" and xtts_force_cpu:
        wanted_tts = ""

    # Lightweight engines don't need VRAM management
    if wanted_tts and wanted_tts not in GPU_ENGINES:
        return TTSState(success=True)

    running = _detect_running_tts_engine()

    # Case 1: Already correct
    if running == wanted_tts:
        if running:
            yield f"{running.upper()} already running"
        return TTSState(success=True)

    # Case 2a: Puck optimization — LLM loaded, want different GPU TTS
    # Caller can inferize with current LLM first, then switch
    if check_defer and wanted_tts and _is_llm_loaded(backend_type):
        yield "LLM loaded, deferring TTS switch to after inference"
        return TTSState(success=True, deferred=True)

    # Case 2b: Puck optimization — LLM loaded, GPU TTS running but not needed
    # (User switched to lightweight engine like Edge/Piper/eSpeak)
    # Start container stop NOW (background thread) — docker compose down is
    # CPU/IO and doesn't interfere with GPU inference running in parallel.
    # After inference, force_tts_switch() will join the thread and load base model.
    # Skip if an active pipeline still holds the engine (refcount > 0).
    if check_defer and not wanted_tts and running and _is_llm_loaded(backend_type):
        if is_tts_in_use(running):
            yield (
                f"{running.upper()} in use by {get_tts_refcount(running)} active pipeline(s) "
                f"— stop deferred"
            )
            return TTSState(success=True)
        _start_async_tts_stop(running)
        yield f"LLM loaded, {running.upper()} stop started — deferring model switch to after inference"
        return TTSState(success=True, deferred=True)

    # Case 3: Want TTS but wrong/none running → switch
    if wanted_tts:
        yield from _do_switch(wanted_tts, running, backend_type, xtts_force_cpu)
        # Get final state
        final_running = _detect_running_tts_engine()
        return TTSState(
            success=final_running == wanted_tts,
            changed=True,
            messages=[],
        )

    # Case 4: Don't want TTS but one is running → stop it … unless an active
    # pipeline still needs it (refcount > 0). Skipping the stop keeps Puck
    # responses safe from cross-channel teardown; the container's KEEP_ALIVE
    # will eventually idle it out anyway.
    if running:
        if is_tts_in_use(running):
            yield (
                f"{running.upper()} in use by {get_tts_refcount(running)} active pipeline(s) "
                f"— stop deferred"
            )
            return TTSState(success=True)
        yield f"Stopping {running.upper()} (not needed)..."
        success, msg = stop_engine(running)
        yield msg
        # Verify the container is actually gone — if the stop silently failed,
        # the base LLM profile (full VRAM budget) would OOM when loaded. Poll
        # the health endpoint until it stops answering, up to a short deadline.
        import time
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if not _detect_running_tts_engine():
                break
            time.sleep(0.5)
        else:
            yield f"⚠️ {running.upper()} still responds after stop — VRAM may not be free"
            return TTSState(success=False, changed=True)
        return TTSState(success=success, changed=True)

    return TTSState(success=True)


def _do_switch(
    new_engine: str,
    old_engine: str,
    backend_type: str,
    xtts_force_cpu: bool,
) -> Generator[str, None, None]:
    """Execute the actual TTS engine switch. Yields status after each step.

    Steps (all blocking, sequential):
    1. Stop old TTS container (if different one running)
    2. Free VRAM (stop LLM, stop other TTS containers)
    3. Start new TTS container + wait for model load
    4. Restart LLM backend with TTS-calibrated profile
    """
    from .process_utils import unload_all_gpu_models

    # Step 1: Stop old engine
    if old_engine and old_engine in GPU_ENGINES:
        success, msg = stop_engine(old_engine)
        yield f"{old_engine.upper()} stopped" if success else f"{old_engine.upper()} stop failed: {msg}"

    # Step 2: Free VRAM — only if something needs to be removed.
    # old_engine is already known from caller (no extra HTTP call needed).
    # LLM must be stopped to make room for the new TTS engine.
    if old_engine or _is_llm_loaded(backend_type):
        actions = unload_all_gpu_models(backend_type, keep_tts=new_engine)
        if actions:
            yield f"VRAM freed: {', '.join(actions)}"

    # Step 3: Start new engine + wait for model load
    if new_engine == "xtts":
        from .process_utils import set_xtts_cpu_mode, ensure_xtts_ready

        success, msg = set_xtts_cpu_mode(xtts_force_cpu)
        yield msg

        if success:
            success, ready_msg = ensure_xtts_ready(timeout=60)
            yield ready_msg

    elif new_engine == "moss":
        from .process_utils import ensure_moss_ready

        yield "MOSS-TTS: Loading model..."
        success, msg, device = ensure_moss_ready(timeout=120)
        yield msg

    # Step 4: Restart LLM with TTS-calibrated profile
    restart_llm_backend(backend_type)
    model_info = get_effective_model_info(backend_type)
    if model_info:
        yield f"LLM restarted: {model_info}"
    else:
        yield "LLM restarted with TTS-calibrated profile"


def force_tts_switch(
    wanted_tts: str,
    backend_type: str = "llamacpp",
    xtts_force_cpu: bool = False,
) -> Generator[str, None, TTSState]:
    """Ensure TTS + LLM with TTS-profile are loaded after deferred inference.

    Called after Puck used the existing LLM (without TTS) for inference.
    Now load TTS and switch LLM to TTS-calibrated profile.

    Cases:
    - wanted_tts="" → no GPU TTS needed, clean up container + load base model
    - TTS already running (correct engine) → only switch LLM profile
    - TTS not running → unload all, start TTS, load LLM with TTS profile
    - Wrong TTS running → unload all, start correct TTS, load LLM with TTS profile
    """
    # No GPU TTS wanted — clean up and switch to base model
    if not wanted_tts:
        from .process_utils import stop_llama_swap

        # Wait for background TTS stop (started in Case 2b, parallel to inference)
        _await_tts_stop()

        # Verify container is actually gone
        still_running = _detect_running_tts_engine()
        if still_running:
            yield f"Stopping {still_running.upper()} (not yet stopped)..."
            stop_engine(still_running)
            yield f"{still_running.upper()} container stopped"

        # Stop LLM (TTS variant), then restart with base profile
        stop_llama_swap()
        yield "VRAM freed: llama-swap stopped"
        restart_llm_backend(backend_type)
        model_info = get_effective_model_info(backend_type)
        yield f"LLM restarted: {model_info}" if model_info else "LLM restarted with base profile"
        return TTSState(success=True, changed=True)

    running = _detect_running_tts_engine()

    if running == wanted_tts:
        # TTS already running — just switch LLM to TTS profile
        restart_llm_backend(backend_type)
        model_info = get_effective_model_info(backend_type)
        yield f"LLM profile switched: {model_info}" if model_info else "LLM profile switched"
        return TTSState(success=True, changed=True)

    # TTS not running or wrong engine — full switch (unload → TTS → LLM)
    yield from _do_switch(wanted_tts, running, backend_type, xtts_force_cpu)

    final_running = _detect_running_tts_engine()
    return TTSState(success=final_running == wanted_tts, changed=True)


def get_effective_model_info(backend_type: str = "llamacpp") -> str:
    """Get effective model + context info string after TTS/VRAM change.

    Single source of truth for the "model reloaded with X context" debug line.
    Used by both browser (TTS toggle) and Puck (ensure_tts_state) paths.
    """
    from .config import get_effective_model_from_settings
    from .formatting import format_number

    model = get_effective_model_from_settings("aifred")
    if not model:
        return ""

    if backend_type == "llamacpp":
        from .research.context_utils import get_model_native_context
        ctx = get_model_native_context(model, backend_type)
        if ctx > 0:
            return f"{model} (ctx: {format_number(ctx)})"
    return model


def restart_llm_backend(
    backend_type: str = "llamacpp",
) -> bool:
    """Restart the LLM backend with current VRAM profile."""
    if backend_type == "llamacpp":
        from .process_utils import start_llama_swap
        if start_llama_swap():
            log_message("llama-swap restarted")
            return True
    return False
