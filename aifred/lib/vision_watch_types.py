"""Konfiguration und Status einer Watch-Session (Vigilantia).

Reine Datentypen ohne Logik — der Supervisor (``vision_watcher``) und alle
Stellen, die eine Konfiguration bauen (Autostart, Live-Vorschau, Tool-Pfad),
importieren sie von hier. Die Standardwerte stehen in ``config.py`` (SSOT).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .config import (
    VISION_EDGE_AI_EVENT_SOURCE,
    VISION_ONVIF_DEFAULT_PORT,
    VISION_BURST_INTERVAL_SEC,
    VISION_BURST_LINGER_SEC,
    VISION_BURST_REPORT_DEADLINE_SEC,
    VISION_EDGE_AI_POLL_INTERVAL_SEC,
    VISION_EDGE_AI_SETTLE_SEC,
    VISION_MIN_EVENT_INTERVAL_SEC,
    VISION_MOTION_EGO_SHIFT_PX,
    VISION_MOTION_HISTORY_FRAMES,
    VISION_MOTION_MIN_AREA_RATIO,
    VISION_MOTION_VAR_THRESHOLD,
    VISION_MOTION_WARMUP_FRAMES,
    VISION_PREROLL_SEC,
    VISION_WATCH_FPS,
)


@dataclass(frozen=True)
class EdgeAiConfig:
    """Edge-AI-Trigger einer Kamera: die Kamera erkennt Person/Fahrzeug/Tier
    on-device, AIfred reagiert auf ihre Meldung.

    * ``channel``       Kanal der Erkennung und des Weitwinkel-Snaps.
    * ``face_channel``  Dual-Lens: Kanal des Zoom-/Tele-Objektivs für die
      Gesichtserkennung (mehr Pixel im Gesicht). ``None`` = Gesicht auf dem
      Weitwinkel-Frame, kein separater Zoom-Snap.
    * ``poll_interval_sec``  Abstand der Zustandsabfragen.
    * ``settle_sec``    Wartezeit nach der steigenden Flanke, bevor das Frame
      gezogen wird.
    * ``event_source``  ``onvif`` (Abonnement) oder ``poll`` (Abfrage im Takt).
    * ``host`` / ``onvif_port`` / ``cred``  ONVIF-Zugang (nur für ``onvif``);
      die Zugangsdaten selbst liegen im Credential-Broker.
    """

    channel: int = 0
    face_channel: int | None = None
    poll_interval_sec: float = VISION_EDGE_AI_POLL_INTERVAL_SEC
    settle_sec: float = VISION_EDGE_AI_SETTLE_SEC
    event_source: str = VISION_EDGE_AI_EVENT_SOURCE
    host: str = ""
    onvif_port: int = VISION_ONVIF_DEFAULT_PORT
    cred: str = ""


@dataclass
class WatchConfig:
    """Per-Source Konfiguration für eine Watch-Session.

    Werte werden vom Tool-Plugin aus ``settings.json`` (Sektion ``watch``)
    befüllt und beim ``start()`` überschrieben — Caller können pro Aufruf
    abweichen (z.B. höhere fps, Face-Detect on/off).
    """

    fps: float = VISION_WATCH_FPS
    motion_min_area_ratio: float = VISION_MOTION_MIN_AREA_RATIO
    motion_history_frames: int = VISION_MOTION_HISTORY_FRAMES
    motion_var_threshold: float = VISION_MOTION_VAR_THRESHOLD
    motion_warmup_frames: int = VISION_MOTION_WARMUP_FRAMES
    # Ego-Motion-Gate (PTZ-Schwenk): Mindest-Bildverschiebung zwischen
    # zwei Frames (px auf dem 160-px-Analysebild der Phasenkorrelation),
    # ab der die KAMERA als bewegt gilt → kein Trigger, Modell-Reset.
    motion_ego_shift_px: float = VISION_MOTION_EGO_SHIFT_PX
    min_event_interval_sec: float = VISION_MIN_EVENT_INTERVAL_SEC
    save_event_frames: bool = True
    run_face_detect_on_motion: bool = True
    # YOLO-Körper-Detektor pro Motion-Event, PARALLEL zur Gesichtserkennung
    # (kein Gate untereinander): findet er einen Körper, wird ein
    # ``person``-Event geschrieben (+ ggf. Alert). Die Gesichtserkennung läuft
    # unabhängig davon — sonst bliebe ein formatfüllendes Gesicht (Nahaufnahme
    # ohne ganzen Körper) unerkannt.
    run_person_detect_on_motion: bool = False
    # True (Default, statische Kameras): die Detektions-Pipeline läuft nur,
    # wenn der Motion-Detektor Bewegung meldet. False (PTZ-/Auto-Tracking-
    # Kameras wie die Reolink TrackMix): jedes Frame wird gesampelt
    # (weiterhin durch ``min_event_interval_sec`` gedrosselt) — bei
    # schwenkender Kamera sind Motion-Detektor und Zonenmaske sinnlos.
    motion_gated: bool = True
    # ── Dauer-VLM (Live-Vorschau-Teleprompter) ───────────────────────
    # Wenn True, ruft der Watcher die VLM auf JEDEM frischen Frame auf
    # (begrenzt durch ``vlm_cooldown_sec``) und publiziert die Beschreibung
    # live auf den Event-Bus — kein Eintrag im VisionStore. Das Modell kommt
    # einheitlich aus ``plugins/tools/vision/settings.json``.
    run_vlm_continuous: bool = False
    vlm_cooldown_sec: float = 10.0
    # True: die Gesichtserkennung läuft auf JEDEM Frame (gedrosselt durch
    # ``min_event_interval_sec``), nicht nur bei Bewegung. Sinnvoll für
    # Schreibtisch-Setups, wo eine Person ruhig vor der Cam sitzt.
    face_recognition_continuous: bool = False
    # ── Edge-AI-Trigger ──────────────────────────────────────────────
    # Gesetzt: die Kamera erkennt selbst und löst die Pipeline aus; der
    # MOG2-Bewegungsdetektor läuft dann nicht. ``None``: MOG2 triggert.
    edge_ai: EdgeAiConfig | None = None
    # ── Face-Hunt-Burst (nur Edge-AI-Pfad) ───────────────────────────
    # Nach einem Personen-Trigger zieht der Burst im Takt von
    # ``burst_interval_sec`` frische Dual-Snaps und schickt JEDES Bild durch
    # die Gesichtserkennung — der erste Snap ist systematisch der schlechteste
    # Moment (PTZ schwenkt/zoomt noch). Der Burst läuft UNBEGRENZT, solange
    # die Kamera die Person weiter meldet, plus ``burst_linger_sec`` Nachlauf
    # nach dem letzten aktiven Flag. Jeder Tick, der etwas zeigt (Gesicht,
    # oder YOLO-Person ohne Gesicht), wird sofort als Ereignis im Burst-
    # Cluster gespeichert — die "Film"-Serie eines Vorbeigangs. Gemeldet wird
    # als BILANZ (vision_alerts.BurstReport): eine Meldung mit dem besten
    # Bild und allen erkannten Namen beim Burst-Ende, spätestens aber nach
    # ``burst_report_deadline_sec``; danach Follow-ups nur bei echten
    # Neuigkeiten (neuer Name, Band-Upgrade, mehr Personen).
    # burst_interval_sec <= 0 schaltet den Burst ab.
    burst_interval_sec: float = VISION_BURST_INTERVAL_SEC
    burst_linger_sec: float = VISION_BURST_LINGER_SEC
    burst_report_deadline_sec: float = VISION_BURST_REPORT_DEADLINE_SEC
    # Vorlauf (nur Edge-AI-Pfad): Ringpuffer der letzten ``preroll_sec``
    # Sekunden des RTSP-Stroms (nur speichern, nichts auswerten). Beim
    # Trigger werden diese Bilder als Vorlauf des Ereignisses abgelegt —
    # das Subjekt ist oft schon weg, wenn der erste Snap kommt.
    # preroll_sec <= 0 schaltet den Vorlauf ab.
    preroll_sec: float = VISION_PREROLL_SEC


@dataclass
class WatchStatus:
    """Snapshot über eine laufende oder beendete Watch-Session."""

    source_id: str
    running: bool
    started_at: datetime
    fps: float
    frames_seen: int
    motion_events: int
    face_events: int
    last_event_at: datetime | None
