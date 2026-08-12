"""Plugin registry — auto-discovers all ``TTSEngine`` subclasses.

Drop a new ``foo.py`` into this package that defines a ``TTSEngine``
subclass with ``key`` and ``label_short``, and it lights up everywhere
(UI dropdown, calibration picker, channel-plugin options) — no extra
registration code, no central dict to keep in sync.

The discovery order is determined by the engine's ``display_order``
class attribute (lower number = earlier in the UI). This is the
single source of truth for "which TTS engines exist" — ``config.py``
derives ``TTS_ENGINE_KEYS`` from this dict, no parallel hardcoded list.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Iterator

from .base import TTSEngine


def _discover_engines() -> dict[str, TTSEngine]:
    """Import every sibling ``*.py`` module so its TTSEngine subclass
    registers itself, then build the ordered ``{key: instance}`` dict."""
    pkg_dir = Path(__file__).parent
    pkg_name = __name__.rsplit(".", 1)[0]   # "aifred.lib.tts_engines"
    skip = {"__init__", "base", "registry"}

    for py_file in sorted(pkg_dir.glob("*.py")):
        if py_file.stem in skip:
            continue
        importlib.import_module(f"{pkg_name}.{py_file.stem}")

    # __subclasses__() returns direct subclasses only — fine because
    # engines inherit straight from TTSEngine. Sort by display_order so
    # the UI dropdown stays predictable; ties broken by key for stability.
    instances = [cls() for cls in TTSEngine.__subclasses__()]  # type: ignore[abstract]
    instances.sort(key=lambda e: (e.display_order, e.key))
    return {e.key: e for e in instances}


TTS_ENGINES: dict[str, TTSEngine] = _discover_engines()


def get_engine(key: str) -> TTSEngine | None:
    """Return the TTSEngine for ``key``, or ``None`` if no engine
    registered under that key. Use this instead of ``TTS_ENGINES.get``
    at call sites so the call signature is easy to grep for."""
    return TTS_ENGINES.get(key)


def gpu_engines() -> Iterator[TTSEngine]:
    """Iterate engines that occupy GPU VRAM (and therefore need to be
    juggled by the VRAM manager / LLM calibration)."""
    return (e for e in TTS_ENGINES.values() if e.needs_gpu)


def installed_gpu_engines() -> list[TTSEngine]:
    """GPU engines whose Docker image is built on this host — i.e.
    engines the user can actually calibrate against right now. Used by
    the calibration picker so we never show engines that aren't usable."""
    return [e for e in gpu_engines() if e.is_installed()]


def channel_engine_options() -> list[tuple[str, str]]:
    """``(key, short_label)`` pairs for channel-plugin dropdowns (FreeEcho.2).
    Replaces ``config.get_tts_engine_channel_options``."""
    return [
        (e.key, e.label_short)
        for e in TTS_ENGINES.values()
        if e.suitable_for_channels
    ]


def voice_names(engine: TTSEngine) -> list[str]:
    """Katalog-Namen einer Engine: live ``get_voices()``, bei Fehler ODER
    leerem Ergebnis der statische ``voices_fallback``.

    Container-Engines liefern ``{}`` (keine Exception), solange der
    Container steht — das dokumentierte Katalog-Muster aus base.py. SSOT
    für das narrator-Plugin und die Narrator-UI; die weiteren Varianten
    (XTTS-State-Cache im Hot-Path, async-to_thread in der Kalibrierung,
    Dict-statt-Namen) bleiben bewusst bei ihren Aufrufern — deren
    Kontext-Anforderungen sind echte Unterschiede, keine Drift.
    """
    try:
        voices = engine.get_voices()
    except Exception:  # noqa: BLE001 — down/unreachable == leerer Katalog
        voices = {}
    return list((voices or engine.voices_fallback).keys())


def resolve_narrator_engine(
    narrator_engine: str,
    enable_tts: bool,
    tts_engine: str,
    fallback_engine: str,
) -> str:
    """Narrator-Engine-Entscheidung — SSOT für Plugin UND UI-Mixin:
    ``"auto"`` folgt bei aktivem TTS der Sprach-Engine, sonst dem
    GPU-freien Fallback (die geladene LLM behält ihr VRAM). Nimmt reine
    Werte statt State/Settings, damit beide Aufrufer (Reflex-Var mit
    deps, Plugin mit settings.json) dieselbe Logik teilen können.
    """
    if narrator_engine and narrator_engine != "auto":
        return narrator_engine
    return tts_engine if enable_tts else fallback_engine


def parse_speed_factor(raw: object) -> "float | None":
    """``"1.25x"``/``"1.25"``/``1.25`` → 1.25; ``None``/``""``/Müll → None.

    Nur der PARSER ist SSOT — ob der Aufrufer bei None auf einen Default
    zurückfällt (Browser-UI) oder fail-loud abbricht (FreeEcho2-Reply),
    ist bewusste Policy pro Konsument.
    """
    if raw in (None, ""):
        return None
    try:
        return float(str(raw).replace("x", ""))
    except (ValueError, TypeError):
        return None
