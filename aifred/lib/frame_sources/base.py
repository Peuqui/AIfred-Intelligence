"""FrameSource protocol — abstrakte Schnittstelle für Bild-/Video-Quellen.

Jede konkrete Quelle (USB-Webcam via V4L2, IP-Cam via RTSP, Bildschirm-
Capture, eingehende Telegram-Anhänge, lokale Bild-/Videodatei, …) erfüllt
dieses Protocol. Konsumenten (Tools, Filter, VLM-Analyzer) sehen nur das
Protocol — der konkrete Source-Typ ist austauschbar.

Das Datenmodell ist bewusst **Frame**, nicht „Kamera". Ein Video ist eine
zeitliche Sequenz von Frames mit gleicher ``metadata['sequence_id']``, ein
Einzelbild ist ein einzelner Frame. Damit ist die obere Pipeline (Motion-
Filter, Face-Detect, VLM-Analyzer) eingabe-agnostisch — sie sieht nur
Frames, egal ob die aus einer Live-Cam, einer Datei oder einer Nachricht
stammen.

Implementierungen leben in ``aifred/lib/frame_sources/<name>_source.py``
und registrieren sich beim Import ihres Moduls per ``register()`` aus
``aifred.lib.frame_sources``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncIterator, Protocol, runtime_checkable


@dataclass(frozen=True)
class Frame:
    """Universelles Frame-Datenmodell.

    ``image_bytes`` ist immer encoded (JPEG/PNG) — Roh-Frames werden vom
    Adapter encoded, bevor sie auf den Bus gehen. Das hält den Bus klein
    und kompatibel mit allen Konsumenten (Storage, VLM, Browser-Preview).

    Konventionen für ``metadata``:

    * ``kind``: ``"rgb"`` | ``"ir"`` | ``"depth"``
    * ``sequence_id``: gleicher Wert über mehrere Frames = zeitliche
      Sequenz (Video, Burst). Fehlt = Einzelbild.
    * ``frame_idx``: 0-basierter Index in der Sequenz.
    * ``prompt_context``: per-source Briefing-Text für VLM-Calls.
    * channel-spezifische Felder erlaubt (``fov``, ``position``, …).
    """

    source_id: str           # "cam/webcam0", "screen/desktop", "tg/<msg-id>"
    timestamp: datetime
    image_bytes: bytes
    format: str = "jpeg"     # "jpeg" | "png"
    width: int = 0
    height: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceInfo:
    """Statische + dynamische Info zu einer Source — Output von ``info()``.

    Eine Source kann ``available=False`` zurückliefern wenn sie zwar
    registriert ist, das Gerät aber gerade nicht erreichbar ist (Webcam
    nicht eingestöpselt, IP-Cam offline, …). Konsumenten behandeln das
    ohne Exception — die Source bleibt im Listing sichtbar mit
    ``available=False``.
    """

    source_id: str
    display_name: str
    kind: str                # "webcam" | "rtsp" | "screen" | "file" | "incoming"
    width: int               # 0 wenn nicht verfügbar
    height: int              # 0 wenn nicht verfügbar
    fps: float | None        # None wenn nicht streamable
    available: bool
    prompt_context: str = "" # für VLM-Calls: "Eingangsbereich, Briefkasten links"
    position: str = ""       # Standort-Beschreibung: "Haustür NW-Seite"
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class FrameSource(Protocol):
    """Protocol für jede Bild-/Video-Quelle.

    Implementierungen MÜSSEN diese Attribute als Instanz- oder Klassen-
    Variablen setzen (nicht nur als Annotations), damit ``runtime_checkable``
    sie über ``isinstance(obj, FrameSource)`` validiert.
    """

    source_id: str
    display_name: str
    kind: str

    def is_available(self) -> bool:
        """True wenn die Source gerade Frames liefern kann.

        Darf I/O machen (z.B. ``cv2.VideoCapture.isOpened()`` testen, RTSP-
        Ping). Sollte schnell sein (< 100 ms) — wird beim Listing aller
        Sources aufgerufen.
        """
        ...

    async def snapshot(self, *, width: int = 0, height: int = 0) -> Frame:
        """Liefert einen einzelnen Frame (on-demand, sofort).

        Bei Live-Quellen: aktuelles Bild ohne Stream-Aufbau. Bei File-
        Quellen: das ganze Bild bzw. der erste Frame. Bei nicht verfügbarer
        Source: ``RuntimeError`` — Konsument fängt das.

        Optional ``width`` / ``height``: gewünschte Ziel-Auflösung. ``0/0``
        (Default) heißt „was die Quelle natürlich liefert". Hardware-
        Quellen versuchen das einzustellen, die effektive Auflösung steht
        im zurückgegebenen ``Frame``.
        """
        ...

    def stream(self, fps: float = 1.0, *, width: int = 0, height: int = 0) -> AsyncIterator[Frame]:
        """Kontinuierlicher Frame-Stream mit Ziel-Frequenz.

        Frames teilen sich eine ``metadata['sequence_id']`` (UUID-String,
        konstant über den ganzen Stream) → Consumer können sie als
        zeitliche Sequenz behandeln. ``frame_idx`` zählt 0, 1, 2, ….

        ``fps`` ist Best-Effort — bei Hardware-Limit kann es weniger sein.
        Implementierungen die nicht streamen können (z.B. ``file_source``
        auf Einzelbild), liefern einen einzigen Frame und beenden den
        Iterator.
        """
        ...

    def info(self) -> SourceInfo:
        """Statische + dynamische Info. Wird beim Listing aufgerufen."""
        ...
