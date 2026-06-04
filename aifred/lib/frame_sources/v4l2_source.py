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
# Wird nur bei nicht-MJPEG-Cams gebraucht — bei nativem MJPEG-Output
# (FOURCC=JPEG/MJPG) reichen wir den unveränderten Bytestream durch.
_JPEG_QUALITY = 90

# FOURCC-Codes der Cams die nativ schon MJPEG liefern — dann reichen
# wir die Bytes 1:1 durch ans Frontend (kein Decode→Reencode).
_NATIVE_JPEG_FOURCCS = frozenset({"JPEG", "MJPG"})


def _fourcc_str(cap: cv2.VideoCapture) -> str:
    """Lies das aktuelle Pixel-Format als 4-char string, z.B. ``"JPEG"``,
    ``"MJPG"``, ``"YUYV"``."""
    code = int(cap.get(cv2.CAP_PROP_FOURCC))
    return (
        chr(code & 0xFF)
        + chr((code >> 8) & 0xFF)
        + chr((code >> 16) & 0xFF)
        + chr((code >> 24) & 0xFF)
    )


def _sanitize_id_part(raw: str) -> str:
    """Lowercase + collapse anything non-alphanumeric to single underscores.
    Used to build stable, filesystem-/key-safe source-id fragments from USB
    strings (manufacturer, product, serial, port)."""
    out = []
    prev_us = False
    for ch in raw.strip().lower():
        if ch.isalnum():
            out.append(ch)
            prev_us = False
        elif not prev_us:
            out.append("_")
            prev_us = True
    return "".join(out).strip("_")


def _is_usable_serial(serial: str) -> bool:
    """A USB serial is only useful as an identity anchor if it's non-empty
    and not an all-zero/placeholder string. Many cheap cams report ``0`` /
    ``00000000`` (or no serial descriptor at all → empty)."""
    s = serial.strip()
    return bool(s) and set(s) != {"0"}


def _resolve_device_identity(sysfs_entry: Path) -> tuple[str | None, str | None]:
    """Walk the device symlink upwards to the USB *device* node and derive
    a human name + a STABLE identity key for the physical camera.

    Returns ``(display_name, stable_key)``:

    - ``display_name``: ``"<manufacturer> <product>"`` (e.g. "Image+ UGREEN
      Camera 4K"), falling back to the product label alone. UVC reports a
      nice name; gspca legacy cams expose only "USB Camera" but that still
      beats "V4L2 videoN".
    - ``stable_key``: ``"cam/<mfr>_<product>_<serial>"`` when the device
      reports a usable serial (→ identity follows the physical camera across
      ports). When there is NO serial (e.g. the gspca Hercules, which reports
      SerialNumber=0), we fall back to the USB *port path* instead:
      ``"cam/<mfr>_<product>_port_<bus-port>"`` — stable as long as the cam
      stays in the same physical USB port. Two identical serial-less cams are
      thus distinguished by port, never silently merged.

    Both ``None`` if no USB device node is found (non-USB / odd topology) —
    the caller then keeps the index-based id as a last resort.
    """
    try:
        device_real = (sysfs_entry / "device").resolve()
    except OSError:
        return None, None

    def _read(node: Path, name: str) -> str:
        f = node / name
        try:
            return f.read_text(encoding="utf-8").strip() if f.exists() else ""
        except OSError:
            return ""

    # device_real points to the USB *interface* (e.g. ``1-2:1.0``); walk up
    # to the USB *device* node (e.g. ``1-2``), identified by the presence of
    # ``idVendor`` (every USB device node has it).
    candidate: Path = device_real
    for _ in range(6):
        if (candidate / "idVendor").exists():
            manufacturer = _read(candidate, "manufacturer")
            product = _read(candidate, "product")
            serial = _read(candidate, "serial")
            vid = _read(candidate, "idVendor")
            pid = _read(candidate, "idProduct")
            port = candidate.name  # e.g. "1-2" or "1-1.1" — physical USB port

            if manufacturer and product:
                name: str | None = f"{manufacturer} {product}"
            else:
                name = product or None

            device_part = _sanitize_id_part(f"{manufacturer} {product}") or \
                _sanitize_id_part(f"{vid} {pid}") or "usb_cam"
            if _is_usable_serial(serial):
                unique = _sanitize_id_part(serial)
            else:
                unique = f"port_{_sanitize_id_part(port)}"
            return name, f"cam/{device_part}_{unique}"
        candidate = candidate.parent
    return None, None


def _enumerate_devices() -> list[tuple[int, str, str]]:
    """Find V4L2 video devices in /sys/class/video4linux/. Returns list of
    ``(index, human_name, stable_key)``.

    ``stable_key`` anchors the camera identity to the physical USB *device*
    (serial, or USB port for serial-less cams) — NOT the ``/dev/videoN``
    index. So swapping cameras on the same port no longer makes a new cam
    inherit the old one's config. Falls back to the index-based id only when
    no USB device node is resolvable.

    Name resolution priority:
    1. USB product/manufacturer from sysfs (works for gspca + UVC)
    2. The v4l2 ``name`` file (driver-reported — fine for UVC, ugly for gspca)
    3. Generic fallback ``V4L2 videoN``
    """
    if not _V4L2_SYSFS.exists():
        return []
    devices: list[tuple[int, str, str]] = []
    for entry in sorted(_V4L2_SYSFS.iterdir()):
        if not entry.name.startswith("video"):
            continue
        try:
            idx = int(entry.name[len("video"):])
        except ValueError:
            continue
        if not (_V4L2_DEV_DIR / entry.name).exists():
            continue
        # USB device identity takes precedence — gives a real product name
        # ("UGREEN Camera 4K") + a serial/port-anchored stable id rather than
        # "gspca main driver" / index-based ids for legacy cams.
        usb_name, stable_key = _resolve_device_identity(entry)
        if usb_name:
            name = usb_name
        else:
            name_file = entry / "name"
            name = (
                name_file.read_text(encoding="utf-8").strip()
                if name_file.exists()
                else f"V4L2 video{idx}"
            )
        # Last-resort id when there's no resolvable USB device node.
        if not stable_key:
            stable_key = f"cam/v4l2_{idx}"
        devices.append((idx, name, stable_key))
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

    def __init__(self, index: int, display_name: str, source_id: str) -> None:
        self.index = index
        # Stable, device-anchored id (serial/port) from _enumerate_devices —
        # NOT derived from the /dev index, so config follows the physical cam.
        self.source_id = source_id
        self.display_name = display_name
        self._lock = asyncio.Lock()
        # Lazy-cached list of (width, height) modes the driver actually
        # honoured during probe. ``None`` = not yet detected.
        self._supported_resolutions: list[tuple[int, int]] | None = None
        # Eviction signal for the currently active stream. A new stream()
        # call sets this so the old loop exits its yield/sleep cycle and
        # releases the V4L2 device — needed because the browser's TCP
        # cleanup on src-change can lag behind, and waiting for the old
        # generator's CancelledError to arrive naturally makes
        # resolution/fps switches feel laggy.
        self._stream_stop: asyncio.Event | None = None

    # ── Protocol ────────────────────────────────────────────────────

    def is_available(self) -> bool:
        # V4L2 erlaubt nur einen Reader gleichzeitig. Wenn wir SELBST
        # gerade streamen (z.B. weil der Browser-MJPEG-Endpoint die
        # Cam offen hält), würde ein zweiter cv2-Probe failen und die
        # Source fälschlich als "nicht verfügbar" zurückmelden. Solange
        # wir streamen, ist sie trivialerweise verfügbar.
        if self._stream_stop is not None and not self._stream_stop.is_set():
            return True
        return _can_capture(self.index)

    def detect_resolutions(self) -> list[tuple[int, int]]:
        """Return the list of (width, height) modes the V4L2 driver honours.

        Probes a fixed set of common webcam resolutions; cv2 silently
        clamps to its nearest supported mode when an unsupported size is
        requested, so we compare the reported back-value with the
        requested one. Result is cached on the instance — call once per
        process per source.

        Returns an empty list if the device can't be opened (e.g. cam
        disconnected during the probe).
        """
        if self._supported_resolutions is not None:
            return list(self._supported_resolutions)
        probe_set = [
            (320, 240),
            (640, 480),
            (800, 600),
            (1024, 768),
            (1280, 720),
            (1280, 960),
            (1600, 1200),
            (1920, 1080),
            (2560, 1440),
            (3840, 2160),
        ]
        cap = cv2.VideoCapture(self.index, cv2.CAP_V4L2)
        try:
            if not cap.isOpened():
                self._supported_resolutions = []
                return []
            supported: list[tuple[int, int]] = []
            for w, h in probe_set:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(w))
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(h))
                actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                if (actual_w, actual_h) == (w, h):
                    supported.append((w, h))
            self._supported_resolutions = supported
            return list(supported)
        finally:
            cap.release()

    def info(self) -> SourceInfo:
        # Race-Schutz: Wenn wir gerade streamen, würde cv2.VideoCapture
        # gegen unseren eigenen Stream konkurrieren und failen. In dem
        # Fall verzichten wir auf den Probe und melden 'available' ohne
        # frische width/height — die hat unser Caller eh nur als
        # diagnostische Info, nicht als Pflichtfeld.
        if self._stream_stop is not None and not self._stream_stop.is_set():
            return SourceInfo(
                source_id=self.source_id,
                display_name=self.display_name,
                kind=self.kind,
                width=0,
                height=0,
                fps=None,
                available=True,
                extra={"device_path": f"/dev/video{self.index}"},
            )
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

    async def snapshot(self, *, width: int = 0, height: int = 0) -> Frame:
        """Snapshot mit optionaler Ziel-Auflösung.

        ``width=0, height=0`` (Default): die Cam liefert was sie default-mäßig
        gibt — meist 640×480. Mit konkreten Werten versucht cv2 das per
        ``CAP_PROP_FRAME_WIDTH/HEIGHT`` zu setzen; was die Hardware
        wirklich liefert, steht dann in ``Frame.width / .height``.
        """
        async with self._lock:
            jpeg_bytes, w, h = await asyncio.to_thread(
                self._capture_single, width, height
            )
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
        """Stream frames at ``fps``. If another stream is already running
        on this source, signal it to stop first and wait for it to
        release the device — otherwise the new consumer would deadlock
        on ``self._lock`` waiting for the old browser TCP-cleanup.

        Native MJPEG passthrough: if the cam reports FOURCC=JPEG/MJPG,
        ``CAP_PROP_CONVERT_RGB`` is disabled and ``cap.read()`` returns
        the raw JPEG bytes — they're forwarded 1:1 without the
        decode→reencode round-trip. Saves CPU and avoids a second
        lossy JPEG step.
        """
        if fps <= 0:
            raise ValueError(f"stream fps must be > 0, got {fps}")
        interval = 1.0 / fps
        sequence_id = str(uuid.uuid4())

        # Evict any active stream on this source.
        old_stop = self._stream_stop
        if old_stop is not None:
            old_stop.set()
        # Register ourselves as the new active stream BEFORE taking the
        # lock so concurrent stream() calls see us and trigger the same
        # eviction dance.
        my_stop = asyncio.Event()
        self._stream_stop = my_stop

        async with self._lock:
            # If we got evicted while waiting for the lock (yet another
            # stream() call came in), bail out cleanly. Whoever set
            # _stream_stop is now the new owner.
            if my_stop.is_set():
                return
            cap = await _open_capture_with_retry(self.index)
            try:
                if width > 0 and height > 0:
                    await asyncio.to_thread(
                        cap.set, cv2.CAP_PROP_FRAME_WIDTH, float(width)
                    )
                    await asyncio.to_thread(
                        cap.set, cv2.CAP_PROP_FRAME_HEIGHT, float(height)
                    )
                native_jpeg = await asyncio.to_thread(_configure_native_jpeg, cap)
                # Best-effort: tell the V4L2 driver we only want one
                # frame queued. Not all drivers honour this, but where
                # they do it keeps reads close to real-time. Combined
                # with the grab-drain in the sleep loop below, this
                # ensures long intervals don't return stale frames.
                await asyncio.to_thread(cap.set, cv2.CAP_PROP_BUFFERSIZE, 1.0)
                eff_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                eff_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                logger.info(
                    "v4l2 stream open: %s @ %dx%d, native_jpeg=%s",
                    self.source_id, eff_w, eff_h, native_jpeg,
                )
                for _ in range(_WARMUP_FRAMES):
                    await asyncio.to_thread(cap.read)
                frame_idx = 0
                while not my_stop.is_set():
                    jpeg_bytes, w, h = await asyncio.to_thread(
                        _read_frame, cap, native_jpeg, eff_w, eff_h
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
                    # Drain-sleep: instead of plain asyncio.sleep, we
                    # periodically issue cap.grab() during the wait to
                    # consume frames that the driver buffers behind our
                    # back. Without this, a 2-second interval would
                    # show a frame that's almost 2 seconds old, because
                    # cap.read() returns whatever is at the head of the
                    # driver's FIFO. Wakes up immediately if evicted.
                    if not await _drain_sleep(cap, interval, my_stop):
                        break
            finally:
                await asyncio.to_thread(cap.release)
                if self._stream_stop is my_stop:
                    self._stream_stop = None

    # ── Internals ──────────────────────────────────────────────────

    def _capture_single(self, width: int = 0, height: int = 0) -> tuple[bytes, int, int]:
        """Sync: open → set resolution → warmup → read → encode → close.
        Über ``asyncio.to_thread()`` aufgerufen, damit es den Event-Loop
        nicht blockiert. ``width``/``height``=0 bedeutet "Treiber-Default".
        Nutzt native MJPEG passthrough wenn die Cam das anbietet."""
        cap = cv2.VideoCapture(self.index, cv2.CAP_V4L2)
        try:
            if not cap.isOpened():
                raise RuntimeError(
                    f"Cannot open {self.source_id} (/dev/video{self.index})"
                )
            if width > 0 and height > 0:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(width))
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(height))
            native_jpeg = _configure_native_jpeg(cap)
            eff_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            eff_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            for _ in range(_WARMUP_FRAMES):
                cap.read()
            return _read_frame(cap, native_jpeg, eff_w, eff_h)
        finally:
            cap.release()


async def _open_capture_with_retry(
    index: int, attempts: int = 12, delay: float = 0.1
) -> cv2.VideoCapture:
    """Open a V4L2 device, retrying for up to ``attempts * delay`` seconds.

    The Linux V4L2/USB stack needs a beat to free the device after a
    previous ``release()`` — the file descriptor is gone but the
    kernel's internal capture state can stay busy for 50-200 ms.
    Opening immediately after a release on the same device commonly
    fails with "can't open camera by index" and ``cap.isOpened() ==
    False``. Without the retry, a stream-switch (FPS/resolution change
    in the popup → eviction → new stream() call) would 500 the new
    HTTP response and the browser shows a black frame.

    Defaults give ~1.2 s of total slack, which beats every USB cam
    I've seen in practice.
    """
    for attempt in range(attempts):
        cap = await asyncio.to_thread(cv2.VideoCapture, index, cv2.CAP_V4L2)
        if cap.isOpened():
            return cap
        await asyncio.to_thread(cap.release)
        if attempt < attempts - 1:
            await asyncio.sleep(delay)
    raise RuntimeError(
        f"Cannot open V4L2 device {index} after {attempts} attempts"
    )


async def _drain_sleep(
    cap: cv2.VideoCapture, interval: float, stop_event: asyncio.Event
) -> bool:
    """Wait ``interval`` seconds while continuously draining the driver
    frame buffer so the next ``cap.read()`` returns a fresh frame.

    Without draining, a 2-second interval gives you the frame at the
    head of the driver FIFO — which can be 100-200 ms old at 30 fps,
    not the one captured the instant we read.

    Returns False if the stream was evicted (caller should exit the
    yield loop), True if the full interval elapsed normally.

    ``cap.grab()`` is cheaper than ``cap.read()`` — it captures from
    the device into the next buffer slot but skips decode. We grab
    roughly once per native frame period (~33 ms at 30 fps) so the
    ring stays empty.
    """
    grab_period = 0.033  # ~30 fps native cam rate
    loop = asyncio.get_event_loop()
    deadline = loop.time() + interval
    while not stop_event.is_set():
        remaining = deadline - loop.time()
        if remaining <= 0:
            return True
        if remaining > grab_period:
            await asyncio.to_thread(cap.grab)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=grab_period)
                return False  # evicted mid-sleep
            except asyncio.TimeoutError:
                pass
        else:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=remaining)
                return False
            except asyncio.TimeoutError:
                return True
    return False


def _configure_native_jpeg(cap: cv2.VideoCapture) -> bool:
    """Aktiviere native MJPEG passthrough wenn die Cam das anbietet.

    Setzt ``CAP_PROP_CONVERT_RGB=0``, sodass ``cap.read()`` die rohen
    JPEG-Bytes als 1D-ndarray liefert statt sie zu BGR zu dekodieren.
    Spart einen Decode-Encode-Cycle pro Frame und vermeidet einen
    zweiten lossy JPEG-Step.

    Returns True wenn passthrough aktiv ist, False wenn die Cam
    ein Nicht-JPEG-Format liefert (dann muss der Reader klassisch
    decoden + reencoden).
    """
    fcc = _fourcc_str(cap).strip().upper()
    if fcc not in _NATIVE_JPEG_FOURCCS:
        return False
    # 0.0 disables the implicit decode-to-RGB step in cv2's V4L2 backend.
    ok = cap.set(cv2.CAP_PROP_CONVERT_RGB, 0.0)
    if not ok:
        # Some drivers don't honour the property — fall back gracefully.
        return False
    return True


def _read_frame(
    cap: cv2.VideoCapture, native_jpeg: bool, fallback_w: int, fallback_h: int
) -> tuple[bytes, int, int]:
    """Read + (maybe) encode one frame from an already-opened capture.

    With ``native_jpeg=True`` we trust the driver to deliver pre-encoded
    MJPEG bytes and forward them as-is — width/height come from
    ``CAP_PROP_FRAME_WIDTH/HEIGHT`` since the 1D byte ndarray doesn't
    carry image dimensions. With ``native_jpeg=False`` (YUYV cams etc.)
    we decode-then-encode like before.
    """
    ok, raw = cap.read()
    if not ok or raw is None:
        raise RuntimeError("Failed to read frame from capture")
    if native_jpeg:
        # raw is a 1D ndarray of uint8 containing the MJPEG bytestream.
        return bytes(raw), fallback_w, fallback_h
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
    seen_keys: set[str] = set()
    for index, name, stable_key in _enumerate_devices():
        try:
            if not _can_capture(index):
                logger.debug(
                    "V4L2 /dev/video%d (%s) not capturable, skipping", index, name
                )
                continue
        except Exception as e:  # noqa: BLE001
            logger.warning("V4L2 probe failed for /dev/video%d: %s", index, e)
            continue
        # Doppelknoten-Merge: ein physisches Gerät meldet oft mehrere
        # /dev/videoN-Knoten (Capture + Metadata) unter DEMSELBEN stable_key.
        # Nur den ersten capture-fähigen Knoten pro Gerät registrieren.
        if stable_key in seen_keys:
            logger.debug(
                "V4L2 /dev/video%d is a secondary node of %s — skipping",
                index, stable_key,
            )
            continue
        seen_keys.add(stable_key)
        source = V4L2Source(index=index, display_name=name, source_id=stable_key)
        # Auflösungen JETZT erkennen, solange das Gerät frei ist (vor dem
        # ersten Stream) und auf der Instanz cachen — sonst läuft der lazy
        # Probe der Live-Vorschau WÄHREND des Streamens, scheitert (V4L2
        # erlaubt nur einen Reader) und das Dropdown zeigt nur "Treiber-Default".
        try:
            source.detect_resolutions()
        except Exception as e:  # noqa: BLE001
            logger.debug(
                "resolution probe at discover failed for %s: %s", stable_key, e
            )
        register(source)
        logger.info(
            "Registered V4L2 source: %s (%s, /dev/video%d)",
            source.source_id, name, index,
        )


# Initial discovery beim Modul-Import. No-op wenn keine Cams da sind.
discover()
