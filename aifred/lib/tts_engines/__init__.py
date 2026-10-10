"""TTS engine plugin registry — SSOT for everything a caller needs to
know about an individual TTS backend.

Before this module existed, ~24 different places in the codebase carried
their own ``if engine == "xtts" / elif "moss" / elif "qwen3local"``
cascade. Adding a new engine meant remembering to touch every single
one of them, and missing one (which happened repeatedly during the
Qwen3-TTS rollout) led to subtle bugs like the regen-button doing
nothing or the voice dropdown showing Edge voices for a Qwen3 session.

The fix is a thin plugin layer: each engine lives in its own folder (``<key>/engine.py``, ``i18n.json``), subclasses
:class:`TTSEngine` and is found by the registry. Callers route through
the registry instead of branching on the engine key — so adding the
next engine is a new class + one dict entry, not a hunt across 24 files.

Public surface kept intentionally small:

    from aifred.lib.tts_engines import TTS_ENGINES, get_engine

    eng = get_engine("qwen3local")
    if eng.is_running():
        await eng.generate_speech(text="Hallo", voice="AIfred",
                                  language="de", speed=1.0, pitch=1.0)

Migration strategy: this module starts as a *parallel* SSOT — it does
NOT yet replace the if/elif cascades elsewhere. Each cascade gets
migrated one at a time, with a small commit per migration, so a bug
in the refactor stays bounded.
"""
from .base import SPEECH_UNITS, FailureReason, TTSEngine, TTSFailure
from .registry import (
    TTS_ENGINES,
    default_escalation,
    get_engine,
    gpu_engines,
    installed_gpu_engines,
    parse_speed_factor,
    require_engine,
    speech_unit_for,
    voice_names,
)

__all__ = [
    "SPEECH_UNITS",
    "FailureReason",
    "TTSEngine",
    "TTSFailure",
    "TTS_ENGINES",
    "default_escalation",
    "get_engine",
    "gpu_engines",
    "installed_gpu_engines",
    "parse_speed_factor",
    "require_engine",
    "speech_unit_for",
    "voice_names",
]
