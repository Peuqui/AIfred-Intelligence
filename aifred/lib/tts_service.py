"""Adapter between the TTS-Suite client and what AIfred's code asks about speech output.

SSOT for everything AIfred derives from the service's answers: engine keys with GPU need,
installed GPU engines, the unit per engine, labels per language, the status key shown in
the list (texts ``tts_status_<key>``), the place of an entry (``tts_location_*``), the
VRAM footprint per engine and the control calls (start, stop, reload, set_config).

The list is read from ``GET /engines`` (one ``EngineInfo`` per entry of the service's
escalation list, in speaking order, with host, status, unit and footprint). The answer is
cached for ``TTS_SUITE_CACHE_TTL_S`` seconds, no longer: computed UI variables and planning
calls ask often. Callers that decide on the live state (running engine, footprint) bypass
the cache; every writing call clears it.

The service not being reachable is an error, not an empty list: ``TTSSuiteUnreachable``
passes through. UI code that must keep rendering shows ``unreachable_text`` instead.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from time import monotonic
from typing import Any

from .config import TTS_SUITE_CACHE_TTL_S
from .i18n import TranslationManager, t
from .tts_client import (
    EngineControlFailed,
    EngineInfo,
    EngineInUse,
    TTSSuiteError,
    get_tts_client,
)


class TTSVramUnknown(RuntimeError):
    """The service could not measure the VRAM of a running local GPU engine."""


# ---- Cache of GET /engines ---------------------------------------------------

_lock = threading.Lock()
_cached: tuple[float, tuple[EngineInfo, ...]] | None = None


def reset_cache() -> None:
    """Forget the cached list (called by every writing call; tests call it between cases)."""
    global _cached
    with _lock:
        _cached = None


def entries(*, fresh: bool = False) -> tuple[EngineInfo, ...]:
    """The service's escalation list in speaking order, one ``EngineInfo`` per entry.

    ``fresh=True`` ignores the cache (and refreshes it).

    Raises:
        TTSSuiteUnreachable: the service does not answer; there is no stand-in list.
    """
    global _cached
    with _lock:
        if not fresh and _cached is not None and monotonic() - _cached[0] < TTS_SUITE_CACHE_TTL_S:
            return _cached[1]
        listed = tuple(get_tts_client().engines())
        _cached = (monotonic(), listed)
        return listed


def unreachable_text(error: TTSSuiteError, lang: str | None = None) -> str:
    """Message for UI places that show the failure instead of data."""
    return t("tts_service_unreachable", lang=lang, reason=str(error))


# ---- Facts about entries -------------------------------------------------------


def _is_active(info: EngineInfo) -> bool:
    return info.enabled and not info.deactivated


def _is_local_gpu(info: EngineInfo) -> bool:
    return info.needs_gpu and info.host is None and not info.deactivated


def _pick(names: Mapping[str, str], lang: str | None) -> str:
    return names[TranslationManager._resolve_lang(lang)]


def status_key(info: EngineInfo) -> str:
    """What an entry would do right now, as key of ``tts_status_<key>``."""
    if info.deactivated:
        return "engine_deactivated"
    if not info.enabled:
        return "disabled"
    if info.browser:
        return "ready"  # the client's own voice; the service neither starts nor probes it
    if not info.installed:
        return "no_image"
    if info.status == "starting":
        return "starting"
    if info.status == "running":
        if info.cloud:
            return "cloud"
        return "running" if info.needs_gpu else "ready"
    if info.start_hint is not None:
        return "start_failed"
    if info.status == "unreachable":
        return "unreachable"
    return "no_access" if info.cloud else "sleeping"


def location_label(info: EngineInfo, lang: str | None = None) -> str:
    """Where an entry speaks: host name, device (the user's browser), cloud, local (GPU
    container on this machine) or CPU."""
    if info.host is not None:
        return info.host
    if info.deactivated:
        return t("tts_location_deactivated", lang=lang)
    if info.browser:
        return t("tts_location_device", lang=lang)
    if info.cloud:
        return t("tts_location_cloud", lang=lang)
    if info.needs_gpu:
        return t("tts_location_local", lang=lang)
    return t("tts_location_cpu", lang=lang)


def entry_label(info: EngineInfo, lang: str | None = None) -> str:
    """``Engine · place`` as the list shows an entry."""
    return f"{_pick(info.label_short, lang)} · {location_label(info, lang)}"


def _first_entry_of(engine_key: str) -> EngineInfo:
    for info in entries():
        if info.engine == engine_key:
            return info
    raise ValueError(f"TTS engine {engine_key!r} is not in the service's list")


def engine_label(engine_key: str, lang: str | None = None) -> str:
    return _pick(_first_entry_of(engine_key).label, lang)


def engine_label_short(engine_key: str, lang: str | None = None) -> str:
    return _pick(_first_entry_of(engine_key).label_short, lang)


def engine_key_for_label(label: str) -> str:
    """Reverse of ``engine_label`` over every language; a label that names no engine is returned as is."""
    for info in entries():
        if label in info.label.values():
            return info.engine
    return label


def speech_unit_for(engine_key: str) -> str:
    """Unit of the speech output of an engine (``sentence``, ``paragraph``, ``whole``):
    the user's choice kept by the service."""
    return _first_entry_of(engine_key).unit


def first_enabled_entry() -> EngineInfo | None:
    """The topmost active entry; its unit decides whether the browser streams."""
    return next((info for info in entries() if _is_active(info)), None)


def _unique(keys: list[str]) -> list[str]:
    return list(dict.fromkeys(keys))


def usable_engine_keys() -> list[str]:
    """Engines worth configuring, in list order: the service can start them (image built
    or none needed) or they run on another host."""
    return _unique([info.engine for info in entries() if not info.deactivated and info.installed])


def gpu_engine_keys() -> set[str]:
    """Engines that occupy VRAM on this machine."""
    return {info.engine for info in entries() if _is_local_gpu(info)}


def installed_gpu_engine_keys() -> list[str]:
    """Local GPU engines whose image is built: the ones one can calibrate against."""
    return _unique(
        [info.engine for info in entries() if _is_local_gpu(info) and info.installed],
    )


def has_active_local_gpu_entry(engine_key: str) -> bool:
    return any(_is_active(info) and _is_local_gpu(info) and info.engine == engine_key for info in entries())


def active_local_gpu_keys() -> list[str]:
    """Local GPU engines of active entries, in speaking order."""
    return _unique([info.engine for info in entries() if _is_active(info) and _is_local_gpu(info)])


# ---- Live state (never cached) --------------------------------------------------


def running_gpu_engine_keys() -> list[str]:
    """Local GPU engines that run right now, in list order."""
    return _unique([info.engine for info in entries(fresh=True) if _is_local_gpu(info) and info.status == "running"])


def running_gpu_engine() -> str:
    """Key of the first local GPU engine that runs right now, ``""`` if none."""
    return next(iter(running_gpu_engine_keys()), "")


def local_gpu_footprint() -> dict[str, dict[str, int]]:
    """VRAM (MiB) of the running local GPU engines: ``{engine_key: {gpu_uuid: MiB}}``.

    Raises:
        TTSVramUnknown: the service could not measure an engine that runs.
    """
    footprint: dict[str, dict[str, int]] = {}
    for info in entries(fresh=True):
        if not _is_local_gpu(info) or info.status != "running":
            continue
        if info.vram_mib is None:
            raise TTSVramUnknown(f"the VRAM of {info.engine} could not be measured by the TTS service")
        if info.vram_mib:
            footprint[info.engine] = dict(info.vram_mib)
    return footprint


# ---- Control calls (clear the cache) -----------------------------------------------


def start_entry(engine: str, host: str | None = None) -> None:
    """Start the entry's container and wait until it serves (blocks; may take minutes)."""
    try:
        get_tts_client().start(engine, host)
    finally:
        reset_cache()


def stop_entry(engine: str, host: str | None = None) -> None:
    try:
        get_tts_client().stop(engine, host)
    finally:
        reset_cache()


def start_engine(engine_key: str) -> tuple[bool, str]:
    """Start the local container of an engine and wait until it serves; ``(success, message)``.

    The service reports a start that did not work (no card fits, container failed) as
    ``EngineControlFailed``; the reason is the message.
    """
    try:
        start_entry(engine_key)
    except EngineControlFailed as failed:
        return False, str(failed)
    return True, ""


def stop_engine(engine_key: str) -> tuple[bool, str]:
    """Stop the local container of an engine; ``(success, message)``.

    A reading that still runs on the engine refuses the stop (``EngineInUse``): reported as
    not stopped, the VRAM stays occupied.
    """
    try:
        stop_entry(engine_key)
    except EngineInUse as busy:
        return False, f"{engine_key} is in use and was not stopped: {busy}"
    except EngineControlFailed as failed:
        return False, str(failed)
    return True, ""


def reload_engines() -> dict[str, list[str]]:
    """Make the service read its engine folders again; ``{"removed": [...], "added": [...]}``."""
    try:
        return get_tts_client().reload()
    finally:
        reset_cache()


def set_config(config_json: Mapping[str, Any]) -> dict[str, Any]:
    """Store the service's configuration; returns the stored one."""
    try:
        return get_tts_client().set_config(config_json)
    finally:
        reset_cache()
