"""Adapter between the TTS-Suite client and what AIfred's code asks about speech output.

SSOT for everything AIfred derives from the service's answers and from its own list:

- The LIST (entries ``{engine, host, enabled}``, their order) and the unit the user chose per
  engine live in AIfred's ``settings.json`` (``tts_escalation``, ``tts_toggles_per_engine``).
  The service keeps no list; every request brings AIfred's active entries (``request_engines``).
- The CATALOG (``GET /engines``) says what exists: label, status, ``installed``, ``vram_mib``,
  ``start_hint``, the recommended unit (``unit_default``), per engine and machine. 
Nothing is added or removed in AIfred: the list is always the MATCH of the stored
order/switches with the catalog (``entries``). Rows the stored list does not
know yet come at the end, switched off; stored rows the service no longer offers stay (shown
as not offered, never active, never sent). The match is a pure view; every change by the user
saves the complete matched list (``matched_list``).

AIfred writes nothing into the service and starts or stops no container there: the service
starts and unloads its engines itself when a request reaches it. AIfred only ASKS: before a
language model (or describer) needs cards a TTS container holds, ``release_gpu_engines`` asks
the service to take those containers down (``/gpu/release``, without force); the service decides
(a container a reading holds stays, the answer says so).

An entry is the pair of both: ``ListEntry`` (what the user decided + the catalog row of
``(engine, host)``, ``None`` if the service does not offer it). The catalog is cached for
``TTS_SUITE_CACHE_TTL_S`` seconds, no longer (computed UI variables and planning calls ask
often); the settings are never cached here. Callers that decide on the live state (running
engine, footprint) bypass the cache.

The service not being reachable is an error, not an empty catalog: ``TTSSuiteUnreachable``
passes through. UI code that must keep rendering shows ``unreachable_text`` instead.
"""

from __future__ import annotations

import threading
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Any

from .config import TTS_BROWSER_DEFAULT_UNIT, TTS_BROWSER_ENGINE_KEY, TTS_SUITE_CACHE_TTL_S
from .i18n import TranslationManager, t
from .logging_utils import log_message
from .settings import persisted_settings
from .tts_client import (
    SPEECH_UNITS,
    EngineInfo,
    EngineRef,
    GpuReleaseResult,
    SpeakSummary,
    TraceStep,
    TTSSuiteError,
    get_tts_client,
)


class TTSVramUnknown(RuntimeError):
    """The service could not measure the VRAM of a running local GPU engine."""


# ---- The catalog: GET /engines (cached) ---------------------------------------

_lock = threading.Lock()
_cached: tuple[float, tuple[EngineInfo, ...]] | None = None


def reset_cache() -> None:
    """Forget the cached catalog (tests call it between cases)."""
    global _cached
    with _lock:
        _cached = None


def catalog(*, fresh: bool = False) -> tuple[EngineInfo, ...]:
    """Every engine the service offers, per machine (``host`` None = this one).

    ``fresh=True`` ignores the cache (and refreshes it).

    Raises:
        TTSSuiteUnreachable: the service does not answer; there is no stand-in catalog.
    """
    global _cached
    with _lock:
        if not fresh and _cached is not None and monotonic() - _cached[0] < TTS_SUITE_CACHE_TTL_S:
            return _cached[1]
        offered = tuple(get_tts_client().engines())
        _cached = (monotonic(), offered)
        return offered


def unreachable_text(error: TTSSuiteError, lang: str | None = None) -> str:
    """Message for UI places that show the failure instead of data."""
    return t("tts_service_unreachable", lang=lang, reason=str(error))


def _pick(names: Mapping[str, str], lang: str | None) -> str:
    return names[TranslationManager._resolve_lang(lang)]


def _catalog_row_of_engine(engine_key: str) -> EngineInfo:
    for info in catalog():
        if info.engine == engine_key:
            return info
    raise ValueError(f"TTS engine {engine_key!r} is not in the service's catalog")


def engine_label(engine_key: str, lang: str | None = None) -> str:
    return _pick(_catalog_row_of_engine(engine_key).label, lang)


def engine_label_short(engine_key: str, lang: str | None = None) -> str:
    return _pick(_catalog_row_of_engine(engine_key).label_short, lang)


def engine_key_for_label(label: str) -> str:
    """Reverse of ``engine_label`` over every language.

    Raises:
        ValueError: the label names no engine (e.g. the "service unreachable" line a dropdown shows).
    """
    for info in catalog():
        if label in info.label.values():
            return info.engine
    raise ValueError(f"{label!r} names no TTS engine")


def voice_names(engine_key: str) -> list[str]:
    """Voice names the service offers for an engine, in the service's order.

    Raises:
        TTSSuiteUnreachable: the service does not answer; there is no stand-in catalog.
    """
    return [voice.name for voice in get_tts_client().voices(engine_key)]


def _is_local_gpu(info: EngineInfo) -> bool:
    return info.needs_gpu and info.host is None


def _unique(keys: list[str]) -> list[str]:
    return list(dict.fromkeys(keys))


def usable_engine_keys() -> list[str]:
    """Engines worth configuring, in catalog order: the service can start them (image built
    or none needed) or they run on another host."""
    return _unique([info.engine for info in catalog() if info.installed])


def gpu_engine_keys() -> set[str]:
    """Engines that occupy VRAM on this machine."""
    return {info.engine for info in catalog() if _is_local_gpu(info)}


def installed_gpu_engine_keys() -> list[str]:
    """Local GPU engines whose image is built: the ones one can calibrate against."""
    return _unique([info.engine for info in catalog() if _is_local_gpu(info) and info.installed])


# ---- Status and labels of a catalog row ----------------------------------------


def row_status_key(info: EngineInfo) -> str:
    """What the engine would do right now, as key of ``tts_status_<key>``."""
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
    """Where an engine speaks: host name, device (the user's browser), cloud, local (GPU
    container on this machine) or CPU."""
    if info.host is not None:
        return info.host
    if info.browser:
        return t("tts_location_device", lang=lang)
    if info.cloud:
        return t("tts_location_cloud", lang=lang)
    if info.needs_gpu:
        return t("tts_location_local", lang=lang)
    return t("tts_location_cpu", lang=lang)


def row_label(info: EngineInfo, lang: str | None = None) -> str:
    """``Engine · place`` as the list and the add choices show a catalog row."""
    return f"{_pick(info.label_short, lang)} · {location_label(info, lang)}"


def _local_gpu_rows(*, fresh: bool = False) -> list[EngineInfo]:
    return [info for info in catalog(fresh=fresh) if _is_local_gpu(info)]


# ---- AIfred's list: settings + catalog -----------------------------------------


@dataclass(frozen=True)
class ListEntry:
    """One entry of AIfred's list. ``info`` is the catalog row of ``(engine, host)``; ``None``
    when the service does not offer that pair (engine removed, host removed) or when the
    entry was built without the catalog (``stored_entries``)."""

    engine: str
    host: str | None
    enabled: bool
    unit: str  # the user's choice, else the engine's ``unit_default``; "" without catalog and choice
    info: EngineInfo | None

    @property
    def active(self) -> bool:
        """Switched on and offered: the entries a request carries."""
        return self.enabled and self.info is not None


def stored_list() -> list[dict[str, Any]]:
    """The raw list from settings.json: ``{"engine", "host", "enabled"}`` per entry, in order."""
    return [dict(raw) for raw in persisted_settings()["tts_escalation"]]


def matched_list(rows: Sequence[EngineInfo]) -> list[dict[str, Any]]:
    """The stored list plus every catalog row it does not know yet, those at the end, switched off
    (cost, cloud). Stored entries the catalog no longer has stay."""
    stored = stored_list()
    known = {(raw["engine"], raw["host"]) for raw in stored}
    return stored + [
        {"engine": info.engine, "host": info.host, "enabled": False}
        for info in rows
        if (info.engine, info.host) not in known
    ]


def check_stored_settings(settings: Mapping[str, Any]) -> None:
    """Refuse a list that cannot be right: an entry without exactly its fields or the same
    engine on the same machine twice. The catalog is not asked
    (the service validates each request).

    Raises:
        ValueError: with the reason.
    """
    seen: set[tuple[str, str | None]] = set()
    for position, raw in enumerate(settings["tts_escalation"]):
        if set(raw) != {"engine", "host", "enabled"}:
            raise ValueError(f"tts_escalation[{position}] must have exactly engine, host, enabled; got {sorted(raw)}")
        pair = (raw["engine"], raw["host"])
        if pair in seen:
            raise ValueError(f"{raw['engine']}@{raw['host'] or 'local'} is already in the list")
        seen.add(pair)


def chosen_unit(engine_key: str) -> str | None:
    """The unit the user chose for the engine (settings.json), ``None`` = no choice."""
    chosen = persisted_settings().get("tts_toggles_per_engine", {}).get(engine_key, {}).get("unit")
    if chosen is not None and chosen not in SPEECH_UNITS:
        raise ValueError(f"speech unit of {engine_key!r} must be one of {SPEECH_UNITS}, got {chosen!r}")
    return str(chosen) if chosen is not None else None


def stored_entries() -> list[ListEntry]:
    """AIfred's stored list without the catalog (``info`` None, unit only where the user chose
    one): what the editor can still show while the service does not answer."""
    return [
        ListEntry(str(raw["engine"]), raw["host"], bool(raw["enabled"]), chosen_unit(str(raw["engine"])) or "", None)
        for raw in stored_list()
    ]


def entries(*, fresh: bool = False) -> tuple[ListEntry, ...]:
    """AIfred's list in speaking order (matched with the catalog), each entry joined with its row.

    Raises:
        TTSSuiteUnreachable: the service does not answer.
    """
    rows = catalog(fresh=fresh)
    by_pair = {(info.engine, info.host): info for info in rows}
    default_unit = {info.engine: info.unit_default for info in rows}
    joined: list[ListEntry] = []
    for raw in matched_list(rows):
        engine, host = str(raw["engine"]), raw["host"]
        info = by_pair.get((engine, host))
        unit = chosen_unit(engine) or (info.unit_default if info else default_unit.get(engine, ""))
        joined.append(ListEntry(engine, host, bool(raw["enabled"]), unit, info))
    return tuple(joined)


def status_key(entry: ListEntry) -> str:
    """What the entry would do right now, as key of ``tts_status_<key>``."""
    if entry.info is None:
        return "not_offered"
    if not entry.enabled:
        return "disabled"
    return row_status_key(entry.info)


def entry_label(entry: ListEntry, lang: str | None = None) -> str:
    """``Engine · place``; for an entry the service does not offer, its key and host."""
    if entry.info is None:
        return f"{entry.engine} · {entry.host or t('tts_location_this_machine', lang=lang)}"
    return row_label(entry.info, lang)


def speech_unit_for(engine_key: str) -> str:
    """Unit of the speech output of an engine (``sentence``, ``paragraph``, ``whole``): the
    user's choice, else the engine's recommendation from the catalog (system-wide, not per agent).

    Raises:
        TTSSuiteUnreachable: no choice made and the service does not answer.
    """
    return chosen_unit(engine_key) or _catalog_row_of_engine(engine_key).unit_default


def request_engines() -> list[EngineRef]:
    """The list one request carries: AIfred's active entries in its order, each with its unit and host.

    The service refuses an empty list; with nothing switched on the request fails there.
    """
    return [EngineRef(entry.engine, entry.unit, entry.host) for entry in entries() if entry.active]


def request_engines_before_browser() -> list[EngineRef]:
    """The list of a reading into a browser window: AIfred's active entries ABOVE the browser entry
    (all of them if the browser entry is not switched on). The browser's own voice runs on the
    user's device, the service cannot speak with it, and an entry below it is never reached: the
    browser always works. Empty = the browser is the topmost active entry.

    Raises:
        TTSSuiteUnreachable: the service does not answer.
    """
    refs: list[EngineRef] = []
    for entry in entries():
        if not entry.active:
            continue
        if entry.engine == TTS_BROWSER_ENGINE_KEY:
            break
        refs.append(EngineRef(entry.engine, entry.unit, entry.host))
    return refs


def _active_stored_engines() -> list[str]:
    """Engine keys of the stored entries that are switched on, in order. Settings
    only: the answer does not depend on the service."""
    return [str(raw["engine"]) for raw in stored_list() if raw["enabled"]]


def browser_entry_unit() -> str | None:
    """Unit the browser entry reads in (the user's choice, else the default of the browser engine);
    ``None`` if the browser entry is not switched on in AIfred's list. Settings only, because it
    must be known when the service is down: then the browser speaks."""
    if TTS_BROWSER_ENGINE_KEY not in _active_stored_engines():
        return None
    return chosen_unit(TTS_BROWSER_ENGINE_KEY) or TTS_BROWSER_DEFAULT_UNIT


def service_entries_before_browser() -> bool:
    """Is any entry switched on above the browser entry (or, without a browser entry, at all)? Settings
    only: with none, a reading into a browser window needs no service."""
    active = _active_stored_engines()
    return bool(active) and active[0] != TTS_BROWSER_ENGINE_KEY


def ref_label(ref: EngineRef) -> str:
    """``engine`` on this machine, else ``engine@host``: how the service names an entry in segments and trace."""
    return ref.engine if ref.host is None else f"{ref.engine}@{ref.host}"


def single_engine_ref(engine_key: str, unit: str) -> EngineRef:
    """The entry a task speaks with first (audiobook: see :func:`narration_refs` for the whole list).

    ``engine_key`` set: that engine, where AIfred's list has it (active entry, its host), else on
    this machine. Empty: the topmost active entry of the list. The browser entry cannot be
    chosen (the service never speaks with it).

    Raises:
        ValueError: with the reason (nothing active, engine not offered, or the browser).
        TTSSuiteUnreachable: the service does not answer.
    """
    active = [entry for entry in entries() if entry.active and entry.info is not None and not entry.info.browser]
    if not engine_key:
        if not active:
            raise ValueError("no active TTS entry in the list")
        return EngineRef(active[0].engine, unit, active[0].host)
    for entry in active:
        if entry.engine == engine_key:
            return EngineRef(engine_key, unit, entry.host)
    row = next((info for info in catalog() if info.engine == engine_key and info.host is None), None)
    if row is None:
        raise ValueError(f"TTS engine {engine_key!r} is not offered by the TTS service")
    if row.browser:
        raise ValueError(f"TTS engine {engine_key!r} speaks in the browser only")
    return EngineRef(engine_key, unit, None)


def narration_refs(engine_key: str, unit: str) -> list[EngineRef]:
    """The list a long task (audiobook) sends: with ``engine_key`` just that engine (pinned by the
    caller), else every active entry of AIfred's list in order, so the service steps to the next one
    like in any reading. All entries read in ``unit``.

    Raises:
        ValueError, TTSSuiteUnreachable: as :func:`single_engine_ref`.
    """
    first = single_engine_ref(engine_key, unit)
    if engine_key:
        return [first]
    return [EngineRef(entry.engine, unit, entry.host)
            for entry in entries() if entry.active and entry.info is not None and not entry.info.browser]


def _catalog_by_entry() -> dict[str, EngineInfo]:
    """The catalog rows by the name the service uses for an entry in a trace (``engine`` / ``engine@host``)."""
    return {ref_label(EngineRef(info.engine, "", info.host)): info for info in catalog()}


def _step_name(by_entry: Mapping[str, EngineInfo], step: TraceStep, lang: str | None) -> str:
    """Display name of a trace step: the catalog's label; an entry the catalog does not have (switched off in
    the service, removed since) is named by its key."""
    return row_label(by_entry[step.entry], lang) if step.entry in by_entry else step.entry


def browser_note(lang: str | None = None) -> str:
    """Note for a reply the browser's own voice spoke: no service involved, so no catalog either."""
    return f"{t('tts_browser_name', lang=lang)} · {t('tts_location_device', lang=lang)}"


def spoken_note(trace: Sequence[TraceStep], lang: str | None = None) -> str:
    """Note for the chat bubble: who spoke, on a change with the reason, e.g.
    ``XTTS · Aragon -> DashScope Audio 3 · cloud (server outage)``. Empty if nobody has spoken.

    Built from the service's trace: an entry that fails after it has spoken is the reason of the
    change to the entry that speaks next.
    """
    by_entry = _catalog_by_entry()
    spoken: set[str] = set()
    change_reason: str | None = None
    parts: list[str] = []
    for step in trace:
        if step.outcome == "failed" and step.entry in spoken:
            change_reason = change_reason or step.reason
        elif step.outcome == "spoke":
            spoken.add(step.entry)
            part = _step_name(by_entry, step, lang)
            if change_reason is not None:
                part += f" ({t(f'tts_reason_{change_reason}', lang=lang)})"
                change_reason = None
            parts.append(part)
    return " \u2192 ".join(parts)


def first_enabled_key() -> str | None:
    """Engine of the topmost switched-on entry of AIfred's list (any host), from settings.json alone:
    what an editor starts with, which must open while the service does not answer."""
    return next((str(raw["engine"]) for raw in stored_list() if raw["enabled"]), None)


def active_local_keys() -> list[str]:
    """Engines of the switched-on local entries of AIfred's list, in speaking order.

    Read from settings.json alone: the plan of the LLM profile (which ``-tts-<engine>`` profile
    reserves room) must not depend on the service. A calibrated profile exists only for a local
    GPU engine the service can start, so the caller needs nothing else from the catalog."""
    return _unique([str(raw["engine"]) for raw in stored_list() if raw["enabled"] and raw["host"] is None])


# ---- Live state (never cached) --------------------------------------------------


def running_gpu_engine_keys() -> list[str]:
    """Local GPU engines that run right now, in catalog order."""
    return _unique([info.engine for info in _local_gpu_rows(fresh=True) if info.status == "running"])


def running_gpu_engine() -> str:
    """Key of the first local GPU engine that runs right now, ``""`` if none."""
    return next(iter(running_gpu_engine_keys()), "")


# ---- Release: AIfred asks the service for room on the cards ---------------------


def running_gpu_footprint() -> dict[str, dict[str, int]]:
    """VRAM (MiB) of the running local GPU engines that hold any: ``{engine_key: {gpu_uuid: MiB}}``.

    Raises:
        TTSVramUnknown: the service could not measure an engine that runs.
        TTSSuiteUnreachable: the service does not answer.
    """
    footprint: dict[str, dict[str, int]] = {}
    for info in _local_gpu_rows(fresh=True):
        if info.status != "running":
            continue
        if info.vram_mib is None:
            raise TTSVramUnknown(f"the VRAM of {info.engine} could not be measured by the TTS service")
        if info.vram_mib:
            footprint[info.engine] = dict(info.vram_mib)
    return footprint


def release_gpu_engines(
    cards_needed: Collection[str] | None = None, *, reason: str,
) -> list[GpuReleaseResult]:
    """Ask the service to release the local GPU engines that run right now (SSOT of every place that
    needs room on the cards: the GPU guard before a model load, the describer, the TTS plan).

    ``cards_needed`` None = all running local GPU engines; a set of GPU UUIDs = only engines whose
    footprint touches one of them. The service decides per engine; every answer that is not a
    success is logged here, visibly: ``busy`` = a reading holds the container or it is starting, so
    the release was refused (callers neither repeat nor wait: the load behaves as with too little room).
    ``reason`` says what the room is for ("loading 'model'").

    Raises:
        TTSVramUnknown: ``cards_needed`` is given and the service could not measure a running engine.
        TTSSuiteError: the service does not answer or refuses the request (callers log it; there
            is no other way to free the cards).
    """
    engines = [
        info.engine
        for info in _local_gpu_rows(fresh=True)
        if info.status == "running" and _touches(info, cards_needed)
    ]
    return release_engines(engines, reason=reason)


def release_engines(engines: Sequence[str], *, reason: str) -> list[GpuReleaseResult]:
    """Ask the service to release exactly these local GPU engines, without force, and log every
    answer that is not a success (``release_gpu_engines`` and the TTS burn-in share this).

    Raises:
        TTSSuiteError: the service does not answer or refuses the request.
    """
    if not engines:
        return []
    try:
        results = get_tts_client().gpu_release(engines, force=False, reason=reason)
    finally:
        reset_cache()
    for result in results:
        label = ref_label(EngineRef(result.item, "", None))
        if result.outcome == "released":
            log_message(f"🔊 TTS container {label} released for {reason}")
        elif result.outcome == "busy":
            log_message(f"⚠️ TTS in use, release of {label} refused ({reason}): {result.detail}", "warning")
        elif result.outcome != "not_running":
            log_message(
                f"⚠️ TTS container {label} NOT released for {reason} ({result.outcome}): {result.detail}", "warning",
            )
    return results


def _touches(info: EngineInfo, cards: Collection[str] | None) -> bool:
    """Does the engine's footprint touch one of ``cards``? ``None`` = every engine does."""
    if cards is None:
        return True
    if info.vram_mib is None:
        raise TTSVramUnknown(f"the VRAM of {info.engine} could not be measured by the TTS service")
    return bool(set(cards) & set(info.vram_mib))


def escalation_report(summary: SpeakSummary, label: str) -> list[str]:
    """Debug lines of a reading, from the service's own account: which entry spoke, which was skipped
    or failed (with the reason), and a change of voice (``summary.switch``). ``label`` names the caller."""
    by_entry = _catalog_by_entry()
    lines = []
    for step in summary.trace:
        name = _step_name(by_entry, step, "en")
        if step.outcome == "spoke":
            lines.append(f"🔊 [{label}] TTS escalation: {name} speaks")
        else:
            lines.append(f"🔊 [{label}] TTS escalation: {step.outcome} {name} ({step.reason})")
    if summary.switch:
        lines.append(f"🔊 [{label}] TTS escalation: voice change announced ({summary.switch})")
    return lines
