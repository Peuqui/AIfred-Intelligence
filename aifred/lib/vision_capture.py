"""Ein Weg zum Bild einer Kamera, für alle, die eines brauchen.

* :func:`grab_frame` — das frischste Bild: über die Snap-API der Kamera (volle Linsenauflösung, kein Strom-Nachlauf),
  sonst der geteilte Strom des Frame-Hubs. Foto-Knopf der Oberfläche, Gesichtserfassung, die Kamera-Werkzeuge
  des Agenten und die Aufnahme laufen darüber.
* :func:`output_chain` — die Ausgabekette (``vision_output``: Schwärzungs-Maske der Zone, Zeitstempel, Speichern),
  die jedes Bild durchläuft, das die Pipeline verlässt: auch das, das ein Agent anfordert und das über einen
  Kanal (Telegram) hinausgehen kann. Wer ein Bild von einer Kamera ausgibt, ohne sie, umgeht die DSGVO-Schwärzung.
"""

from __future__ import annotations

from pathlib import Path

from .frame_hub import get_default_hub
from .frame_sources import Frame
from .frame_sources import get as get_source
from .user_error import Missing
from .vision_filters.zone_mask import load_zone_mask
from .vision_output import OutputChain
from .vision_snap import resolve_snap_channel, snap_frames
from .vision_utils import VIGILANTIA_DIR, resolve_source_resolution


async def grab_frame(source_id: str, *, prefer_face: bool = False, snap_required: bool = False) -> Frame:
    """Das frischste Bild der Kamera. Die Snap-API zuerst (``prefer_face``: die Tele-Linse für Gesichter); gibt sie
    keines — oder hat die Kamera keine —, das Einzelbild des Hubs in der Auflösung, die der Nutzer für die Kamera
    gewählt hat (ein Substream-Bild schlägt kein Bild). ``snap_required``: bei einer Kamera mit Snap-API kein
    Ersatz aus dem Substream (ein Foto soll die volle Linsenauflösung haben), sondern ``RuntimeError``.

    Wirft :class:`Missing` für eine unbekannte Kamera, ``RuntimeError``/``TimeoutError``, wenn kein Bild kommt."""
    if resolve_snap_channel(source_id, prefer_face=prefer_face) is not None:
        snapped = await snap_frames(source_id, 1, prefer_face=prefer_face)
        if snapped:
            return snapped[0]
        if snap_required:
            raise RuntimeError(f"the snapshot API of {source_id} gave no picture")
    source = get_source(source_id)
    if source is None:
        raise Missing("unknown_camera", camera=source_id)
    width, height = resolve_source_resolution(source_id)
    # Über den Hub, nicht ``source.snapshot()``: ein laufender Watcher hält die Quelle für den ganzen Strom.
    return await get_default_hub().snapshot(source, width=width, height=height)


def output_chain(source_id: str, frames_dir: Path = VIGILANTIA_DIR) -> OutputChain:
    """Die Ausgabekette mit der Schwärzungs-Maske der Kamera; ``frames_dir`` nur, wenn sie speichert."""
    chain = OutputChain(frames_dir)
    chain.set_zone_mask(source_id, load_zone_mask(source_id))
    return chain
