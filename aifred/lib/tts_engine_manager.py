"""TTS Engine Manager — Single Source of Truth for TTS engine lifecycle.

Handles VRAM management, Docker container start/stop, and LLM backend
restart when the browser switches the planned TTS engine (the one the LLM
profile reserves VRAM for). Channels never switch it — they speak through
the escalation list (``tts_escalation``), which only starts a local engine
when it fits into free VRAM.

Central function: ensure_tts_state(wanted_tts, backend_type)
- It checks what's running, and adjusts if needed.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Generator

from .logging_utils import log_message


# Engines that require a Docker container with GPU VRAM.
# Derived from the SSOT registry (aifred.lib.tts_engines) so adding a new
# GPU engine = one new TTSEngine subclass + one registry entry; this set
# updates automatically.
def _gpu_engine_keys() -> set[str]:
    from .tts_engines import gpu_engines
    return {e.key for e in gpu_engines()}


GPU_ENGINES = _gpu_engine_keys()


def _label(engine_key: str) -> str:
    """Name of a TTS engine in status lines."""
    from .tts_engines import require_engine
    return require_engine(engine_key).label_short


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
    and external cancellations (e.g. FreeEcho.2 Action-Button stop event).
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


@dataclass
class TTSState:
    """Result of ensure_tts_state check."""

    success: bool = True
    changed: bool = False           # True if VRAM state was modified
    messages: list[str] = field(default_factory=list)
    moss_device: str = ""


def _container_host_pids(compose_file: str) -> set[str]:
    """Host PIDs of the processes inside a compose project's container(s)."""
    import subprocess

    ids = subprocess.run(
        ["docker", "compose", "-f", compose_file, "ps", "-q"],
        capture_output=True, text=True, timeout=10, check=False,
    ).stdout.split()
    pids: set[str] = set()
    for container_id in ids:
        top = subprocess.run(
            ["docker", "top", container_id, "-eo", "pid"],
            capture_output=True, text=True, timeout=10, check=False,
        ).stdout.split()
        pids.update(pid for pid in top[1:] if pid.isdigit())
    return pids


def local_tts_gpu_footprint() -> dict[str, dict[str, int]]:
    """VRAM (MiB) of the running local GPU TTS containers:
    ``{engine_key: {gpu_uuid: MiB}}``. nvidia-smi reports host PIDs, docker
    top lists the container's host PIDs — their intersection is exactly the
    TTS container, not the LLM or the Whisper worker next to it."""
    from .nvidia_smi import compute_apps
    from .tts_engines import gpu_engines

    apps = compute_apps()
    if not apps:
        return {}
    footprint: dict[str, dict[str, int]] = {}
    for engine in gpu_engines():
        if engine.docker_compose_path is None or not engine.is_running():
            continue
        pids = _container_host_pids(str(engine.docker_compose_path))
        for app in apps:
            if app["pid"] in pids:
                per_card = footprint.setdefault(engine.key, {})
                per_card[app["gpu_uuid"]] = per_card.get(app["gpu_uuid"], 0) + int(app["used_memory"])
    return footprint


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


def _llm_already_on_tts_variant(new_engine: str, backend_type: str) -> bool:
    """True if the loaded llama-swap model already runs the ``-tts-<engine>``
    variant for ``new_engine``. That variant's VRAM budget already reserves
    the slot for this TTS engine (calibrated with fewer layers), so the TTS
    container can start alongside the WARM LLM — no llama-swap stop/restart,
    no needless LLM cold start. The engine key equals the suffix token
    (see ``has_llamaswap_tts_variant``: ``-tts-<backend>``)."""
    if backend_type != "llamacpp" or not new_engine:
        return False
    try:
        import requests
        from .config import DEFAULT_LLAMACPP_URL
        base_url = DEFAULT_LLAMACPP_URL.removesuffix("/v1")
        r = requests.get(f"{base_url}/running", timeout=TTS_HEALTH_CHECK_TIMEOUT)
        if not r.ok:
            return False
        data = r.json()
        running = data.get("running", []) if isinstance(data, dict) else data
        token = f"-tts-{new_engine}"
        for m in running:
            name = (m.get("model") if isinstance(m, dict) else str(m)) or ""
            if token in name:
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def stop_engine(engine: str) -> tuple[bool, str]:
    """Stop a running TTS engine container.

    Dispatches through the registry; lightweight engines are a no-op.
    """
    from .tts_engines import get_engine
    eng = get_engine(engine)
    if eng is None:
        return True, ""  # unknown engine key — historical no-op behaviour
    return eng.stop()


# Engine-specific timeouts live on each TTSEngine subclass now (see
# ensure_ready overrides in aifred/lib/tts_engines/*/engine.py). XTTS=60s,
# MOSS=180s, Qwen3=240s as defaults.


def ensure_engine_ready(
    engine: str,
    timeout: int | None = None,
) -> tuple[bool, str, str]:
    """Single SSOT for "start container + wait until model is loaded".

    Routes through the engine registry — adding a future GPU engine
    means adding a TTSEngine subclass with its own ``ensure_ready``,
    no edits here.

    Returns ``(success, status_message, device)``. ``device`` is the
    string the engine container reports for its compute target ("cuda:0",
    "cpu", …) — engines that don't expose one return "".
    """
    from .tts_engines import get_engine
    eng = get_engine(engine)
    if eng is None:
        # Unknown / lightweight engine — nothing to start, treat as ready.
        return True, "", ""

    return eng.ensure_ready(timeout=timeout)


def ensure_tts_state(
    wanted_tts: str,
    backend_type: str = "llamacpp",
) -> Generator[str, None, TTSState]:
    """Ensure VRAM state matches TTS requirements. Yields status after each step.

    This is the SINGLE SOURCE OF TRUTH for TTS state management.

    Args:
        wanted_tts: Desired TTS engine key, or "" for none
        backend_type: Active LLM backend ("llamacpp", "ollama", etc.)

    Yields:
        Status messages after each blocking step.

    Returns:
        TTSState with success and changed flags.
    """
    # Lightweight engines don't need VRAM management
    if wanted_tts and wanted_tts not in GPU_ENGINES:
        return TTSState(success=True)

    running = _detect_running_tts_engine()

    # Case 1: Already correct
    if running == wanted_tts:
        if running:
            yield f"{_label(running)} already running"
        return TTSState(success=True)

    # Case 3: Want TTS but wrong/none running → switch
    if wanted_tts:
        yield from _do_switch(wanted_tts, running, backend_type)
        # Get final state
        final_running = _detect_running_tts_engine()
        return TTSState(
            success=final_running == wanted_tts,
            changed=True,
            messages=[],
        )

    # Case 4: Don't want TTS but one is running → stop it … unless an active
    # pipeline still needs it (refcount > 0). Skipping the stop keeps FreeEcho.2
    # responses safe from cross-channel teardown; the container's KEEP_ALIVE
    # will eventually idle it out anyway.
    if running:
        if is_tts_in_use(running):
            yield (
                f"{_label(running)} in use by {get_tts_refcount(running)} active pipeline(s) "
                f"— stop deferred"
            )
            return TTSState(success=True)
        yield f"Stopping {_label(running)} (not needed)..."
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
            yield f"⚠️ {_label(running)} still responds after stop — VRAM may not be free"
            return TTSState(success=False, changed=True)
        return TTSState(success=success, changed=True)

    return TTSState(success=True)


def _do_switch(
    new_engine: str,
    old_engine: str,
    backend_type: str,
) -> Generator[str, None, None]:
    """Execute the actual TTS engine switch. Yields status after each step.

    Steps (all blocking, sequential):
    1. Stop old TTS container (if different one running)
    2. Free VRAM (stop LLM, stop other TTS containers)
    3. Start new TTS container + wait for model load
    4. Restart LLM backend with TTS-calibrated profile
    """
    from .process_utils import unload_all_gpu_models

    # If the LLM is ALREADY loaded on the matching ``-tts-<new_engine>``
    # variant, its VRAM budget already reserves the slot for this TTS engine.
    # Then we can start the container next to the warm LLM and skip both the
    # VRAM-free (Step 2) and the llama-swap restart (Step 4) — avoiding a
    # needless LLM cold start. Only relevant when no *other* GPU TTS is running.
    llm_already_matching = (
        not (old_engine and old_engine in GPU_ENGINES)
        and _llm_already_on_tts_variant(new_engine, backend_type)
    )

    # Step 1: Stop old engine
    if old_engine and old_engine in GPU_ENGINES:
        success, msg = stop_engine(old_engine)
        yield f"{_label(old_engine)} stopped" if success else f"{_label(old_engine)} stop failed: {msg}"

    # Step 2: Free VRAM — only if the LLM isn't already on the matching variant.
    # LLM must be stopped to make room for the new TTS engine ONLY when the
    # current profile doesn't already reserve the slot.
    if not llm_already_matching and (old_engine or _is_llm_loaded(backend_type)):
        actions = unload_all_gpu_models(backend_type, keep_tts=new_engine)
        if actions:
            yield f"VRAM freed: {', '.join(actions)}"

    # Step 3: Start new engine + wait for model load
    started_ok = True
    if new_engine in GPU_ENGINES:
        # Yield a per-engine "loading…" line so the UI shows progress
        # before the (possibly minute-long) container start blocks.
        yield f"{_label(new_engine)}: Loading model..."
        started_ok, ready_msg, _device = ensure_engine_ready(
            new_engine,
        )
        if ready_msg:
            yield ready_msg

    # Safety net: if we kept the LLM warm but the TTS container failed to come
    # up (e.g. the reserved slot didn't actually fit), fall back to the full
    # dance — free VRAM and retry once, then restart the LLM below.
    if llm_already_matching and not started_ok and new_engine in GPU_ENGINES:
        yield "TTS start failed next to warm LLM — freeing VRAM and retrying..."
        unload_all_gpu_models(backend_type, keep_tts=new_engine)
        started_ok, ready_msg, _device = ensure_engine_ready(
            new_engine,
        )
        if ready_msg:
            yield ready_msg
        llm_already_matching = False  # LLM was stopped → must restart below

    # Step 4: Restart the LLM orchestrator (llama-swap) so it picks up the
    # new VRAM/TTS profile — but ONLY if we actually changed the LLM's VRAM
    # situation. If the LLM stayed warm on the matching variant, restarting
    # would just cold-start it for nothing.
    if not llm_already_matching:
        restart_llm_backend(backend_type)
    model_info = get_effective_model_info(backend_type)
    if model_info:
        yield f"LLM profile ready: {model_info}"
    else:
        yield "LLM profile ready (TTS-calibrated)"


def get_effective_model_info(backend_type: str = "llamacpp") -> str:
    """Get effective model + context info string after TTS/VRAM change.

    Single source of truth for the "model reloaded with X context" debug line.
    Used by the browser's TTS toggle (ensure_tts_state).
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
