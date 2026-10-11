"""Hochauflösender Schnappschuss aus der Live-Ansicht.

Der Live-Strom der Oberfläche ist der kleine Substream. Ein Foto kommt deshalb in
voller Linsenauflösung: bei Kameras mit Snap-API über diese (Reolink, 4K), sonst
als Einzelbild des Hubs. Es läuft durch dieselbe Ausgabekette wie jedes Bild, das
die Pipeline verlässt (Schwärzungs-Maske, Overlay-Stempel, Speichern) und liegt
unter ``data/vigilantia/photos/<Quelle>/<Datum>/``.
"""

from __future__ import annotations

from pathlib import Path

from .frame_hub import get_default_hub
from .frame_sources import get as get_source
from .vision_filters.zone_mask import load_zone_mask
from .vision_output import OutputChain
from .vision_snap import resolve_snap_channel, snap_frames
from .vision_utils import VIGILANTIA_DIR

PHOTOS_DIR = VIGILANTIA_DIR / "photos"


class PhotoError(RuntimeError):
    """Der Schnappschuss konnte nicht aufgenommen oder gespeichert werden."""


async def take_photo(source_id: str) -> str:
    """Nimmt ein Foto der Quelle auf und liefert seine URL
    (``/_upload/vigilantia/photos/…``)."""
    if resolve_snap_channel(source_id) is not None:
        frames = await snap_frames(source_id, 1)
        if not frames:
            raise PhotoError(f"Schnappschuss der Kamera {source_id} fehlgeschlagen")
        frame = frames[0]
    else:
        source = get_source(source_id)
        if source is None:
            raise PhotoError(f"unbekannte Quelle: {source_id}")
        frame = await get_default_hub().snapshot(source)
    chain = OutputChain(PHOTOS_DIR)
    chain.set_zone_mask(source_id, load_zone_mask(source_id))
    finalized = await chain.finalize(frame, save=True)
    if not finalized.path:
        raise PhotoError(f"Foto von {source_id} konnte nicht gespeichert werden")
    relative = Path(finalized.path).relative_to(VIGILANTIA_DIR)
    return f"/_upload/vigilantia/{relative.as_posix()}"
