"""V4L2-Webcam-Source via OpenCV.

Scannt beim Modul-Import (und bei jedem ``rescan()``) das System nach
V4L2-Capture-Devices unter ``/dev/video*``, registriert für jedes
brauchbare Gerät eine ``V4L2Source``-Instanz im Registry.

Pixel-Format ist nicht erzwungen — OpenCV verhandelt mit V4L2 ein
geeignetes Format. Frames werden intern als JPEG encoded, bevor sie
auf den Bus gehen.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

import cv2

from . import register, unregister_kind
from .base import Frame, SourceInfo

logger = logging.getLogger(__name__)

_V4L2_DEV_DIR = Path("/dev")
_V4L2_SYSFS = Path("/sys/class/video4linux")

# Drei Frames nach Capture-Open verwerfen — Auto-Exposure braucht etwas
# Zeit bis das Bild brauchbar ist. Wert empirisch (cv2 + UVC-Webcams).
_WARMUP_FRAMES = 3

# JPEG-Qualität für encodeJPEG. 90 ist guter Default (klein + visuell ok).
_JPEG_QUALITY = 90


def _enumerate_devices() -> list[tuple[int, str]]:
    """Find V4L2 video devices in /sys/class/video4linux/. Returns list of
    ``(index, human_name)``."""
    if not _V4L2_SYSFS.exists():
        return []
    devices: list[tuple[int, str]] = []
    for entry in sorted(_V4L2_SYSFS.iterdir()):
        if not entry.name.startswith("video"):
            continue
        try:
            idx = int(entry.name[len("video"):])
        except ValueError:
            continue
        if not (_V4L2_DEV_DIR / entry.name).exists():
            continue
        name_file = entry / "name"
        name = name_file.read_text(encoding="utf-8").strip() if name_file.exists() else f"V4L2 video{idx}"
        devices.append((idx, name))
    return devices


def _can_capture(index: int) -> bool:
    """Probe a V4L2 device: open + check frame size > 0. Some /dev/videoN
    entries are metadata/output devices that cannot capture."""
    cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
    try:
        if not cap.isOpened():
            return False
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        return w > 0
    except Exception:  # noqa: BLE001
        return False
    finally:
        cap.release()


class V4L2Source:
    """USB-Webcam via cv2 + V4L2-Backend.

    Snapshot- und Stream-Operationen sind durch ``_lock`` serialisiert —
    eine cv2.VideoCapture-Instanz pro Device kann nicht parallel von
    mehreren Coroutines benutzt werden.
    """

    kind: str = "webcam"

    def __init__(self, index: int, display_name: str) -> None:
        self.index = index
        self.source_id = f"cam/v4l2_{index}"
        self.display_name = display_name
        self._lock = asyncio.Lock()

    # ── Protocol ────────────────────────────────────────────────────

    def is_available(self) -> bool:
        return _can_capture(self.index)

    def info(self) -> SourceInfo:
        cap = cv2.VideoCapture(self.index, cv2.CAP_V4L2)
        try:
            if not cap.isOpened():
                return SourceInfo(
                    source_id=self.source_id,
                    display_name=self.display_name,
                    kind=self.kind,
                    width=0,
                    height=0,
                    fps=None,
                    available=False,
                    extra={"device_path": f"/dev/video{self.index}"},
                )
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            raw_fps = cap.get(cv2.CAP_PROP_FPS)
            fps = raw_fps if raw_fps and raw_fps > 0 else None
            return SourceInfo(
                source_id=self.source_id,
                display_name=self.display_name,
                kind=self.kind,
                width=w,
                height=h,
                fps=fps,
                available=True,
                extra={"device_path": f"/dev/video{self.index}"},
            )
        finally:
            cap.release()

    async def snapshot(self) -> Frame:
        async with self._lock:
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

    async def stream(self, fps: float = 1.0) -> AsyncIterator[Frame]:
        if fps <= 0:
            raise ValueError(f"stream fps must be > 0, got {fps}")
        interval = 1.0 / fps
        sequence_id = str(uuid.uuid4())
        async with self._lock:
            # Eine cv2.VideoCapture wird für den ganzen Stream offen gehalten —
            # open/close pro Frame wäre auf USB-Cams 200-500 ms Latenz.
            cap = await asyncio.to_thread(cv2.VideoCapture, self.index, cv2.CAP_V4L2)
            try:
                if not cap.isOpened():
                    raise RuntimeError(
                        f"Cannot open {self.source_id} for streaming"
                    )
                # Warmup
                for _ in range(_WARMUP_FRAMES):
                    await asyncio.to_thread(cap.read)
                frame_idx = 0
                while True:
                    jpeg_bytes, w, h = await asyncio.to_thread(
                        _read_and_encode, cap
                    )
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
        ``asyncio.to_thread()`` aufgerufen, damit es den Event-Loop
        nicht blockiert."""
        cap = cv2.VideoCapture(self.index, cv2.CAP_V4L2)
        try:
            if not cap.isOpened():
                raise RuntimeError(
                    f"Cannot open {self.source_id} (/dev/video{self.index})"
                )
            for _ in range(_WARMUP_FRAMES):
                cap.read()
            return _read_and_encode(cap)
        finally:
            cap.release()


def _read_and_encode(cap: cv2.VideoCapture) -> tuple[bytes, int, int]:
    """Read one frame from an already-opened capture and JPEG-encode it."""
    ok, raw = cap.read()
    if not ok or raw is None:
        raise RuntimeError("Failed to read frame from capture")
    h, w = raw.shape[:2]
    ok_enc, buf = cv2.imencode(".jpg", raw, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
    if not ok_enc:
        raise RuntimeError("JPEG encode failed")
    return bytes(buf), w, h


def discover() -> None:
    """Scan and (re-)register V4L2 capture devices.

    Idempotent: removes all existing ``kind="webcam"`` sources before
    registering the current set. Safe to call at module import and via
    ``rescan()`` at runtime when devices are hot-plugged.
    """
    unregister_kind("webcam")
    for index, name in _enumerate_devices():
        try:
            if not _can_capture(index):
                logger.debug(
                    "V4L2 /dev/video%d (%s) not capturable, skipping", index, name
                )
                continue
        except Exception as e:  # noqa: BLE001
            logger.warning("V4L2 probe failed for /dev/video%d: %s", index, e)
            continue
        source = V4L2Source(index=index, display_name=name)
        register(source)
        logger.info("Registered V4L2 source: %s (%s)", source.source_id, name)


# Initial discovery beim Modul-Import. No-op wenn keine Cams da sind.
discover()
