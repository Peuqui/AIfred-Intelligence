"""What AIfred starts once per process, with or without a browser: the debug log and the platform line, the background
tasks (cleanups of audio state, lookup caches, vision, the audit log and the vLLM compile cache; the audio index), the
vision pre-warm in live mode, the GPU detection and an empty Ollama; and the lifespans of the server: the session
events' loop, the Message Hub with the scheduler and Vigilantia's background watchers, the service status.

The Reflex app runs ``start_process_tasks`` on the first page load and registers the lifespans; a server without Reflex
composes its lifespan from the same pieces.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
from collections.abc import AsyncIterator

from .gpu_utils import total_actual_vram_gb
from .logging_utils import initialize_debug_log, log_message
from .stt_client import get_default_client as get_stt_client


async def start_process_tasks() -> None:
    """The once-per-process start (moved unchanged from the Reflex on_load)."""
    initialize_debug_log(force_reset=False)

    import platform
    kernel = platform.release()
    is_wsl = "microsoft" in kernel.lower()
    is_windows = os.name == "nt"
    if is_wsl:
        env_label = "WSL2 (WDDM — VRAM swapping possible)"
    elif is_windows:
        env_label = "Windows (WDDM — VRAM swapping possible)"
    else:
        env_label = "Native Linux (no VRAM swapping)"
    log_message(f"🖥️ Platform: {platform.system()} {kernel} — {env_label}")

    # Audio state cleanup task
    from .audio_state import cleanup_audio_state_task
    asyncio.create_task(cleanup_audio_state_task())

    # Vision pre-warm: when vision_mode is "live" the VLM
    # is supposed to stay resident in VRAM. Fire-and-forget
    # because the load can take seconds and we don't want
    # to block server startup behind it. On-demand mode
    # deliberately skips this — the whole point of
    # on-demand is lazy loading.
    from .vision_prewarm import get_vision_mode, prewarm_vlm
    if get_vision_mode() == "live":
        log_message("🎥 Vision pre-warm: starting (mode=live)")
        asyncio.create_task(prewarm_vlm())

    # Lookup-Cache cleanup task (Speculative-Decoding-Caches einsammeln)
    from .lookup_cache_cleanup import cleanup_lookup_cache_task
    asyncio.create_task(cleanup_lookup_cache_task())

    # Vision cleanup task — räumt alte Face-Crops, Motion-
    # Frames und Vision-DB-Events auf, TTL aus
    # plugins/tools/vision/settings.json face_recognition.retention_days.
    from .vision_cleanup import cleanup_vision_task
    asyncio.create_task(cleanup_vision_task())

    # Audit-Log cleanup task — prunt die append-only tool_audit-
    # Tabelle (SECURITY_AUDIT_RETENTION_DAYS) am selben Slot.
    from .security import cleanup_audit_log_task
    asyncio.create_task(cleanup_audit_log_task())

    # vLLM-Compile-Cache cleanup task — Artefakte geloeschter
    # Modelle und verworfener Betriebspunkte (Alter oder
    # Obergrenze, siehe VLLM_PRODUCTION_CACHE_*) am selben Slot.
    from .calibration.vllm_probe import cleanup_vllm_compile_cache_task
    asyncio.create_task(cleanup_vllm_compile_cache_task())

    # Audio index incremental sync (only for sources already populated)
    from .audio_index import sync_audio_index_task
    asyncio.create_task(sync_audio_index_task())

    # Whisper STT: Docker container (started on demand or always-on)
    if get_stt_client().is_ready():
        log_message("✅ Whisper: Docker service ready")
    else:
        log_message("⚠️ Whisper: Docker service not running (will start on first STT request)")

    # GPU Detection (once per server)
    log_message("🔍 Detecting GPU capabilities...")
    try:
        from .gpu_detection import server_gpu_info
        gpu_info = server_gpu_info()
        if gpu_info:
            total_vram_gb = total_actual_vram_gb(gpu_info)

            unique_names = list(dict.fromkeys(gpu_info.all_gpu_names))
            if gpu_info.gpu_count == 1:
                log_message(f"✅ GPU: {gpu_info.name} (Compute {gpu_info.compute_capability}, {total_vram_gb} GB)")
            elif len(unique_names) == 1:
                log_message(f"✅ GPU: {gpu_info.gpu_count}x {gpu_info.name} (Compute {gpu_info.compute_capability}, {total_vram_gb} GB total)")
            else:
                log_message(f"✅ GPU: {' + '.join(unique_names)} ({total_vram_gb} GB total)")

            if gpu_info.unsupported_backends:
                log_message(f"⚠️ Incompatible backends: {', '.join(gpu_info.unsupported_backends)}")
            if gpu_info.warnings:
                for warning in gpu_info.warnings[:2]:
                    log_message(f"⚠️ {warning}")
        else:
            log_message("ℹ️ No GPU detected or nvidia-smi not available")
    except (subprocess.CalledProcessError, ImportError, RuntimeError) as e:
        log_message(f"⚠️ GPU detection failed: {e}")

    # Unload all Ollama models to ensure clean VRAM slate
    try:
        from ..backends.ollama import OllamaBackend
        ollama = OllamaBackend()
        _success, unloaded = await ollama.unload_all_models(wait_for_stability=False)
        if unloaded:
            log_message(f"🧹 Startup: Unloaded {len(unloaded)} model(s) from VRAM: {', '.join(unloaded)}")
        else:
            log_message("🧹 Startup: No models to unload (VRAM clean)")
        await ollama.client.aclose()
    except (RuntimeError, OSError) as e:
        log_message(f"ℹ️ Ollama not available for startup cleanup: {e}")


@contextlib.asynccontextmanager
async def message_hub_lifespan() -> AsyncIterator[None]:
    """Start Message Hub on ASGI startup, stop on shutdown.

    A respawned worker runs the lifespan again (Reflex 0.10, granian 2.8); the Reflex on_load starts the hub too.
    """
    from .config import MESSAGE_HUB_OWNER
    from .message_hub import message_hub, register_channel_workers
    from .scheduler import scheduler_loop

    if not MESSAGE_HUB_OWNER:
        raise RuntimeError(
            "MESSAGE_HUB_OWNER is not set — add MESSAGE_HUB_OWNER=<your AIfred "
            "username> to .env (the account that owns Message-Hub sessions)"
        )
    register_channel_workers(message_hub)
    if not message_hub.is_running("scheduler"):
        message_hub.register("scheduler", scheduler_loop)
    await message_hub.start_all()
    # Vigilantia: Hintergrund-Watcher für Quellen mit auto_start=True.
    # Läuft unabhängig vom Browser-Tab — solange der AIfred-Service da
    # ist, ist die Detection scharf.
    try:
        from .vision_autostart import (
            ensure_schedule_supervisor,
            start_all_background_watchers,
        )
        await start_all_background_watchers()
        # Dauer-Supervisor: startet/stoppt die Watcher an den Pro-Kamera-
        # Zeitfenster-Grenzen (z.B. Stopp um 06:00). Lief bisher nie, weil
        # nur start_all aufgerufen wurde — Kameras blieben nach Fenster-Ende
        # scharf. Idempotent (Granian-Respawn-sicher).
        ensure_schedule_supervisor()
    except Exception as e:  # Vigilantia must not keep AIfred from starting; logged
        import logging
        logging.getLogger(__name__).warning(
            "vigilantia autostart failed: %s", e, exc_info=True,
        )
    try:
        yield
    finally:
        from .logging_utils import log_exception
        await message_hub.stop_all()
        try:
            from .vision_watcher import get_default_watcher
            await get_default_watcher().shutdown()
        except Exception as exc:  # noqa: BLE001 — shutting down: the camera logout below must still run
            log_exception("Shutdown: vision watcher", exc)
        # Geteilte Kamera-Snap-/AI-Clients sauber abmelden (Reolink-Logout →
        # gibt die Session frei). Gegen Token-Leaks bei Service-Neustarts,
        # die sonst das „max session"-Kontingent der Kamera füllen.
        try:
            from .vision_snap import close_clients
            await close_clients()
        except Exception as exc:  # noqa: BLE001 — shutting down: logged, nothing else depends on it
            log_exception("Shutdown: camera clients", exc)


@contextlib.asynccontextmanager
async def service_status_lifespan() -> AsyncIterator[None]:
    """Hold llama-swap's event stream and read STT/TTS state, for the page's status line."""
    from .service_status import get_monitor

    task = asyncio.create_task(get_monitor().run())
    try:
        yield
    finally:
        task.cancel()


@contextlib.asynccontextmanager
async def session_events_lifespan() -> AsyncIterator[None]:
    """The event hub belongs to this loop; publishes from the hub thread are handed over to it."""
    from . import session_events

    session_events.bind_home_loop(asyncio.get_running_loop())
    yield
