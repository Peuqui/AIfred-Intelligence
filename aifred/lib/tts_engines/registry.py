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
    instances = [cls() for cls in TTSEngine.__subclasses__()]
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
