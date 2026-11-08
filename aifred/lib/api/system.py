"""System endpoints: restart the backend's service or AIfred, reset the settings to their defaults."""

from pydantic import BaseModel, Field

from ..logging_utils import log_message
from ..user_error import NotDone
from .app import api_app
from .schemas import SystemActionResponse


class BackendRestart(BaseModel):
    backend_type: str
    ready_after_s: float | None = Field(description="Seconds until it answered again; null: not yet (it may still come up)")


@api_app.post("/system/restart-backend", response_model=BackendRestart, tags=["System"])
async def restart_backend() -> BackendRestart:
    """Restart the service of the backend in use (Ollama, or llama-swap for llama.cpp and vLLM) and wait until it
    answers. 422 for the cloud, which has no service here."""
    from ..backend_switch import restart_backend as restart_service_of
    from ..settings import persisted_settings

    backend_type = persisted_settings()["backend_type"]
    log_message(f"🔄 API: restarting the service of {backend_type}...")
    seconds = await restart_service_of(backend_type)
    log_message(f"✅ API: {backend_type} ready after {seconds:.1f} s" if seconds is not None else f"⚠️ API: {backend_type} not ready yet")
    return BackendRestart(backend_type=backend_type, ready_after_s=seconds)


@api_app.post("/system/restart-aifred", response_model=SystemActionResponse, tags=["System"])
async def restart_aifred() -> SystemActionResponse:
    """Restart AIfred's service; answers first, the restart follows after a second."""
    from ..process_utils import restart_aifred_soon

    log_message("🔄 API: AIfred restart requested...")
    restart_aifred_soon()
    return SystemActionResponse(success=True, message="AIfred restart scheduled", details="Service will restart in ~1 second")


@api_app.post("/system/reset-defaults", response_model=SystemActionResponse, tags=["System"])
async def reset_to_defaults() -> SystemActionResponse:
    """Replace settings.json by the defaults of config.py."""
    from ..settings import reset_to_defaults as do_reset

    log_message("💾 API: Resetting to default settings...")
    if not do_reset():
        raise NotDone("settings_not_reset")
    log_message("✅ API: Settings reset to defaults")
    return SystemActionResponse(success=True, message="Settings reset to defaults", details="Restart backend for changes to take effect")
