"""RTSP-/IP-Kamera-Source via OpenCV (FFMPEG-Backend).

Anders als USB-Webcams lassen sich Netzwerk-/WLAN-Kameras nicht auto-
scannen — sie werden über ihre RTSP-Parameter konfiguriert. ``discover()``
liest die Kameraliste aus der vision-Plugin-``settings.json`` (Schlüssel
``rtsp_cameras``) und registriert pro Eintrag eine ``RTSPSource``.

Credentials laufen ausschließlich über den ``CredentialBroker`` — kein
Passwort/User landet je in der settings.json. Die settings.json hält nur
nicht-geheime Verbindungsdaten plus eine ``cred``-ID; User/Passwort liest
der Broker aus den Umgebungsvariablen (``.env``).

Format in ``plugins/tools/vision/settings.json``::

    "rtsp_cameras": [
      {"name": "Eingang", "host": "192.168.1.50", "port": 554,
       "path": "Preview_01_sub", "cred": "trackmix"},
      {"name": "Garage",  "host": "192.168.1.51", "path": "h264"}
    ]

* ``name``  Anzeigename (auch Basis für die ``source_id``)
* ``host``  IP/Hostname der Kamera
* ``port``  RTSP-Port (optional, Default 554)
* ``path``  Stream-Pfad ohne führenden Slash (z.B. ``Preview_01_sub``)
* ``cred``  Credential-ID (optional). User/Passwort kommen vom Broker als
  Service ``rtsp_<cred>`` → Umgebungsvariablen ``RTSP_<CRED>_USER`` und
  ``RTSP_<CRED>_PASSWORD`` in der ``.env``. Ohne ``cred`` wird ohne
  Authentifizierung verbunden (offene Kamera).

Die fertige URL inkl. Credentials wird erst beim Verbindungsaufbau lokal
zusammengebaut und **nie** gespeichert, geloggt oder nach außen gegeben —
Konsumenten und das LLM sehen nur den Anzeigenamen. Status: erste
Implementierung, real noch ungetestet (keine RTSP-Kamera zur Hand).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import quote

# FFMPEG-Robustheit für RTSP — MUSS vor dem ersten cv2.VideoCapture gesetzt
# sein (das FFMPEG-Backend liest die env-var beim Open):
#   * rtsp_transport;tcp   — TCP statt UDP: kein Paketverlust → drastisch
#     weniger H264-Korruption (die UDP-bedingten „missing picture / no frame /
#     error while decoding MB"-Fehler).
#   * fflags;discardcorrupt — korrupte Pakete verwerfen statt sie dem
#     H264-Decoder zu füttern. Genau dieser Decode-an-kaputten-Daten-Pfad
#     hat am 2026-06-23 einen opencv/ffmpeg-Segfault ausgelöst, der den
#     ganzen granian-Worker mitriss. setdefault: eine bewusst gesetzte
#     System-/User-Variable hat Vorrang.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|fflags;discardcorrupt",
)
# FFmpeg loggt Verbindungs-/Protokollfehler nach stderr INKLUSIVE der
# kompletten RTSP-URL — also mit user:password (landet im journal).
# AV_LOG_QUIET (-8) unterbindet das; unsere eigenen Fehlerpfade loggen
# bewusst nur die source_id. setdefault: per Umgebung übersteuerbar
# (z.B. fürs Debuggen von Kamera-Verbindungen).
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")

import cv2  # noqa: E402  — nach dem env-Set, damit FFMPEG die Optionen sieht

from ..credential_broker import broker
from . import register, unregister_kind
from .base import Frame, SourceInfo

logger = logging.getLogger(__name__)

# vision-Plugin-settings.json relativ zum Paket:
# aifred/lib/frame_sources/ -> parents[2] == aifred/ -> plugins/tools/vision/
_VISION_SETTINGS = (
    Path(__file__).resolve().parents[2] / "plugins/tools/vision/settings.json"
)

_JPEG_QUALITY = 90
# Erste Frames nach Stream-Open verwerfen — RTSP braucht einen Moment bis
# ein vollständiger Keyframe da ist.
_WARMUP_FRAMES = 2
# FFMPEG-Timeouts (ms): RTSP-Handshake / Reads sollen nicht ewig blockieren.
_OPEN_TIMEOUT_MS = 5000
_READ_TIMEOUT_MS = 5000
# Schnelle TCP-Erreichbarkeitsprüfung für is_available() — kein voller
# RTSP-Handshake, damit das Source-Listing flott bleibt.
_REACH_TIMEOUT_S = 0.4
_DEFAULT_PORT = 554
# Reconnect-Backoff (s): bei Read-/Open-Fehler wartet stream() zwischen den
# Wiederverbindungsversuchen, exponentiell bis zu diesem Maximum — damit ein
# kurzer RTSP-Stall nicht stundenlang einfriert, aber eine tote Kamera nicht
# pausenlos gehämmert wird.
_RECONNECT_MAX_S = 30.0
# Fallback-Grab-Periode, wenn die Kamera keine brauchbare CAP_PROP_FPS
# meldet (0 oder unplausibel hoch). 15 fps ist ein üblicher RTSP-Sub-
# Stream-Wert; die Periode muss nur ungefähr stimmen, da grab() billig ist
# und nur den Treiber-Puffer leerhalten soll, nicht exakt takten.
_DEFAULT_GRAB_FPS = 15.0


def _slugify(name: str) -> str:
    keep = "".join(c if c.isalnum() else "_" for c in name.strip().lower())
    return keep.strip("_") or "cam"


def _load_rtsp_configs() -> list[dict[str, str | int]]:
    """Liest die ``rtsp_cameras``-Liste aus der vision-settings.json.

    Leere Liste, wenn Datei/Key fehlt oder unbrauchbar — dann gibt es
    schlicht keine RTSP-Quellen (Default). Einträge ohne ``name`` oder
    ``host`` werden übersprungen. Credentials stehen hier bewusst NICHT —
    die kommen über den Broker.
    """
    try:
        data = json.loads(_VISION_SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.debug("rtsp: vision settings not readable (%s)", e)
        return []
    cams = data.get("rtsp_cameras")
    if not isinstance(cams, list):
        return []
    result: list[dict[str, str | int]] = []
    for c in cams:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name", "")).strip()
        host = str(c.get("host", "")).strip()
        if not name or not host:
            continue
        try:
            port = int(c.get("port", _DEFAULT_PORT))
        except (TypeError, ValueError):
            port = _DEFAULT_PORT
        result.append({
            "name": name,
            "host": host,
            "port": port,
            "path": str(c.get("path", "")).strip().lstrip("/"),
            "cred": str(c.get("cred", "")).strip(),
        })
    return result


def find_camera_config(source_id: str) -> dict[str, Any] | None:
    """Roh-Eintrag aus ``rtsp_cameras`` zu einer ``source_id`` finden.

    Die ``source_id`` einer RTSP-Quelle ist ``cam/rtsp_<slug(name)>`` — hier
    wird über den slugifizierten Namen zurückgemappt. Liefert das komplette
    (ungefilterte) Dict des Eintrags, inkl. Zusatzfeldern wie ``profile``,
    ``api_port`` oder ``onvif_port``. ``None`` wenn nichts passt (z.B. bei
    V4L2-Quellen). Credentials sind hier NICHT enthalten — nur die
    ``cred``-ID."""
    try:
        data = json.loads(_VISION_SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    cams = data.get("rtsp_cameras")
    if not isinstance(cams, list):
        return None
    for c in cams:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name", "")).strip()
        if name and f"cam/rtsp_{_slugify(name)}" == source_id:
            return c
    return None


def _make_capture(url: str) -> cv2.VideoCapture:
    """Öffne eine RTSP-Capture über das FFMPEG-Backend mit Timeouts +
    kleinem Puffer (näher an Echtzeit). Property-Sets sind best-effort —
    nicht jedes Backend ehrt sie."""
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    try:
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, float(_OPEN_TIMEOUT_MS))
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, float(_READ_TIMEOUT_MS))
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1.0)
    except Exception:  # noqa: BLE001
        pass
    return cap


def _read_encode(cap: cv2.VideoCapture) -> tuple[bytes, int, int]:
    """Read one frame, encode to JPEG. Returns (jpeg_bytes, width, height)."""
    ok, raw = cap.read()
    if not ok or raw is None:
        raise RuntimeError("Failed to read frame from RTSP stream")
    h, w = raw.shape[:2]
    ok_enc, buf = cv2.imencode(".jpg", raw, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
    if not ok_enc:
        raise RuntimeError("JPEG encode failed")
    return bytes(buf), w, h


def _grab_period_for(cap: cv2.VideoCapture) -> float:
    """Native Frame-Periode der Kamera (Sekunden), aus CAP_PROP_FPS.
    Fallback ``_DEFAULT_GRAB_FPS`` wenn der Treiber 0 oder einen
    unplausiblen Wert meldet (RTSP/FFMPEG liest das aus der SDP-Session,
    nicht immer verlässlich)."""
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0 or fps > 120:
        fps = _DEFAULT_GRAB_FPS
    return 1.0 / fps


async def _drain_sleep(cap: "_CapGuard", interval: float, grab_period: float) -> None:
    """Warte ``interval`` Sekunden, aber leere dabei laufend den
    Decoder-Puffer via ``cap.grab()`` (billig — kein Decode), damit der
    nächste ``cap.read()`` einen frischen Frame liefert statt eines, der
    während der Wartezeit im Treiber-FIFO aufgelaufen ist.

    Portiert vom analogen ``_drain_sleep`` in ``v4l2_source.py``
    (2026-05-25) — dort behoben, hier beim Bau der RTSP-Quelle fünf Tage
    später übersehen. ``CAP_PROP_BUFFERSIZE=1`` (in ``_make_capture``) ist
    beim FFMPEG/RTSP-Backend nur best-effort und wird nicht zuverlässig
    honoriert; ohne aktives Draining läuft der Puffer bei gedrosseltem
    fps (z.B. 4 fps gegen einen 15-fps-nativen Sub-Stream) über Zeit
    IMMER WEITER auf — das "Live"-Bild im Zonen-Editor hinkt zunehmend
    hinterher statt aktuell zu sein (User-Report 2026-07-09). Kein
    Eviction-Handling nötig (anders als V4L2): RTSP erlaubt parallele
    Reader, der Hub-Reader-Task wird bei Unsubscribe schlicht cancelt.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + interval
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return
        if remaining > grab_period:
            await asyncio.to_thread(cap.grab)
            await asyncio.sleep(grab_period)
        else:
            await asyncio.sleep(remaining)
            return


class _CapGuard:
    """Serialisiert ALLE Zugriffe auf einen ``cv2.VideoCapture`` über einen
    Lock. Grund: ``asyncio.to_thread``-Tasks sind NICHT abbrechbar — wird
    der Stream-Generator gecancelt (Tab zu, Reload, App-Neustart), während
    ein ``grab()`` noch im Thread-Pool läuft, kollidiert das
    ``finally``-``release()`` mit ihm → Use-after-free in libavcodec
    (deterministische Worker-Segfaults 2026-07-10, ip …2c87f0, drei
    identische Crashes). Der Lock lässt ``release()`` WARTEN statt racen.
    """

    def __init__(self, cap: cv2.VideoCapture) -> None:
        self._cap = cap
        self._lock = threading.Lock()

    def grab(self) -> bool:
        with self._lock:
            return bool(self._cap.grab())

    def read(self) -> Any:
        with self._lock:
            return self._cap.read()

    def read_encode(self) -> tuple[bytes, int, int]:
        with self._lock:
            return _read_encode(self._cap)

    def is_opened(self) -> bool:
        with self._lock:
            return bool(self._cap.isOpened())

    def grab_period(self) -> float:
        with self._lock:
            return _grab_period_for(self._cap)

    def release(self) -> None:
        with self._lock:
            self._cap.release()


async def _drain_backlog(
    cap: "_CapGuard", grab_period: float, max_grabs: int = 120,
) -> int:
    """Aufgelaufenen Puffer-Rückstand bis zum Live-Rand verwerfen.

    ``_drain_sleep`` hält den Puffer nur WÄHREND der Wartezeit im
    Kamera-Takt leer — es baut keinen Rückstand ab und die yield-Phase
    (Konsument verarbeitet: YOLO/InsightFace auf CPU, MJPEG-Send) läuft
    komplett ungedraint. Jede Konsument-Verzögerung akkumulierte so
    dauerhaft Latenz (User-Report 2026-07-10: Zonen-Editor viele
    Sekunden hinter live, wird nie besser).

    Erkennung "Puffer leer" über das grab-Timing: ein ``grab()``, der
    deutlich schneller zurückkommt als die native Frame-Periode, kam aus
    dem Puffer; einer, der ~eine Periode braucht, hat auf ein LIVE-Frame
    gewartet → aufgeholt, stopp. ``max_grabs`` begrenzt den Aufwand
    (Schutz gegen Timing-Fehlklassifikation, ~8 s Rückstand bei 15 fps).

    Kostet bei leerem Puffer genau einen verworfenen Live-Frame
    (~1 Frame-Periode Wartezeit) pro Zyklus — bei den gedrosselten
    Consumer-fps (1–4) vernachlässigbar gegen den Latenz-Gewinn.
    """
    loop = asyncio.get_event_loop()
    n = 0
    while n < max_grabs:
        t0 = loop.time()
        ok = await asyncio.to_thread(cap.grab)
        if not ok:
            break
        n += 1
        if loop.time() - t0 >= grab_period * 0.5:
            break  # grab hat auf ein Live-Frame gewartet → Puffer leer
    return n


class RTSPSource:
    """IP-/WLAN-Kamera via cv2 + FFMPEG.

    RTSP erlaubt — anders als V4L2 — mehrere parallele Reader (jede
    ``VideoCapture`` öffnet eine eigene Verbindung), daher kein
    Single-Reader-Eviction wie beim ``V4L2Source``. ``width``/``height``
    werden akzeptiert, aber ignoriert: die Auflösung bestimmt der
    Kamera-Stream selbst.

    Credentials werden nicht auf der Instanz gehalten — die URL wird pro
    Verbindung über ``_build_url()`` aus dem Broker frisch zusammengebaut.
    """

    kind: str = "rtsp"

    def __init__(self, name: str, host: str, port: int, path: str, cred: str) -> None:
        self.display_name = name
        self.source_id = f"cam/rtsp_{_slugify(name)}"
        self._host = host
        self._port = port
        self._path = path
        self._cred = cred  # Credential-ID für den Broker, kein Secret selbst

    # ── Protocol ────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Schnelle TCP-Erreichbarkeitsprüfung auf host:port.

        Kein voller RTSP-Handshake (zu langsam fürs Listing) — nur ob der
        Port erreichbar ist. Echte Frame-Lieferung zeigt sich erst bei
        snapshot()/stream(). Braucht keine Credentials."""
        try:
            with socket.create_connection(
                (self._host, self._port), timeout=_REACH_TIMEOUT_S
            ):
                return True
        except OSError:
            return False

    def info(self) -> SourceInfo:
        return SourceInfo(
            source_id=self.source_id,
            display_name=self.display_name,
            kind=self.kind,
            width=0,
            height=0,
            fps=None,
            available=self.is_available(),
            # host ist nicht geheim; Credentials tauchen hier bewusst NICHT auf.
            extra={"transport": "rtsp", "host": self._host},
        )

    async def snapshot(self, *, width: int = 0, height: int = 0) -> Frame:
        jpeg_bytes, w, h = await asyncio.to_thread(self._capture_single)
        return Frame(
            source_id=self.source_id,
            timestamp=datetime.now(),
            image_bytes=jpeg_bytes,
            format="jpeg",
            width=w,
            height=h,
            metadata={"kind": "rgb"},
        )

    async def stream(
        self, fps: float = 1.0, *, width: int = 0, height: int = 0
    ) -> AsyncIterator[Frame]:
        if fps <= 0:
            raise ValueError(f"stream fps must be > 0, got {fps}")
        interval = 1.0 / fps
        sequence_id = str(uuid.uuid4())
        frame_idx = 0
        cap: _CapGuard | None = None
        grab_period = 1.0 / _DEFAULT_GRAB_FPS
        backoff = 1.0
        try:
            while True:
                # (Neu-)Verbindung aufbauen, falls noch kein offener Cap.
                if cap is None:
                    raw = await asyncio.to_thread(_make_capture, self._build_url())
                    cap = _CapGuard(raw)
                    if not await asyncio.to_thread(cap.is_opened):
                        await asyncio.to_thread(cap.release)
                        cap = None
                        logger.warning(
                            "RTSP open failed for %s — retry in %.0fs",
                            self.source_id, backoff,
                        )
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, _RECONNECT_MAX_S)
                        continue
                    for _ in range(_WARMUP_FRAMES):
                        await asyncio.to_thread(cap.read)
                    grab_period = await asyncio.to_thread(cap.grab_period)
                    backoff = 1.0
                # Frame lesen — bei Read-Fehler/Stall NICHT crashen (das ließ
                # den Hub-Reader sterben → stundenlanges Einfrieren), sondern
                # die Verbindung neu aufbauen (selbst-heilend).
                try:
                    # Rückstand aus der yield-Phase (Konsument-Verarbeitung)
                    # verwerfen, BEVOR gelesen wird — _drain_sleep allein
                    # hält nur Schritt, holt aber nie auf (Latenz-Akkumulation).
                    dropped = await _drain_backlog(cap, grab_period)
                    if dropped > 3:
                        logger.debug(
                            "RTSP %s: dropped %d backlog frame(s) before read",
                            self.source_id, dropped,
                        )
                    jpeg_bytes, w, h = await asyncio.to_thread(cap.read_encode)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "RTSP read failed for %s: %s — reconnecting in %.0fs",
                        self.source_id, e, backoff,
                    )
                    await asyncio.to_thread(cap.release)
                    cap = None
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, _RECONNECT_MAX_S)
                    continue
                yield Frame(
                    source_id=self.source_id,
                    timestamp=datetime.now(),
                    image_bytes=jpeg_bytes,
                    format="jpeg",
                    width=w,
                    height=h,
                    metadata={
                        "kind": "rgb",
                        "sequence_id": sequence_id,
                        "frame_idx": frame_idx,
                    },
                )
                frame_idx += 1
                # Drain-sleep statt plain sleep — siehe _drain_sleep-
                # Docstring: ohne aktives Draining läuft der FFMPEG/RTSP-
                # Puffer bei gedrosseltem fps über Zeit auf, das "Live"-
                # Bild hinkt zunehmend hinterher statt aktuell zu sein.
                await _drain_sleep(cap, interval, grab_period)
        finally:
            if cap is not None:
                await asyncio.to_thread(cap.release)

    # ── Internals ──────────────────────────────────────────────────

    def _build_url(self) -> str:
        """Baue die vollständige RTSP-URL inkl. Credentials aus dem Broker.

        Wird nur lokal beim Verbindungsaufbau verwendet — die Rückgabe
        enthält das Passwort und darf NICHT geloggt oder gespeichert werden.
        Ohne ``cred`` (oder ohne hinterlegte Werte) wird ohne Auth verbunden.
        """
        auth = ""
        if self._cred:
            user = broker.get(f"rtsp_{self._cred}", "user")
            password = broker.get(f"rtsp_{self._cred}", "password")
            if user or password:
                auth = f"{quote(user, safe='')}:{quote(password, safe='')}@"
        return f"rtsp://{auth}{self._host}:{self._port}/{self._path}"

    def _capture_single(self) -> tuple[bytes, int, int]:
        """Sync: open → warmup → read → encode → close. Über
        ``asyncio.to_thread()`` aufgerufen, damit es den Event-Loop nicht
        blockiert."""
        cap = _make_capture(self._build_url())
        try:
            if not cap.isOpened():
                raise RuntimeError(f"Cannot open RTSP stream {self.source_id}")
            for _ in range(_WARMUP_FRAMES):
                cap.read()
            return _read_encode(cap)
        finally:
            cap.release()


def discover() -> None:
    """(Re-)registriere alle konfigurierten RTSP-Kameras.

    Idempotent: entfernt erst alle ``kind="rtsp"``-Sources, registriert
    dann die aktuelle Liste aus der vision-settings.json. Safe bei
    Modul-Import und via ``rescan()``."""
    unregister_kind("rtsp")
    for cfg in _load_rtsp_configs():
        source = RTSPSource(
            name=str(cfg["name"]),
            host=str(cfg["host"]),
            port=int(cfg["port"]),
            path=str(cfg["path"]),
            cred=str(cfg["cred"]),
        )
        register(source)
        logger.info("Registered RTSP source: %s (%s)", source.source_id, cfg["name"])


# Initial discovery beim Modul-Import. No-op wenn keine rtsp_cameras konfiguriert.
discover()
