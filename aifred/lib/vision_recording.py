"""Aufnahme auf Zuruf: eine Bilderfolge einer Kamera, in Echtzeit zu einem Film zusammengesetzt.

Werkzeug ``vision_record``: Dauer und Bildabstand gibt der Aufrufer vor, die Obergrenzen stehen in
``config.py`` (``VISION_RECORD_*``). Jedes Bild wird beim Aufnehmen mit der Dokumentationszeile
(Name, Ort, Zeit) versehen und sofort auf die Platte gelegt, nicht im Arbeitsspeicher gesammelt:
bei 0,2 s Abstand über zwei Minuten sind es 600 Bilder. Der Film läuft ohne Beschleunigung: seine
Bildrate ist die gemessene (Bilder je Sekunde zwischen erstem und letztem Bild), nicht die
gewünschte; braucht die Kamera länger als der Abstand, wäre ein Film mit der gewünschten Rate zu schnell.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import VISION_RECORD_MAX_DURATION_S, VISION_RECORD_MIN_INTERVAL_S
from .frame_hub import get_default_hub
from .frame_sources import Frame, FrameSource
from .user_error import Invalid
from .vision_film import encode_film
from .vision_snap import snap_frames
from .vision_utils import (
    RECORDINGS_DIR,
    annotate_frame,
    filename_timestamp,
    film_source_dir,
    resolve_source_alias,
    resolve_source_resolution,
    slugify_for_filename,
    source_overlay_label,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Recording:
    path: Path
    n_frames: int
    duration_s: float       # span from the first to the last frame, as it plays
    fps: float              # as measured


def check_request(duration_s: float, interval_s: float) -> None:
    """Wirft :class:`Invalid`, wenn Dauer oder Bildabstand außerhalb der Grenzen aus ``config.py`` liegen."""
    if not 0 < duration_s <= VISION_RECORD_MAX_DURATION_S:
        raise Invalid(f"duration_s must be above 0 and at most {VISION_RECORD_MAX_DURATION_S:g} seconds")
    if interval_s < VISION_RECORD_MIN_INTERVAL_S:
        raise Invalid(f"interval_s must be at least {VISION_RECORD_MIN_INTERVAL_S:g} seconds")


async def _grab(source: FrameSource, width: int, height: int) -> Frame:
    """Ein frisches Bild: bevorzugt die Snap-API der Kamera (volle Linsen-Auflösung), sonst der geteilte Strom
    des Frame-Hubs (nicht ``source.snapshot()``: ein laufender Watcher hält die Quelle für den ganzen Strom)."""
    snapped = await snap_frames(source.source_id, 1)
    if snapped:
        return snapped[0]
    return await get_default_hub().snapshot(source, width=width, height=height)


def _stamp_and_store(frame: Frame, label: str, folder: Path, index: int) -> Path:
    """Das Bild mit der Dokumentationszeile versehen und als nummerierte Datei ablegen (image2-Folge)."""
    path = folder / f"{index:06d}.jpg"
    path.write_bytes(annotate_frame(frame.image_bytes, label, timestamp=frame.timestamp))
    return path


async def record_film(source: FrameSource, duration_s: float, interval_s: float) -> Recording:
    """Nimmt ``duration_s`` Sekunden lang alle ``interval_s`` Sekunden ein Bild auf und setzt sie zu einem MP4
    zusammen. Wirft :class:`Invalid` bei Werten außerhalb der Grenzen, ``RuntimeError``/``TimeoutError``,
    wenn die Kamera kein Bild liefert, und ``FilmEncodeError``, wenn ffmpeg scheitert."""
    check_request(duration_s, interval_s)
    source_id = source.source_id
    width, height = resolve_source_resolution(source_id)
    label = source_overlay_label(source_id)
    started = time.monotonic()
    stored: list[asyncio.Task[Path]] = []
    stamps: list[datetime] = []
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        try:
            index = 0
            while index == 0 or index * interval_s <= duration_s:
                await asyncio.sleep(max(0.0, started + index * interval_s - time.monotonic()))
                frame = await _grab(source, width, height)
                stamps.append(frame.timestamp)
                # Stamping and writing off the loop and next to the next grab: the interval holds.
                stored.append(asyncio.create_task(asyncio.to_thread(_stamp_and_store, frame, label, folder, index)))
                index += 1
            paths = [str(path) for path in await asyncio.gather(*stored)]
        except BaseException:
            # A camera error or a cancelled call: no write may run on into the folder that goes away now.
            for task in stored:
                task.cancel()
            await asyncio.gather(*stored, return_exceptions=True)
            raise
        span = (stamps[-1] - stamps[0]).total_seconds()
        if len(paths) < 2 or span <= 0:
            raise RuntimeError("the camera delivered fewer than two pictures")
        fps = (len(paths) - 1) / span
        alias = slugify_for_filename(resolve_source_alias(source_id, fallback="cam"))
        out = (
            film_source_dir(RECORDINGS_DIR, source_id) / stamps[0].strftime("%Y-%m-%d")
            / f"{alias}_{filename_timestamp(stamps[0])}_{span:.0f}s.mp4"
        )
        await encode_film(paths, out, fps)
    logger.info("recording %s: %d frames, %.1f s at %.2f fps → %s", source_id, len(paths), span, fps, out)
    return Recording(path=out, n_frames=len(paths), duration_s=span, fps=fps)
