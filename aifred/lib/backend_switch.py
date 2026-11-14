"""Switching the LLM backend and the cloud provider, restarting the backend's service, and the rules on
the models: the models a backend offers, which saved models it no longer has, which vision model it falls
back to, what leaving a backend frees.

settings.json is the truth. A switch writes the backend, then the models the new backend has for each agent
(``backend_models``, else config.py's defaults) checked against its catalog, with the sampling those models bring.
Notes are English debug lines: the server writes them to its log.
"""

from __future__ import annotations

import asyncio
import time

from .config import (
    BACKEND_READY_POLL_S,
    BACKEND_URLS,
    CLOUD_API_PROVIDERS,
    LLAMASWAP_BACKENDS,
    LLAMASWAP_READY_TIMEOUT_S,
    LLAMASWAP_START_SETTLE_S,
    OLLAMA_READY_TIMEOUT_S,
)
from .logging_utils import log_message
from .runtime_settings import RuntimeSettings
from .user_error import Conflict, Invalid, NotDone

# One switch at a time in this process: checked and set with no await in between.
_switching = False


# ── The models a backend offers ─────────────────────────────────────────


async def _cloud_catalog(provider: str) -> tuple[dict[str, str], list[str]]:
    from ..backends.cloud_api import CloudAPIBackend, get_cloud_api_key
    from .model_manager import sort_models_grouped

    provider_config = CLOUD_API_PROVIDERS.get(provider)
    if provider_config is None:
        return {}, [f"⚠️ Unknown cloud provider: {provider}"]
    key = get_cloud_api_key(provider)
    if not key:
        return {}, [f"⚠️ API key missing: Set {provider_config['env_key']} in .env"]
    backend = CloudAPIBackend(base_url=provider_config["base_url"], api_key=key, provider=provider)
    try:
        models = await backend.list_models()  # empty when the provider refuses (it logs why)
    finally:
        await backend.close()
    if not models:
        return {}, [f"⚠️ No models returned from {provider_config['name']} API"]
    return sort_models_grouped({model: model for model in models}), [f"☁️ {provider_config['name']}: {len(models)} models loaded"]


async def model_catalog(backend_type: str, cloud_provider: str, *, start_service: bool = False) -> tuple[dict[str, str], list[str]]:
    """The models of the backend (id → label, grouped by family and size) and what happened on the way; a cloud
    provider without key lists nothing. ``start_service`` starts llama-swap once when it does not answer (a switch
    or a start of the backend; never on a mere look, a calibration stops it on purpose)."""
    from .model_discovery import discover_models

    if backend_type == "cloud_api":
        return await _cloud_catalog(cloud_provider)
    url = BACKEND_URLS[backend_type]
    if backend_type not in LLAMASWAP_BACKENDS:
        return await asyncio.to_thread(discover_models, backend_type, url), []
    notes: list[str] = []
    models = await asyncio.to_thread(discover_models, backend_type, url)
    if not models and start_service:
        from .process_utils import start_llama_swap

        notes.append("⚠️ llama-swap not reachable, starting service...")
        if await asyncio.to_thread(start_llama_swap):
            await asyncio.sleep(LLAMASWAP_START_SETTLE_S)
            models = await asyncio.to_thread(discover_models, backend_type, url)
    kind = "vLLM" if backend_type == "vllm" else "llama.cpp"
    notes.append(f"📂 Found {len(models)} {kind} models" if models else f"⚠️ llama-swap not reachable at {url}")
    return models, notes


# ── Rules on the models of a target (RuntimeSettings) ──


def drop_unavailable_models(target: RuntimeSettings, catalog: dict[str, str]) -> list[str]:
    """Forget saved models the backend does not offer (deleted, never pulled, another provider's): AIfred's is
    cleared (the user picks one; a turn without a model is refused), every other agent and the Automatik then run
    on AIfred's. The Vision agent has its own rule (``choose_vision_model``). An empty catalog clears nothing: a
    backend that is down for a moment keeps the choice."""
    if not catalog:
        return []
    notes = []
    for agent, tuning in target.agent_tuning.items():
        if agent == "vision" or not tuning.model_id or tuning.model_id in catalog:
            continue
        notes.append(f"⚠️ {agent}: '{tuning.model_id}' not in {target.backend_type} — "
                     + ("cleared, pick a model" if agent == "aifred" else "now runs on AIfred's model"))
        tuning.model_id = ""
        tuning.model = ""
    if target.automatik_model_id and target.automatik_model_id not in catalog:
        notes.append(f"⚠️ Automatik: '{target.automatik_model_id}' not in {target.backend_type} — now runs on AIfred's model")
        target.automatik_model_id = ""
    return notes


def choose_cloud_main_model(target: RuntimeSettings, catalog: dict[str, str]) -> list[str]:
    """AIfred's model after a change of the cloud provider: the provider's first model unless it offers his."""
    aifred = target.agent_tuning["aifred"]
    if not catalog or aifred.model_id in catalog:
        return []
    aifred.model_id = next(iter(catalog))
    aifred.model = catalog[aifred.model_id]
    return [f"⚙️ AIfred: {aifred.model_id}"]


def choose_vision_model(target: RuntimeSettings, candidates: dict[str, str]) -> list[str]:
    """The Vision agent's model when it has none or one the backend does not offer: the first vision model.
    Without vision models it keeps what it has."""
    vision = target.agent_tuning["vision"]
    if not candidates or vision.model_id in candidates:
        return []
    note = (f"⚠️ Saved vision_model '{vision.model_id}' not found in vision models, auto-selecting..."
            if vision.model_id else "⚙️ Auto-selecting vision_model")
    vision.model_id = next(iter(candidates))
    vision.model = candidates[vision.model_id]
    return [note, f"⚙️ vision_model: {vision.model_id}"]


# ── Leaving a backend ────────────────────────────────────────────────────


async def leave_backend(old: str, new: str) -> list[str]:
    """Free what the old backend holds: Ollama unloads its models; llama-swap stops when the new backend is none
    of its family (llama.cpp and vLLM are views on the same service)."""
    if old == "ollama":
        from ..backends.ollama import OllamaBackend

        backend = OllamaBackend()
        try:
            success, unloaded = await backend.unload_all_models()
        finally:
            await backend.close()
        if not success:
            return ["⚠️ Could not unload the Ollama models"]
        return [f"✅ Unloaded {len(unloaded)} Ollama model(s)" if unloaded else "ℹ️ No Ollama models were loaded"]
    if old in LLAMASWAP_BACKENDS:
        if new in LLAMASWAP_BACKENDS:
            return ["ℹ️ llama-swap keeps running (same service)"]
        from .process_utils import stop_llama_swap

        return ["✅ llama-swap stopped" if await asyncio.to_thread(stop_llama_swap) else "⚠️ Could not stop llama-swap"]
    return []


# ── The server's switch ──────────────────────────────────────────────────


async def _settle_models(view: RuntimeSettings, cloud_provider: str, *, choose_main: bool = False) -> list[str]:
    """The backend's catalog against the view's models (with ``choose_main`` AIfred's first: a new cloud
    provider), the vision model, the sampling and properties of the models; written to settings.json."""
    from .agent_choices import (
        chat_agents,
        reset_sampling,
        sampling_defaults,
        save_choices,
        vision_candidates,
    )
    from .runtime_settings import load_agent_model_params

    catalog, notes = await model_catalog(view.backend_type, cloud_provider, start_service=True)
    if choose_main:
        notes += choose_cloud_main_model(view, catalog)
    notes += drop_unavailable_models(view, catalog)
    notes += choose_vision_model(view, await vision_candidates(view, catalog, view.backend_url))
    for agent, tuning in view.agent_tuning.items():
        load_agent_model_params(view, agent, tuning.model_id)
    for agent, _name, _emoji in chat_agents():
        reset_sampling(view, agent, sampling_defaults(view, agent), include_temperature=False)
    if not save_choices(view, list(view.agent_tuning)):
        raise NotDone("settings_not_saved")
    return notes


def _claim_switch() -> None:
    global _switching
    from .calibration_gate import is_calibration_active

    if _switching:
        raise Conflict("backend_switch_running")
    if is_calibration_active():
        raise Conflict("backend_switch_during_calibration")
    _switching = True


def _release_switch() -> None:
    global _switching
    _switching = False


async def switch_backend(backend_type: str) -> None:
    """Switch to ``backend_type`` (one of the backends these GPUs can run, or the cloud). Refused while another
    switch or a calibration runs: leaving llama-swap stops it under the calibration's feet."""
    from .backend_overview import available_backends
    from .gpu_detection import server_gpu_info
    from .runtime_settings import load_runtime_settings
    from .settings import persisted_settings, update_settings

    _claim_switch()
    try:
        if backend_type not in available_backends(await asyncio.to_thread(server_gpu_info)):
            raise Invalid("backend_not_offered", backend=backend_type)
        saved = persisted_settings()
        old = saved["backend_type"]
        if backend_type == old:
            return
        log_message(f"🔄 Backend: {old} → {backend_type}")
        notes = await leave_backend(old, backend_type)
        if not update_settings({"backend_type": backend_type}):
            raise NotDone("settings_not_saved")
        notes += await _settle_models(load_runtime_settings(), saved["cloud_api_provider"])
    finally:
        _release_switch()
    for note in notes:
        log_message(f"   {note}")


async def set_cloud_provider(provider: str) -> None:
    """The provider of the backend ``cloud_api``. While the cloud is the backend, its models change with it: AIfred
    gets the provider's first model unless the provider offers his, every other agent keeps a model the provider
    has and else runs on AIfred's."""
    from .runtime_settings import load_runtime_settings
    from .settings import update_settings

    if provider not in CLOUD_API_PROVIDERS:
        raise Invalid("unknown_cloud_provider", provider=provider)
    _claim_switch()
    try:
        if not update_settings({"cloud_api_provider": provider}):
            raise NotDone("settings_not_saved")
        view = load_runtime_settings()
        if view.backend_type != "cloud_api":
            return
        notes = await _settle_models(view, provider, choose_main=True)
    finally:
        _release_switch()
    log_message(f"☁️ Cloud provider: {provider}")
    for note in notes:
        log_message(f"   {note}")


# ── Restarting the backend's service ─────────────────────────────────────


async def _answers(url: str) -> bool:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=BACKEND_READY_POLL_S * 4) as client:
            return (await client.get(url)).status_code == 200
    except httpx.HTTPError:
        return False


async def restart_backend(backend_type: str) -> float | None:
    """Restart the service of ``backend_type`` (Ollama, or llama-swap for llama.cpp and vLLM) and wait until it
    answers. Returns the seconds from the restart until it answered, None when it did not in time (it may still
    come up: llama-swap's autoscan runs before it listens).

    Raises:
        Invalid: the backend has no service here (the cloud).
        NotDone: systemctl could not restart it.
    """
    from .process_utils import restart_llama_swap, restart_service

    if backend_type == "ollama":
        restarted = await asyncio.to_thread(restart_service, "ollama", True)
        ready_url, timeout = f"{BACKEND_URLS['ollama']}/api/tags", OLLAMA_READY_TIMEOUT_S
    elif backend_type in LLAMASWAP_BACKENDS:
        restarted = await asyncio.to_thread(restart_llama_swap)
        ready_url, timeout = f"{BACKEND_URLS[backend_type].rstrip('/')}/models", LLAMASWAP_READY_TIMEOUT_S
    else:
        raise Invalid("backend_without_service", backend=backend_type)
    if not restarted:
        raise NotDone("backend_restart_failed", backend=backend_type)
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if await _answers(ready_url):
            return time.monotonic() - started
        await asyncio.sleep(BACKEND_READY_POLL_S)
    return None
