"""Hochauflösender Schnappschuss aus der Live-Ansicht.

Der Live-Strom der Oberfläche ist der kleine Substream. Ein Foto kommt deshalb in
voller Linsenauflösung: bei Kameras mit Snap-API über diese (Reolink, 4K), sonst
als Einzelbild des Hubs. Es läuft durch dieselbe Ausgabekette wie jedes Bild, das
die Pipeline verlässt (Schwärzungs-Maske, Overlay-Stempel, Speichern) und liegt
unter ``data/vigilantia/photos/<Quelle>/<Datum>/``.
"""

from __future__ import annotations

from pathlib import Path

from .user_error import UpstreamFailed
from .vision_capture import grab_frame, output_chain
from .vision_utils import VIGILANTIA_DIR

PHOTOS_DIR = VIGILANTIA_DIR / "photos"


class PhotoError(UpstreamFailed):
    """Der Schnappschuss konnte nicht aufgenommen oder gespeichert werden."""


async def take_photo(source_id: str) -> str:
    """Nimmt ein Foto der Quelle auf und liefert seine URL
    (``/_upload/vigilantia/photos/…``)."""
    try:
        frame = await grab_frame(source_id, snap_required=True)
    except (TimeoutError, RuntimeError) as error:  # no frame in time, or the stream ended without one
        raise PhotoError("photo_failed", camera=source_id) from error
    finalized = await output_chain(source_id, PHOTOS_DIR).finalize(frame, save=True)
    if not finalized.path:
        raise PhotoError("photo_not_saved", camera=source_id)
    relative = Path(finalized.path).relative_to(VIGILANTIA_DIR)
    return f"/_upload/vigilantia/{relative.as_posix()}"
