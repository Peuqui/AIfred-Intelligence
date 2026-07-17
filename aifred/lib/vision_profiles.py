"""Kamera-Fähigkeitsprofile — SSoT, welche Pipeline-Stufen pro Quelle laufen.

Eine **"dumme" Webcam** liefert nur Rohbilder: AIfred macht alles selbst —
Motion (MOG2), Person-Detektion (YOLO), Gesichtserkennung und VLM.

Eine **"intelligente" Kamera** (z.B. Reolink TrackMix) erkennt Person /
Fahrzeug / Tier on-device und trackt selbst. AIfred konsumiert dann nur ihre
Erkennungs-Events (Edge-AI-Trigger statt MOG2) und ergänzt das, was die
Kamera NICHT kann: Gesichts-*Identität* (InsightFace), VLM-Beschreibung und
Alert/Chronik. Die teure lokale Roh-Erkennung (MOG2/YOLO) läuft für so eine
Quelle gar nicht erst an — der Code bleibt aber erhalten, er wird nur nicht
aktiviert.

Das Profil entscheidet ausschließlich über *Trigger* und *lokale
Roh-Erkennung*. Gesichtserkennung und VLM bleiben von den jeweiligen
Plugin-Settings gesteuert und gelten für BEIDE Profile (die Gesichts-
erkennung ist günstig und kann die Kamera nicht).
"""

from __future__ import annotations

from dataclasses import dataclass

# Trigger-Modi (auch der Wert von ``WatchConfig.trigger_mode``).
TRIGGER_MOTION = "motion"
TRIGGER_EDGE_AI = "edge_ai"

# SSOT für den Default-Mindestabstand zwischen Detection-Events
# (``WatchConfig.min_event_interval_sec``), wenn eine Quelle keinen eigenen
# Wert in ``sources.settings`` gespeichert hat. Alle drei Konsumenten
# (Hintergrund-Autostart, Live-Vorschau-Popup, LLM-Tool ``vision_start_watch``)
# fallen auf denselben Wert zurück statt je einen eigenen Literal zu pflegen.
DEFAULT_MIN_EVENT_INTERVAL_SEC = 1.0


@dataclass(frozen=True)
class VisionProfile:
    """Fähigkeitsprofil einer Kamera-Quelle.

    * ``trigger_mode``         Was löst die Detektions-Pipeline aus —
      ``motion`` (AIfreds MOG2) oder ``edge_ai`` (Erkennungen der Kamera).
    * ``allow_local_detection`` Ob AIfreds eigene Roh-Erkennung (MOG2-Motion
      + YOLO-Person) überhaupt laufen darf. Bei ``edge_ai`` immer ``False`` —
      die Kamera macht das schon.
    """

    name: str
    trigger_mode: str
    allow_local_detection: bool
    description: str


WEBCAM = VisionProfile(
    name="webcam",
    trigger_mode=TRIGGER_MOTION,
    allow_local_detection=True,
    description=(
        "Dumme Webcam: AIfred macht Motion (MOG2), Person (YOLO), "
        "Gesichtserkennung und VLM selbst."
    ),
)

AI_CAMERA = VisionProfile(
    name="ai_camera",
    trigger_mode=TRIGGER_EDGE_AI,
    allow_local_detection=False,
    description=(
        "Intelligente Kamera: Person/Fahrzeug/Tier + Tracking erkennt die "
        "Kamera on-device. AIfred konsumiert nur die Edge-AI-Events und "
        "ergänzt Gesichts-Identität, VLM-Beschreibung und Alerts."
    ),
)

# SSoT-Registry. Erweiterbar um weitere Kamera-Typen.
VISION_PROFILES: dict[str, VisionProfile] = {p.name: p for p in (WEBCAM, AI_CAMERA)}

DEFAULT_PROFILE = WEBCAM.name


def resolve_profile(name: str | None) -> VisionProfile:
    """Profil per Name auflösen — unbekannt/leer fällt auf ``webcam``
    (das Verhalten von heute). Kein hartes Scheitern, damit ein Tippfehler
    in der Config nicht die ganze Quelle lahmlegt."""
    return VISION_PROFILES.get((name or "").strip(), VISION_PROFILES[DEFAULT_PROFILE])
