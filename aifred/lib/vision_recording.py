"""Aufnahme auf Zuruf: eine Bilderfolge einer Kamera, in Echtzeit zu einem Film zusammengesetzt.

Werkzeug ``vision_capture`` mit ``duration_s``: Dauer und Bildabstand gibt der Aufrufer vor, die Obergrenzen stehen in
``config.py`` (``VISION_RECORD_*``). Jedes Bild läuft beim Aufnehmen durch die Ausgabekette
(Schwärzungs-Maske, Dokumentationszeile mit Name, Ort, Zeit) und wird sofort auf die Platte gelegt,
nicht im Arbeitsspeicher gesammelt:
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
from .formatting import format_number
from .frame_sources import Frame
from .user_error import Invalid
from .vision_capture import grab_frame, output_chain
from .vision_film import encode_film
from .vision_output import OutputChain
from .vision_utils import (
    RECORDINGS_DIR,
    filename_timestamp,
    film_source_dir,
    resolve_source_alias,
    slugify_for_filename,
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
        raise Invalid("record_duration_out_of_range", max_s=format_number(VISION_RECORD_MAX_DURATION_S))
    if interval_s < VISION_RECORD_MIN_INTERVAL_S:
        raise Invalid("record_interval_too_short", min_s=format_number(VISION_RECORD_MIN_INTERVAL_S, 1))


async def _stamp_and_store(chain: OutputChain, frame: Frame, folder: Path, index: int) -> Path:
    """Das Bild durch die Ausgabekette (schwärzen, stempeln) und als nummerierte Datei ablegen (image2-Folge)."""
    path = folder / f"{index:06d}.jpg"
    finalized = await chain.finalize(frame, save=False)
    await asyncio.to_thread(path.write_bytes, finalized.output.image_bytes)
    return path


async def record_film(source_id: str, duration_s: float, interval_s: float) -> Recording:
    """Nimmt ``duration_s`` Sekunden lang alle ``interval_s`` Sekunden ein Bild auf und setzt sie zu einem MP4
    zusammen. Wirft :class:`Invalid` bei Werten außerhalb der Grenzen, :class:`Missing` für eine unbekannte Kamera,
    ``RuntimeError``/``TimeoutError``, wenn die Kamera kein Bild liefert, und ``FilmEncodeError``, wenn ffmpeg scheitert."""
    check_request(duration_s, interval_s)
    chain = output_chain(source_id)
    started = time.monotonic()
    stored: list[asyncio.Task[Path]] = []
    stamps: list[datetime] = []
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        try:
            index = 0
            while index == 0 or index * interval_s <= duration_s:
                await asyncio.sleep(max(0.0, started + index * interval_s - time.monotonic()))
                frame = await grab_frame(source_id)
                stamps.append(frame.timestamp)
                # Blacking out, stamping and writing next to the next grab: the interval holds.
                stored.append(asyncio.create_task(_stamp_and_store(chain, frame, folder, index)))
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
