"""AudioOutputChannel protocol — abstrakte Schnittstelle für Audio-Senken.

Jede konkrete Senke (lokales mpv, Browser-Tab, FreeEcho.2, …) erfüllt
dieses Protocol. Der Audio-Player wählt zur Aufrufzeit per Registry-Lookup
den passenden Channel anhand der ``target_id``-Präfix-Konvention:

  ``local``                → LocalChannel
  ``browser:<session_id>`` → BrowserChannel
  ``freeecho2:<room>``     → FreeEcho2Channel (FreeEcho.2-Speaker)

Das Protocol dehnt sich bewusst nicht auf FreeEcho.2-Lifecycle
(``_standby`` / ``_activate``) aus — das ist Mikrofon-Mute am Speaker und
gehört in den FreeEcho.2-Channel-Plugin selbst, nicht in die Audio-Senken-
Abstraktion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..audio_sources import ResolvedSource
    from ..plugin_base import PluginContext


@dataclass(frozen=True)
class AudioFormat:
    """Format-Anforderung eines Output-Channels.

    ``None`` für Felder die der Channel egal sind. mpv default akzeptiert
    z.B. alles → leeres ``AudioFormat()``. Der FreeEcho.2 verlangt ein konkretes
    Format → ``AudioFormat(48000, 1, "s16le")``.
    """

    sample_rate: int | None = None
    channels: int | None = None
    sample_format: str | None = None


@dataclass(frozen=True)
class TargetInfo:
    """Beschreibt ein konkretes, gerade verfügbares Output-Target."""

    id: str        # "local", "browser:abc123", "freeecho2:wohnzimmer"
    label: str     # User-facing Name in audio_targets()
    ready: bool    # True = Hardware/Connection erreichbar


@runtime_checkable
class AudioOutputChannel(Protocol):
    """Eine Senke für Audio-Streams.

    Implementierungen leben in ``aifred/lib/audio_channels/<name>.py`` und
    werden beim Import von ``audio_channels`` automatisch im Registry
    angemeldet (siehe ``audio_channels/__init__.py``).
    """

    name: str                       # "local", "browser", "freeecho2"
    required_format: AudioFormat    # was die Senke an Eingabe erwartet

    def can_handle(self, target_id: str) -> bool:
        """True wenn dieser Channel die ``target_id`` bedienen kann.

        Konvention: Präfix-Match auf ``self.name``. ``"local"`` matcht exakt,
        ``"browser:..."`` und ``"freeecho2:..."`` matchen via Präfix.
        """
        ...

    def list_targets(self, ctx: "PluginContext") -> list[TargetInfo]:
        """Live-Discovery konkreter Targets.

        Wird von ``audio_targets()``-Tool aufgerufen — Result fließt in die
        UI/LLM-Antwort. Darf I/O machen (Browser-Sessions auflisten,
        FreeEcho.2-WebSocket-Connections checken etc.) sollte aber schnell sein.
        """
        ...

    async def play(
        self,
        src: "ResolvedSource",
        target_id: str,
        start_pos_sec: float | None,
        ctx: "PluginContext",
    ) -> dict[str, Any]:
        """Spielt ``src`` am gewünschten Target ab.

        ``start_pos_sec=None`` heißt „natürlicher Start" (0 für Files,
        irrelevant für Streams). Die Channel-Implementierung macht alle
        nötigen Format-Konversionen + State-Updates intern.

        Rückgabe: dict mit mindestens ``{"success": bool, "target": str}``
        plus channel-spezifische Felder (audio_url für Browser, uri für
        local, …). Der Audio-Player serialisiert das in das LLM-Tool-Result.
        """
        ...

    async def pause(self, target_id: str, ctx: "PluginContext | None" = None) -> bool:
        """Pausiere den Stream am Target. False wenn nichts läuft.

        ``ctx`` wird vom BrowserChannel benötigt um den Reflex-State zu
        manipulieren (HTML5-Player wird über State-Push gesteuert).
        Channels die ctx nicht brauchen (Local, FreeEcho.2) ignorieren ihn.
        """
        ...

    async def resume(self, target_id: str, ctx: "PluginContext | None" = None) -> bool:
        """Setze einen pausierten Stream am Target fort. False wenn nichts pausiert."""
        ...

    async def stop(self, target_id: str, ctx: "PluginContext | None" = None) -> bool:
        """Stoppe den Stream am Target. Position wird gespeichert.

        ``stop()`` ist immer sicher — auch wenn nichts läuft, gibt es
        einfach False zurück und macht nichts. Niemals raisen.
        """
        ...

    async def seek(
        self,
        target_id: str,
        position_sec: float,
        relative: bool = False,
        ctx: "PluginContext | None" = None,
    ) -> bool:
        """Springe zur Position. ``relative=True`` heißt skip ±N Sekunden."""
        ...

    async def set_speed(
        self, target_id: str, factor: float, ctx: "PluginContext | None" = None
    ) -> bool:
        """Playback-Speed 0.25–4.0×. Channel kann das ignorieren wenn er
        keine Server-seitige Speed-Kontrolle hat."""
        ...

    async def play_queue(
        self,
        items: list[dict[str, str]],
        target_id: str,
        ctx: "PluginContext",
        audio_type: str = "music",
        shuffle: bool = False,
    ) -> dict[str, Any]:
        """Sequentielles Playback einer Item-Liste.

        ``items``: ``[{"state_key": ..., "uri": ...}, ...]`` in gewünschter
        Reihenfolge. Mit ``shuffle=True`` wird die Liste channel-seitig
        durchgemischt. Returns ``{"success": bool, ...}``-Dict.

        Channel kann das mit "not implemented" returnen wenn Sequential-
        Playback nicht unterstützt wird (Local-Channel z.B.).
        """
        ...

    async def status(self, target_id: str, ctx: "PluginContext | None" = None) -> dict[str, Any]:
        """Aktueller Wiedergabe-Status am Target.

        Schema: ``{"running": bool, "playing": bool, "paused": bool,
        "state_key": str, "position_sec": float, "duration_sec": float | None,
        ...}``. Channel-spezifische Felder erlaubt.
        """
        ...

    # ── Optional: App-Level-Flow-Control (push-Sinks) ────────
    #
    # Pull-basierte Sinks (Browser-HTML5) und Direkt-OS-Sinks (Local/mpv→
    # ALSA) brauchen keine App-Level-Flow-Control — der Receiver pullt
    # selbst bzw. das Betriebssystem hält die Sound-Karte synchron. Push-
    # basierte Sinks (FreeEcho.2 via WS, ggf. zukünftige BT-Speaker) müssen
    # signalisieren wenn ihr Buffer voll ist, sonst läuft der Server
    # ihnen davon.
    #
    # Channels die das nicht brauchen, lassen ``supports_flow_control``
    # auf False und ``notify_flow`` als No-Op — der WS-Receive-Loop
    # kann dann generisch ``channel.notify_flow(...)`` rufen ohne
    # hasattr-Check, und no-flow-control-Channels ignorieren das.

    def supports_flow_control(self) -> bool:
        """True wenn der Channel App-Level-Flow-Control implementiert."""
        return False

    def notify_flow(self, target_id: str, state: str) -> None:
        """Signal vom Sink: ``state`` = "pause" | "resume".

        Default: No-Op. Push-Sinks überschreiben dies und steuern damit
        ihre interne Pump-Logik (z.B. Asyncio-Event clear/set).
        """
        return None

    def get_stream_start_offset(self, target_id: str) -> float | None:
        """Track-Position, bei der der aktuelle Stream startete.

        Default: None (kein Stream-Konzept). Streaming-Sinks (FreeEcho2)
        überschreiben dies — der WS-Layer rechnet damit ``consumed_ms``
        auf eine absolute Track-Position um.
        """
        return None
