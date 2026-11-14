"""The order in which describers are tried, edited in the Vigilantia settings like the TTS list.

An entry is a source with a switch:

* ``"<machine>/<model>"`` — a model of another machine (lib/remote_describers),
* :data:`LOCAL` — this machine's describer: the VLM of the Vigilantia settings, via its ``-visiond`` profile
  (or the optional Ollama side channel for camera images),
* :data:`MAIN` — the loaded model, when it sees itself.

The list shown and saved is always matched with what is there now: the stored order and switches; sources
that are new come switched off at the end; a model that a machine which answers no longer has drops out; the
entries of a machine that does not answer stay as they are. Without a stored list the order is this machine,
then the main model. Two rules are no entries of the list (lib/vision_routing): a main model that sees takes
chat images itself, and only an explicit request may evict the chat model.

For every interface (the vision page of Vue): the rows as the settings show them, a
move or switch of an entry, and the burn-in of a describer of another machine, one at a time.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .user_error import Conflict, Invalid

if TYPE_CHECKING:
    from .remote_describers import HostOverview

LOCAL = "local"
MAIN = "main"
SETTINGS_KEY = "describers"  # in the Vigilantia settings: [{"source": ..., "enabled": ...}, ...]


@dataclass(frozen=True)
class Entry:
    source: str  # "<machine>/<model>", LOCAL or MAIN
    enabled: bool

    def as_json(self) -> dict[str, Any]:
        return {"source": self.source, "enabled": self.enabled}


DEFAULT: tuple[Entry, ...] = (Entry(LOCAL, True), Entry(MAIN, True))


def _settings() -> dict[str, Any]:
    """The Vigilantia settings (one place, so tests put their own in)."""
    from .vision_settings import load_vision_settings
    return load_vision_settings()


def stored() -> list[Entry]:
    """The saved list, or :data:`DEFAULT` when none is saved. A malformed list is an error."""
    raw = _settings().get(SETTINGS_KEY)
    if raw is None:
        return list(DEFAULT)
    try:
        entries = [Entry(str(item["source"]), bool(item["enabled"])) for item in raw]
    except (KeyError, TypeError) as error:
        raise ValueError(f"Vigilantia settings: '{SETTINGS_KEY}' is malformed: {error!r}") from error
    if len({e.source for e in entries}) != len(entries):
        raise ValueError(f"Vigilantia settings: '{SETTINGS_KEY}' names a source twice")
    return entries


def save(entries: list[Entry]) -> None:
    from .vision_settings import load_vision_settings, save_vision_settings
    settings = load_vision_settings()
    settings[SETTINGS_KEY] = [entry.as_json() for entry in entries]
    save_vision_settings(settings)


def matched(entries: list[Entry], machines: list[HostOverview]) -> list[Entry]:
    """``entries`` matched with what is there now (see the module docstring)."""
    from .remote_describers import split
    answering = {m.host.name: {model.qualified for model in m.models} for m in machines if m.reachable and not m.error}
    kept = []
    for entry in entries:
        remote = split(entry.source) if entry.source not in (LOCAL, MAIN) else None
        if entry.source not in (LOCAL, MAIN) and remote is None:
            continue  # a machine no longer configured
        if remote is not None and remote[0].name in answering and entry.source not in answering[remote[0].name]:
            continue  # the machine answers and no longer has the model
        kept.append(entry)
    present = {entry.source for entry in kept}
    for source in (LOCAL, MAIN, *(s for models in answering.values() for s in sorted(models))):
        if source not in present:
            kept.append(Entry(source, False))
            present.add(source)
    return kept


# ── The rows as the settings show them, a move or switch of an entry ──────────


@dataclass(frozen=True)
class Row:
    index: int
    source: str
    label: str      # model · place
    enabled: bool
    note: str       # an entry of another machine: what holds it back, or what it needs as measured
    warn: bool      # the note says why the entry is skipped
    remote: bool    # a describer of another machine (it can be burnt in)


def rows(lang: str) -> list[Row]:
    """The list as it stands now (:func:`matched`), one row per entry; this machine's entry names the image model
    of the Vigilantia settings. Asks every configured machine (short limit for one that is off)."""
    from .formatting import format_number, format_stamp
    from .i18n import t
    from .remote_describers import overview, split
    from .vigilantia import vigilantia
    from .vlm_vram_cache import get_any, measured_at

    machines = overview()
    by_host = {machine.host.name: machine for machine in machines}
    listed = []
    for index, entry in enumerate(matched(stored(), machines)):
        note, warn, remote = "", False, False
        if entry.source == LOCAL:
            label = f"{vigilantia().model or '—'} · {t('vision_describer_this_machine', lang=lang)}"
        elif entry.source == MAIN:
            label = t("vision_describer_main", lang=lang)
        else:
            remote = True
            host, model_id = split(entry.source) or (None, entry.source)
            machine = by_host.get(host.name) if host else None
            label = f"{model_id} · {host.name if host else '?'}"
            model = next((m for m in machine.models if m.model == model_id), None) if machine else None
            if entry.source in _failed:
                note, warn = t("vision_remote_measure_failed", lang=lang, error=_failed[entry.source]), True
            elif machine is None or not machine.reachable:
                note, warn = t("vision_remote_unreachable", lang=lang, host=host.name if host else "?"), True
            elif machine.error:
                note, warn = t("vision_remote_list_error", lang=lang, host=host.name if host else "?",
                               error=machine.error), True
            elif model is not None and model.context is None:
                note, warn = t("vision_remote_never_loaded", lang=lang), True
            elif model is not None and model.context is not None:
                measured, when = get_any(model.qualified), measured_at(model.qualified)
                ctx = format_number(model.context, 0)
                if measured is None or when is None or measured[1] != model.context:
                    note, warn = t("vision_remote_unmeasured", lang=lang, ctx=ctx), True
                else:
                    note = t("vision_remote_measured", lang=lang, ctx=ctx, mib=format_number(measured[0], 0),
                             when=format_stamp(when.timestamp()))
        listed.append(Row(index, entry.source, label, entry.enabled, note, warn, remote))
    return listed


def _edit(change: Callable[[list[Entry]], None]) -> None:
    """Apply ``change`` to the list as it stands now (matched) and save the result."""
    from .remote_describers import overview

    entries = matched(stored(), overview())
    change(entries)
    save(entries)


def _check_index(entries: list[Entry], index: int) -> None:
    if not 0 <= index < len(entries):
        raise Invalid("list_position_gone", position=index)


def move_entry(index: int, delta: int) -> None:
    """The entry at ``index`` one place up (-1) or down (1); at an end it stays. ValueError for a position the
    list does not have."""
    def change(entries: list[Entry]) -> None:
        _check_index(entries, index)
        target = index + delta
        if 0 <= target < len(entries):
            entries[index], entries[target] = entries[target], entries[index]
    _edit(change)


def enable_entry(index: int, enabled: bool) -> None:
    """Switch the entry at ``index`` on or off. ValueError for a position the list does not have."""
    def change(entries: list[Entry]) -> None:
        _check_index(entries, index)
        entries[index] = Entry(entries[index].source, enabled)
    _edit(change)


# ── The burn-in of a describer of another machine ─────────────────────────────
# One at a time for every interface: it unloads every model on that machine and loads the chosen one there.

class BurnInRunning(Conflict):
    """Another burn-in runs (one at a time)."""


_measuring = ""                  # the source being burnt in ("" = none)
_failed: dict[str, str] = {}     # source → why its last burn-in failed (until its next one)
_run: asyncio.Task[None] | None = None


def measuring() -> str:
    """The source being burnt in now, "" when none is."""
    return _measuring


def start_burn_in(source: str) -> asyncio.Task[None]:
    """Start the burn-in of ``<machine>/<model>`` there (``vlm_stress_prewarm.stress_prewarm_remote``); the task
    ends with it, :func:`measuring` and the rows follow it. :class:`BurnInRunning` while another one runs,
    ValueError for a source of no other machine."""
    global _measuring, _run
    from .remote_describers import split

    if _measuring:
        raise BurnInRunning("burn_in_running", source=_measuring)
    if source in (LOCAL, MAIN) or split(source) is None:
        raise Invalid("not_a_remote_describer", source=source)
    _measuring = source
    _failed.pop(source, None)
    _run = asyncio.create_task(_burn_in(source))
    return _run


async def _burn_in(source: str) -> None:
    global _measuring
    import httpx

    from .gpu_orc_client import GpuOrcError
    from .logging_utils import log_message
    from .remote_describers import RemoteHostError, remote_model
    from .vlm_stress_prewarm import stress_prewarm_remote

    try:
        model = await asyncio.to_thread(remote_model, source)
        log_message(f"🔥 Burn-in of {source} …")
        need = await stress_prewarm_remote(model)
        log_message(f"✅ Burn-in of {source}: {need} MiB")
    except (RemoteHostError, GpuOrcError, httpx.HTTPError) as error:
        log_message(f"⚠️ Burn-in of {source} failed: {error}", "warning")
        _failed[source] = str(error)
    finally:
        _measuring = ""
