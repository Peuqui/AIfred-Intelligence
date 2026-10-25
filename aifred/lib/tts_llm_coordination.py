"""TTS/LLM coordination — keeps the language model's profile in line with the
planned TTS engine, so the VRAM budget has room for it.

Abstimmung zwischen Sprachmodell und Sprachausgabe: Wechselt der Browser die
geplante TTS-Engine (die, für die das LLM-Profil VRAM reserviert), stellt
``ensure_tts_state`` das llama-swap-Profil mit passender ``-tts-<engine>``-Variante
ein. Die TTS-Container fasst AIfred dabei nie an: der TTS-Dienst startet und entlädt
seine Engines selbst (Leerlauf-Abschaltung, Kartenregeln). Kanäle wechseln das Profil
nie — sie sprechen über die Liste von AIfred (``tts_service``).

Central function: ensure_tts_state(wanted_tts, backend_type)
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass

from .gpu_orc_client import GpuOrcError, describe, get_gpu_orc_client
from .service_status import LlamaSwapError, running_models
from .tts_service import active_local_keys, engine_label_short, gpu_engine_keys


def _label(engine_key: str) -> str:
    """Name of a TTS engine in status lines."""
    return engine_label_short(engine_key, "en")


def planned_tts_engine(model_id: str) -> str:
    """Die lokale GPU-Engine, für die das LLM-Profil ``<model>-tts-<engine>``
    Platz freihält: der oberste aktive lokale Eintrag der Liste von AIfred,
    für den das Modell ein kalibriertes TTS-Profil hat (das gibt es nur für lokale
    GPU-Engines). ``""``, wenn keiner passt — dann lädt das Basisprofil, und lokale
    GPU-Einträge sprechen nur, wenn Platz frei ist. Der Dienst wird nicht gefragt: Die
    Planung liest nur settings.json und die llama-swap-Konfiguration."""
    from pathlib import Path

    from .calibration import has_llamaswap_tts_variant
    from .config import LLAMASWAP_CONFIG_PATH

    return next(
        (
            engine_key for engine_key in active_local_keys()
            if has_llamaswap_tts_variant(Path(LLAMASWAP_CONFIG_PATH), model_id, engine_key)
        ),
        "",
    )


@dataclass
class TTSState:
    """Result of ensure_tts_state check."""

    success: bool = True
    changed: bool = False           # True if the LLM profile was switched


def _is_llm_loaded(backend_type: str) -> bool:
    """Check if an LLM is currently loaded in VRAM."""
    if backend_type != "llamacpp":
        return False
    try:
        return len(running_models()) > 0
    except LlamaSwapError:
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
        return any(f"-tts-{new_engine}" in model.name for model in running_models())
    except LlamaSwapError:
        return False


def ensure_tts_state(
    wanted_tts: str,
    backend_type: str = "llamacpp",
) -> Generator[str, None, TTSState]:
    """Bring the LLM profile in line with the planned TTS engine. Yields status after each step.

    This is the SINGLE SOURCE OF TRUTH for the LLM side of the TTS plan. No TTS container is
    started, stopped or loaded here.

    Args:
        wanted_tts: Planned TTS engine key (its ``-tts-<engine>`` profile reserves VRAM), or "" for none
        backend_type: Active LLM backend ("llamacpp", "ollama", etc.)

    Yields:
        Status messages after each blocking step.
    """
    # Lightweight engines and "none" need no TTS profile: the base profile resolves by itself.
    if not wanted_tts or wanted_tts not in gpu_engine_keys():
        return TTSState(success=True)

    # A model already on the matching variant has the slot reserved: no llama-swap restart,
    # no needless LLM cold start.
    if _llm_already_on_tts_variant(wanted_tts, backend_type):
        yield f"LLM already on the {_label(wanted_tts)} profile"
        return TTSState(success=True)

    yield from _switch_llm_profile(backend_type, wanted_tts)
    return TTSState(success=True, changed=True)


def _switch_llm_profile(backend_type: str, wanted_tts: str) -> Generator[str, None, None]:
    """Unload the LLM if one is loaded, have GPU-Orc clear every card of everything but llama-swap's
    models (the new profile leaves room for ``wanted_tts``; the TTS service places an engine again after
    the load), restart the LLM backend so it picks up the TTS-calibrated profile. What stays on the cards,
    or a GPU-Orc that does not answer, is reported here; the switch goes on."""
    from .process_utils import unload_llm_models

    if _is_llm_loaded(backend_type):
        actions = unload_llm_models(backend_type)
        if actions:
            yield f"LLM unloaded: {', '.join(actions)}"
    try:
        result = get_gpu_orc_client().free_all_but_models(reason=f"AIfred: the {_label(wanted_tts)} profile")
    except GpuOrcError as error:
        yield f"⚠️ GPU-Orc could not clear the cards: {error}"
    else:
        for line, _category in describe(result):
            yield line
    if not restart_llm_backend(backend_type):
        yield "⚠️ llama-swap did not start again — see journalctl -u llama-swap"
        return
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
    """Start llama-swap again after the profile switch (llama.cpp: ``unload_llm_models`` stopped it).
    The other backends were not stopped, there is nothing to start. ``False``: llama-swap did not start."""
    if backend_type != "llamacpp":
        return True
    from .process_utils import start_llama_swap

    return start_llama_swap()
