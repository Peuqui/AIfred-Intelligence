"""Burst-Film: ein kurzes Video je Linse aus den Bildern eines Vorkommnisses.

Der Face-Hunt-Burst speichert jeden Tick, der etwas zeigt, als Ereignis im
Burst-Cluster (Weitwinkelbild + Zoombild). Am Burst-Ende werden diese Bilder in
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

from .config import (
    VISION_FILM_CRF,
    VISION_FILM_FPS,
    VISION_FILM_HEIGHT,
    VISION_FILM_MIN_FRAMES,
    VISION_FILM_WIDTH,
)
from .vision_store import VisionStore
from .vision_utils import VIGILANTIA_DIR

logger = logging.getLogger(__name__)

FILMS_DIR = VIGILANTIA_DIR / "films"
# Linsen in Anzeigereihenfolge; Dateiname und Beschriftung (Anzeige für den User).
LENS_LABELS = {"wide": "Weitwinkel", "zoom": "Zoom"}


def film_label(path: str) -> str:
    """Beschriftung eines Film-Dateipfads („Weitwinkel"/„Zoom"), aus dem Dateinamen."""
    stem = Path(path).stem
    for lens, label in LENS_LABELS.items():
        if stem.endswith(f"_{lens}"):
            return label
    return stem


def _lens_frames(store: VisionStore, cluster_id: str) -> tuple[str, str, dict[str, list[str]]]:
    """Bildpfade je Linse in Zeitfolge (ohne Dubletten, nur existierende Dateien).
    Returnt ``(source_id, Datum des ersten Bildes, {Linse: [Pfade]})``."""
    events = []
    for event_id in store.list_cluster_event_ids(cluster_id):
        event = store.get_event(int(event_id))
        if event:
            events.append(event)
    events.sort(key=lambda event: (str(event["timestamp"]), int(event["id"])))
    frames: dict[str, list[str]] = {lens: [] for lens in LENS_LABELS}
    for event in events:
        classification = dict(event.get("classification") or {})
        for lens, path in (
            ("wide", str(event.get("frame_path") or "")),
            ("zoom", str(classification.get("zoom_frame_path") or "")),
        ):
            if path and path not in frames[lens] and Path(path).exists():
                frames[lens].append(path)
    if not events:
        return "", "", frames
    first = datetime.fromisoformat(str(events[0]["timestamp"]))
    return str(events[0]["source_id"]), first.strftime("%Y-%m-%d"), frames


async def _encode(paths: list[str], out: Path) -> None:
    """Bilder zu einem MP4 (H.264, feste Leinwand, Ränder bei anderer Proportion).
    Die Bilder werden als nummerierte Symlinks bereitgestellt (image2-Folge): genau
    ein Filmbild je Quelle."""
    out.parent.mkdir(parents=True, exist_ok=True)
    width, height = VISION_FILM_WIDTH, VISION_FILM_HEIGHT
    canvas = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
    )
    with tempfile.TemporaryDirectory() as tmp:
        for index, path in enumerate(paths):
            Path(tmp, f"{index:06d}.jpg").symlink_to(path)
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-loglevel", "error",
            "-framerate", str(VISION_FILM_FPS), "-i", str(Path(tmp, "%06d.jpg")),
            "-vf", canvas,
            "-c:v", "libx264", "-crf", str(VISION_FILM_CRF), "-movflags", "+faststart",
            str(out),
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(f"ffmpeg ({process.returncode}): {stderr.decode(errors='replace')[-300:]}")


async def make_cluster_films(store: VisionStore, cluster_id: str) -> list[Path]:
    """Je Linse mit mindestens ``VISION_FILM_MIN_FRAMES`` Bildern ein Film des
    Vorkommnisses. Returnt die Dateipfade in Anzeigereihenfolge (Weitwinkel, Zoom);
    leer, wenn keine Linse genug Bilder hat. Wirft bei einem ffmpeg-Fehler."""
    source_id, day, frames = await asyncio.to_thread(_lens_frames, store, cluster_id)
    films: list[Path] = []
    for lens, paths in frames.items():
        if len(paths) < VISION_FILM_MIN_FRAMES:
            continue
        out = FILMS_DIR / source_id.replace("/", "_") / day / f"{cluster_id}_{lens}.mp4"
        await _encode(paths, out)
        logger.info("burst film %s: %d frames → %s", lens, len(paths), out)
        films.append(out)
    return films
