"""Burst-Film: ein kurzes Video je Linse aus den Bildern eines Vorkommnisses.

Der Face-Hunt-Burst speichert jeden Tick, der etwas zeigt, als Ereignis im
Burst-Cluster (Weitwinkelbild + Zoombild). Am Burst-Ende (``vision_burst_media``) werden diese Bilder in
Zeitfolge — samt Vorlauf vor dem Auslösen — zu je einem MP4 pro Linse
zusammengesetzt. Wenige Bilder, deshalb langsam (``VISION_FILM_FPS``) und ohne
Längenlimit. Die Dateien liegen unter ``data/vigilantia/films/<quelle>/<datum>/``
(wird wie die Motion-Frames nach der Aufbewahrungsfrist aufgeräumt) und werden
über ``/_upload/vigilantia/…`` ausgeliefert.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from .config import (
    VISION_FILM_CRF,
    VISION_FILM_FPS,
    VISION_FILM_KEYFRAME_SEC,
)
from .i18n import t
from .vision_utils import FILMS_DIR, film_source_dir

logger = logging.getLogger(__name__)

# Linsen in Anzeigereihenfolge (Teil des Dateinamens); ihre Beschriftung steht in i18n (vision_film_lens_<Linse>).
LENSES = ("wide", "zoom")


class FilmEncodeError(Exception):
    """ffmpeg could not encode the film (its exit code and the end of its error output)."""


def film_filename(cluster_id: str, start: datetime, lens: str) -> str:
    """Dateiname eines Burst-Films: ``<Vorkommnis>_<HHMMSS des ersten Bildes>_<Linse>.mp4``. Die Uhrzeit
    macht ihn je Burst eindeutig, auch wenn ein Vorkommnis mehrere Bursts hat."""
    return f"{cluster_id}_{start.strftime('%H%M%S')}_{lens}.mp4"


def film_lens(path: str) -> str:
    """Die Linse eines Burst-Films („wide"/„zoom"), das letzte Stück des Dateinamens."""
    return Path(path).stem.rsplit("_", 1)[-1]


def film_label(path: str) -> str:
    """Beschriftung eines Film-Dateipfads in der Sprache der Oberfläche („Weitwinkel"/„Zoom"), aus dem
    Dateinamen; ein Film ohne Linse im Namen heißt wie seine Datei."""
    lens = film_lens(path)
    return t(f"vision_film_lens_{lens}") if lens in LENSES else Path(path).stem


def _lens_frames(events: list[dict[str, Any]]) -> tuple[str, str, dict[str, list[tuple[datetime, str]]]]:
    """Bilder je Linse in Zeitfolge als ``(Zeitpunkt, Pfad)`` (ohne Dubletten, nur existierende Dateien).
    Returnt ``(source_id, Datum des ersten Bildes, {Linse: [Bilder]})``."""
    events = sorted(events, key=lambda event: (str(event["timestamp"]), int(event["id"])))
    frames: dict[str, list[tuple[datetime, str]]] = {lens: [] for lens in LENSES}
    for event in events:
        taken = datetime.fromisoformat(str(event["timestamp"]))
        classification = dict(event.get("classification") or {})
        for lens, path in (
            ("wide", str(event.get("frame_path") or "")),
            ("zoom", str(classification.get("zoom_frame_path") or "")),
        ):
            if path and all(path != known for _, known in frames[lens]) and Path(path).exists():
                frames[lens].append((taken, path))
    if not events:
        return "", "", frames
    first = datetime.fromisoformat(str(events[0]["timestamp"]))
    return str(events[0]["source_id"]), first.strftime("%Y-%m-%d"), frames


def _canvas_size(paths: list[str]) -> tuple[int, int]:
    """Leinwand eines Films: die Auflösung seines größten Bildes (nach Pixelzahl), nichts wird verkleinert.
    x264 mit yuv420p verlangt gerade Maße, ein ungerades wird um einen Pixel aufgerundet."""
    sizes = []
    for path in paths:
        with Image.open(path) as picture:
            sizes.append(picture.size)
    width, height = max(sizes, key=lambda size: size[0] * size[1])
    return width + width % 2, height + height % 2


async def encode_film(paths: list[str], out: Path, fps: float = VISION_FILM_FPS) -> None:
    """Bilder zu einem MP4 (H.264, Leinwand = größtes Bild, kleinere mit Rändern bei anderer Proportion),
    ``fps`` Bilder je Sekunde, Schlüsselbild alle ``VISION_FILM_KEYFRAME_SEC`` Sekunden. Die Bilder werden
    als nummerierte Symlinks bereitgestellt (image2-Folge): genau ein Filmbild je Quelle."""
    out.parent.mkdir(parents=True, exist_ok=True)
    width, height = await asyncio.to_thread(_canvas_size, paths)
    canvas = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
    )
    keyframe_interval = max(1, round(fps * VISION_FILM_KEYFRAME_SEC))
    with tempfile.TemporaryDirectory() as tmp:
        for index, path in enumerate(paths):
            Path(tmp, f"{index:06d}.jpg").symlink_to(path)
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-loglevel", "error",
            "-framerate", str(fps), "-i", str(Path(tmp, "%06d.jpg")),
            "-vf", canvas,
            "-c:v", "libx264", "-crf", str(VISION_FILM_CRF),
            # Feste Schlüsselbilder (keine Szenenschnitt-Erkennung), damit der Film an ihnen teilbar bleibt.
            "-g", str(keyframe_interval), "-keyint_min", str(keyframe_interval), "-sc_threshold", "0",
            "-movflags", "+faststart",
            str(out),
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise FilmEncodeError(f"ffmpeg ({process.returncode}): {stderr.decode(errors='replace')[-300:]}")


async def make_cluster_films(cluster_id: str, events: list[dict[str, Any]]) -> list[Path]:
    """Je Linse mit mindestens einem Bild ein Film aus genau diesen ``events`` (Ereignisse mit Bildpfaden,
    wie ``VisionStore.list_cluster_frame_events`` sie liefert; ``cluster_id`` benennt nur die Datei).
    Returnt die Dateipfade in Anzeigereihenfolge (Weitwinkel, Zoom); leer, wenn keine Linse ein Bild hat.
    Wirft :class:`FilmEncodeError` bei einem ffmpeg-Fehler."""
    source_id, day, frames = await asyncio.to_thread(_lens_frames, events)
    films: list[Path] = []
    for lens, lens_frames in frames.items():
        if not lens_frames:
            continue
        out = film_source_dir(FILMS_DIR, source_id) / day / film_filename(cluster_id, lens_frames[0][0], lens)
        await encode_film([path for _, path in lens_frames], out)
        logger.info("burst film %s: %d frames → %s", lens, len(lens_frames), out)
        films.append(out)
    return films


async def film_frames(film: Path, count: int) -> list[bytes]:
    """Bis zu ``count`` JPEG-Bilder, gleichmäßig über die Länge des Films verteilt (für das Vision-Modell,
    das Einzelbilder liest). Wirft :class:`FilmEncodeError`, wenn ffprobe oder ffmpeg scheitern."""
    probe = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(film),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await probe.communicate()
    if probe.returncode != 0:
        raise FilmEncodeError(f"ffprobe ({probe.returncode}): {stderr.decode(errors='replace')[-300:]}")
    duration = float(stdout.decode().strip())
    with tempfile.TemporaryDirectory() as tmp:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(film),
            "-vf", f"fps={count / duration:.6f}", "-frames:v", str(count), "-q:v", "2",
            str(Path(tmp, "%04d.jpg")),
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise FilmEncodeError(f"ffmpeg ({process.returncode}): {stderr.decode(errors='replace')[-300:]}")
        return [path.read_bytes() for path in sorted(Path(tmp).glob("*.jpg"))]
