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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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
