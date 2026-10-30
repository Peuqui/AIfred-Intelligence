"""TTS/LLM coordination — which LLM profile leaves room for the planned TTS engine.

Abstimmung zwischen Sprachmodell und Sprachausgabe: Das llama-swap-Profil
``<model>-tts-<engine>`` lässt auf den Karten des Modells Platz für eine lokale
GPU-Engine. Welches Profil gilt, steht allein in settings.json und der
llama-swap-Konfiguration (``planned_tts_engine``); die Modellwahl hängt das
Suffix an, sobald das Modell geladen wird. Das Laden räumt die Zielkarten über
GPU-Orc (``LLMBackend._load_model``), llama-swap tauscht das vorige Modell selbst
aus. Hier wird nichts gestoppt, entladen oder neu gestartet; die TTS-Container
fasst AIfred nie an — der TTS-Dienst setzt seine Engines selbst auf eine Karte
mit Platz. Kanäle wechseln das Profil nie, sie sprechen über die Liste von AIfred
(``tts_service``).
"""

from __future__ import annotations

from .tts_service import active_local_keys


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


def get_effective_model_info(backend_type: str = "llamacpp") -> str:
    """The model AIfred loads (TTS variant included) with its context, for the model list line."""
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
