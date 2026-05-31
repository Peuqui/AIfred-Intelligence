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
import socket
import uuid
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import quote

import cv2

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
        url = self._build_url()
        cap = await asyncio.to_thread(_make_capture, url)
        try:
            if not await asyncio.to_thread(cap.isOpened):
                raise RuntimeError(f"Cannot open RTSP stream {self.source_id}")
            for _ in range(_WARMUP_FRAMES):
                await asyncio.to_thread(cap.read)
            frame_idx = 0
            while True:
                jpeg_bytes, w, h = await asyncio.to_thread(_read_encode, cap)
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
                await asyncio.sleep(interval)
        finally:
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
