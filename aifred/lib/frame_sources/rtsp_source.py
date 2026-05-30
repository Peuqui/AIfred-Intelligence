"""RTSP-/IP-Kamera-Source via OpenCV (FFMPEG-Backend).

Anders als USB-Webcams lassen sich Netzwerk-/WLAN-Kameras nicht auto-
scannen — sie werden über ihre RTSP-URL konfiguriert. ``discover()`` liest
die Kameraliste aus der vision-Plugin-``settings.json`` (Schlüssel
``rtsp_cameras``) und registriert pro Eintrag eine ``RTSPSource``.

Format in ``plugins/tools/vision/settings.json``::

    "rtsp_cameras": [
      {"name": "Eingang", "url": "rtsp://user:pass@192.168.1.50:554/stream1"},
      {"name": "Garage",  "url": "rtsp://192.168.1.51:554/h264"}
    ]

Die rohe URL (inkl. evtl. Credentials) bleibt **intern** — Konsumenten und
das LLM sehen nur den Anzeigenamen, nie die URL (gleiche Konvention wie
beim Audio-Player). Status: erste Implementierung, real noch ungetestet
(keine RTSP-Kamera zur Hand).
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
from urllib.parse import urlparse

import cv2

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


def _slugify(name: str) -> str:
    keep = "".join(c if c.isalnum() else "_" for c in name.strip().lower())
    return keep.strip("_") or "cam"


def _load_rtsp_configs() -> list[dict[str, str]]:
    """Liest die ``rtsp_cameras``-Liste aus der vision-settings.json.

    Leere Liste, wenn Datei/Key fehlt oder unbrauchbar — dann gibt es
    schlicht keine RTSP-Quellen (Default).
    """
    try:
        data = json.loads(_VISION_SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.debug("rtsp: vision settings not readable (%s)", e)
        return []
    cams = data.get("rtsp_cameras")
    if not isinstance(cams, list):
        return []
    result: list[dict[str, str]] = []
    for c in cams:
        if isinstance(c, dict) and c.get("url") and c.get("name"):
            result.append({"name": str(c["name"]), "url": str(c["url"])})
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
    """

    kind: str = "rtsp"

    def __init__(self, name: str, url: str) -> None:
        self.display_name = name
        self.source_id = f"cam/rtsp_{_slugify(name)}"
        self._url = url  # intern — nie nach außen geben

    # ── Protocol ────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Schnelle TCP-Erreichbarkeitsprüfung auf host:port der RTSP-URL.

        Kein voller RTSP-Handshake (zu langsam fürs Listing) — nur ob der
        Port erreichbar ist. Echte Frame-Lieferung zeigt sich erst bei
        snapshot()/stream()."""
        parsed = urlparse(self._url)
        host = parsed.hostname
        port = parsed.port or 554
        if not host:
            return False
        try:
            with socket.create_connection((host, port), timeout=_REACH_TIMEOUT_S):
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
            extra={"transport": "rtsp"},
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
        cap = await asyncio.to_thread(_make_capture, self._url)
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

    def _capture_single(self) -> tuple[bytes, int, int]:
        """Sync: open → warmup → read → encode → close. Über
        ``asyncio.to_thread()`` aufgerufen, damit es den Event-Loop nicht
        blockiert."""
        cap = _make_capture(self._url)
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
        source = RTSPSource(name=cfg["name"], url=cfg["url"])
        register(source)
        logger.info("Registered RTSP source: %s (%s)", source.source_id, cfg["name"])


# Initial discovery beim Modul-Import. No-op wenn keine rtsp_cameras konfiguriert.
discover()
