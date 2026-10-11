"""TTS/LLM coordination — keeps the language model and the local TTS engine
within the VRAM budget.

Abstimmung zwischen Sprachmodell und Sprachausgabe: Wechselt der Browser die
geplante TTS-Engine (die, für die das LLM-Profil VRAM reserviert), stellt
``ensure_tts_state`` den VRAM-Stand her, startet die Engine und das
llama-swap-Profil mit passender ``-tts-<engine>``-Variante neu. Kanäle
wechseln sie nie — sie sprechen über die Liste des TTS-Dienstes (``tts_service``).

Central function: ensure_tts_state(wanted_tts, backend_type)
- It checks what's running, and adjusts if needed.

The engine lifecycle itself (start/stop/detect, refcount) stays in
``tts_engine_manager``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generator

from .logging_utils import log_message
from .tts_engine_manager import (
    TTS_HEALTH_CHECK_TIMEOUT,
    get_tts_refcount,
    is_tts_in_use,
)
from .tts_service import (
    engine_label_short,
    gpu_engine_keys,
    running_gpu_engine,
    start_engine,
    stop_engine,
)
from .tts_service import entries as tts_entries


def _label(engine_key: str) -> str:
    """Name of a TTS engine in status lines."""
    return engine_label_short(engine_key, "en")


def planned_tts_engine(model_id: str) -> str:
    """Die lokale GPU-Engine, für die das LLM-Profil ``<model>-tts-<engine>``
    Platz freihält: der oberste aktive lokale GPU-Eintrag der Liste des TTS-Dienstes,
    für den das Modell ein kalibriertes TTS-Profil hat. ``""``, wenn keiner passt —
    dann lädt das Basisprofil, und lokale GPU-Einträge sprechen nur, wenn Platz frei ist.

    Raises:
        TTSSuiteUnreachable: der Dienst antwortet nicht (keine Ersatzliste).
    """
    from pathlib import Path

    from .calibration import has_llamaswap_tts_variant
    from .config import LLAMASWAP_CONFIG_PATH

    for info in tts_entries():
        if (
            info.enabled and not info.deactivated and info.host is None and info.needs_gpu
            and has_llamaswap_tts_variant(Path(LLAMASWAP_CONFIG_PATH), model_id, info.engine)
        ):
            return info.engine
    return ""


@dataclass
class TTSState:
    """Result of ensure_tts_state check."""

    success: bool = True
    changed: bool = False           # True if VRAM state was modified
    messages: list[str] = field(default_factory=list)
    moss_device: str = ""


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
    if wanted_tts and wanted_tts not in gpu_engine_keys():
        return TTSState(success=True)

    running = running_gpu_engine()

    # Case 1: Already correct
    if running == wanted_tts:
        if running:
            yield f"{_label(running)} already running"
        return TTSState(success=True)

    # Case 3: Want TTS but wrong/none running → switch
    if wanted_tts:
        yield from _do_switch(wanted_tts, running, backend_type)
        # Get final state
        final_running = running_gpu_engine()
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
            if not running_gpu_engine():
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
        not (old_engine and old_engine in gpu_engine_keys())
        and _llm_already_on_tts_variant(new_engine, backend_type)
    )

    # Step 1: Stop old engine
    if old_engine and old_engine in gpu_engine_keys():
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
    if new_engine in gpu_engine_keys():
        # Yield a per-engine "loading…" line so the UI shows progress
        # before the (possibly minute-long) container start blocks.
        yield f"{_label(new_engine)}: Loading model..."
        started_ok, ready_msg = start_engine(new_engine)
        if ready_msg:
            yield ready_msg

    # Safety net: if we kept the LLM warm but the TTS container failed to come
    # up (e.g. the reserved slot didn't actually fit), fall back to the full
    # dance — free VRAM and retry once, then restart the LLM below.
    if llm_already_matching and not started_ok and new_engine in gpu_engine_keys():
        yield "TTS start failed next to warm LLM — freeing VRAM and retrying..."
        unload_all_gpu_models(backend_type, keep_tts=new_engine)
        started_ok, ready_msg = start_engine(new_engine)
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
