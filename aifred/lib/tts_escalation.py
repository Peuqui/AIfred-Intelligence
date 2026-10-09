"""TTS-Eskalationsliste — welche Engine auf welchem Rechner spricht.

Zwei Listen in ``settings.json``:

- ``tts_hosts``: andere Rechner, die TTS-Container mit derselben API
  betreiben (Name, Adresse, optional abweichende Ports je Engine). Dieser
  Rechner ist implizit.
- ``tts_escalation``: geordnete Einträge aus Engine und Host. Der erste
  passende Eintrag von oben spricht.

Der Code unterscheidet nur nach der Art eines Eintrags (lokal, remote,
Cloud), nie nach dem Namen eines Rechners.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .tts_engines import TTSEngine, get_engine


@dataclass(frozen=True)
class TTSHost:
    """Ein anderer Rechner mit TTS-Containern."""

    name: str
    address: str
    ports: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class EscalationEntry:
    """Ein Eintrag der Liste: Engine, gebunden an einen Host (``None`` = dieser Rechner)."""

    engine: TTSEngine
    host: TTSHost | None
    enabled: bool

    @property
    def label(self) -> str:
        """Kurzname für Debug-Zeilen und Chat-Vermerke, z. B. ``xtts@Aragon``."""
        return f"{self.engine.key}@{self.host.name if self.host else 'local'}"


def _parse_hosts(raw_hosts: list[dict[str, Any]]) -> dict[str, TTSHost]:
    hosts: dict[str, TTSHost] = {}
    for raw in raw_hosts:
        name = str(raw["name"])
        if name in hosts:
            raise ValueError(f"tts_hosts: duplicate host name {name!r}")
        ports = {str(key): int(port) for key, port in (raw.get("ports") or {}).items()}
        for engine_key in ports:
            if get_engine(engine_key) is None:
                raise ValueError(f"tts_hosts[{name}]: port for unknown TTS engine {engine_key!r}")
        hosts[name] = TTSHost(name=name, address=str(raw["address"]), ports=ports)
    return hosts


def _bind_entry(raw: dict[str, Any], hosts: dict[str, TTSHost]) -> EscalationEntry:
    engine_key = str(raw["engine"])
    engine = get_engine(engine_key)
    if engine is None:
        raise ValueError(f"tts_escalation: unknown TTS engine {engine_key!r}")
    host_name = raw.get("host")
    if host_name is None:
        return EscalationEntry(engine=engine, host=None, enabled=bool(raw["enabled"]))
    host = hosts.get(str(host_name))
    if host is None:
        raise ValueError(f"tts_escalation: entry {engine_key!r} names unknown host {host_name!r}")
    remote = engine.at_host(host.address, host.ports.get(engine_key))
    return EscalationEntry(engine=remote, host=host, enabled=bool(raw["enabled"]))


def escalation_entries(settings: dict[str, Any] | None = None) -> list[EscalationEntry]:
    """Die Eskalationsliste aus den Einstellungen, in ihrer Reihenfolge (auch
    deaktivierte Einträge). Fehlerhafte Einträge lösen ``ValueError`` aus —
    eine kaputte Liste wird nicht still übergangen."""
    from .settings import persisted_settings

    source = settings if settings is not None else persisted_settings()
    hosts = _parse_hosts(source["tts_hosts"])
    return [_bind_entry(raw, hosts) for raw in source["tts_escalation"]]
