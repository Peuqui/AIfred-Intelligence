"""System endpoints: restart Ollama/AIfred, clear Vector DB, reset defaults."""

import asyncio
import subprocess

from fastapi import HTTPException, BackgroundTasks

from ..settings import load_settings
from ..logging_utils import log_message
from ..config import DEFAULT_OLLAMA_URL
from .app import api_app
from .schemas import SystemActionResponse


@api_app.post("/system/restart-ollama", response_model=SystemActionResponse, tags=["System"])
async def restart_ollama():
    """
    Restart Ollama service.

    Uses systemctl to restart the ollama service.
    Waits for the service to be ready before returning.
    """
    log_message("🔄 API: Restarting Ollama service...")

    try:
        # Restart via systemctl — in a thread, a sync subprocess.run would
        # block the one granian event loop for up to 30 s (all sessions).
        result = await asyncio.to_thread(
            subprocess.run,
            ["systemctl", "restart", "ollama"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"systemctl restart failed: {result.stderr}"
            )

        # Wait for Ollama to be ready
        import httpx
        settings = load_settings() or {}
        backend_url = settings.get("backend_url", DEFAULT_OLLAMA_URL)

        for attempt in range(20):  # 10 seconds max
            await asyncio.sleep(0.5)
            try:
                async with httpx.AsyncClient(timeout=2.0) as _hc:
                    response = await _hc.get(f"{backend_url}/api/tags")
                if response.status_code == 200:
                    log_message(f"✅ API: Ollama ready after {(attempt+1)*0.5:.1f}s")
                    return SystemActionResponse(
                        success=True,
                        message="Ollama restarted successfully",
                        details=f"Ready after {(attempt+1)*0.5:.1f}s"
                    )
            except httpx.RequestError:
                continue

        return SystemActionResponse(
            success=True,
            message="Ollama restart initiated",
            details="Service may still be starting"
        )

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Restart timed out")
    except Exception as e:
        log_message(f"❌ API: Ollama restart failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_app.post("/system/restart-aifred", response_model=SystemActionResponse, tags=["System"])
async def restart_aifred(background_tasks: BackgroundTasks):
    """
    Restart AIfred service.

    Schedules a restart and returns immediately.
    The service will restart after a short delay.
    """
    log_message("🔄 API: AIfred restart requested...")

    from ..process_utils import restart_service

    def delayed_restart():
        import time
        time.sleep(1)
        restart_service("aifred-intelligence", check=False)

    background_tasks.add_task(delayed_restart)

    return SystemActionResponse(
        success=True,
        message="AIfred restart scheduled",
        details="Service will restart in ~1 second"
    )


@api_app.post("/system/clear-vectordb", response_model=SystemActionResponse, tags=["System"])
async def clear_vector_db():
    """
    Clear Vector DB (ChromaDB).

    Deletes all cached research entries from ChromaDB.
    The collection structure remains intact.
    """
    log_message("🗑️ API: Clearing Vector DB...")

    try:
        import chromadb
        client = chromadb.HttpClient(host='localhost', port=8000)
        collection = client.get_collection('research_cache')

        # Get all IDs
        all_ids = collection.get(include=[])["ids"]
        count = len(all_ids)

        if all_ids:
            collection.delete(ids=all_ids)
            log_message(f"✅ API: Deleted {count} entries from Vector DB")
            return SystemActionResponse(
                success=True,
                message=f"Vector DB cleared ({count} entries deleted)"
            )
        else:
            return SystemActionResponse(
                success=True,
                message="Vector DB is already empty"
            )

    except Exception as e:
        log_message(f"❌ API: Vector DB clear failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_app.post("/system/reset-defaults", response_model=SystemActionResponse, tags=["System"])
async def reset_to_defaults():
    """
    Reset all settings to defaults.

    Loads default values from config.py and saves them to settings.json.
    Backend restart may be required for changes to take effect.
    """
    from ..settings import reset_to_defaults as do_reset

    log_message("💾 API: Resetting to default settings...")

    if do_reset():
        log_message("✅ API: Settings reset to defaults")
        return SystemActionResponse(
            success=True,
            message="Settings reset to defaults",
            details="Restart backend for changes to take effect"
        )
    else:
        raise HTTPException(status_code=500, detail="Failed to reset settings")
