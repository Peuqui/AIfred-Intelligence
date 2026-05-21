"""Central registry — exactly one place that knows about all engines.

When you add a new TTS backend, write its TTSEngine subclass in
``aifred/lib/tts_engines/<key>.py`` and add one entry to the
:data:`TTS_ENGINES` dict below. Nothing else in the codebase has to
change for the engine to be visible in the lifecycle dispatchers
(:func:`get_engine`, :func:`gpu_engines`, ...).
"""
from __future__ import annotations

from typing import Iterator

from .base import TTSEngine
from .qwen3local import Qwen3LocalEngine
from .xtts import XTTSEngine
from .moss import MOSSEngine
from .fishspeech import FishSpeechEngine
from .dashscope import DashScopeEngine
from .edge import EdgeEngine
from .piper import PiperEngine
from .espeak import EspeakEngine


# Order matches the UI's engine-dropdown order (most-recommended first).
# Cross-check with config.TTS_ENGINE_KEYS during the migration period;
# once the migration is complete, TTS_ENGINE_KEYS will be derived from
# this dict directly.
TTS_ENGINES: dict[str, TTSEngine] = {
    "qwen3local": Qwen3LocalEngine(),
    "xtts":       XTTSEngine(),
    "fishspeech": FishSpeechEngine(),
    "moss":       MOSSEngine(),
    "dashscope":  DashScopeEngine(),
    "piper":      PiperEngine(),
    "espeak":     EspeakEngine(),
    "edge":       EdgeEngine(),
}


def get_engine(key: str) -> TTSEngine | None:
    """Return the TTSEngine for ``key``, or ``None`` if no engine
    registered under that key. Use this instead of `TTS_ENGINES.get`
    at call sites so the call signature is easy to grep for during
    further migrations."""
    return TTS_ENGINES.get(key)


def gpu_engines() -> Iterator[TTSEngine]:
    """Iterate engines that occupy GPU VRAM (and therefore need to be
    juggled by the VRAM manager / LLM calibration). Replaces the
    hardcoded ``GPU_ENGINES = {"xtts", "moss", "qwen3local"}`` set."""
    return (e for e in TTS_ENGINES.values() if e.needs_gpu)


def installed_gpu_engines() -> list[TTSEngine]:
    """GPU engines whose docker-compose.yml is present on this host —
    i.e. engines the user can actually calibrate against. Used by the
    calibration picker so we never show or wait on engines that aren't
    provisioned."""
    return [e for e in gpu_engines() if e.is_installed()]


def channel_engine_options() -> list[tuple[str, str]]:
    """``(key, short_label)`` pairs for channel-plugin dropdowns (FreeEcho.2).
    Replaces ``config.get_tts_engine_channel_options``."""
    return [
        (e.key, e.label_short)
        for e in TTS_ENGINES.values()
        if e.suitable_for_channels
    ]
