"""Ausgabekette für Vigilantia-Bilder — SSOT für JEDES Bild, das die Pipeline
verlässt.

Feste Reihenfolge: Schwärzungs-Maske (DSGVO) → Overlay-Stempel → Speichern.
Alles läuft gebündelt in einem Thread, nichts davon blockiert den Event-Loop.
Eine Instanz pro Watcher hält die Zonen-Masken der laufenden Quellen.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from .config import VISION_OVERLAY_SUFFIX_ZOOM
from .vision_filters.zone_mask import ZoneMask

if TYPE_CHECKING:
    from .frame_sources import Frame

logger = logging.getLogger(__name__)

_BLACKOUT_JPEG_QUALITY = 90


@dataclass(frozen=True)
class FinalizedFrame:
    """Ergebnis der Ausgabekette für ein Bild.

    * ``detect`` — geschwärzt, UNgestempelt: für Face-/YOLO-Detektion, damit
      der Stempel-Balken nie in Crops/Embeddings gerät.
    * ``output`` — geschwärzt + gestempelt (Save/VLM/Alerts); bei
      ``stamp=False`` identisch mit ``detect``.
    * ``path`` — Speicherpfad, ``""`` wenn nicht gespeichert.
    """

    detect: "Frame"
    output: "Frame"
    path: str


@dataclass(frozen=True)
class PairOutput:
    """Wide- und Zoom-Bild eines Dual-Lens-Ticks nach der Ausgabekette.
    ``zoom_path`` ist leer, wenn es kein eigenes Zoom-Bild gibt."""

    wide: FinalizedFrame
    zoom_path: str


class OutputChain:
    """Schwärzen, Stempeln und Speichern von Output-Frames."""

    def __init__(self, frames_dir: Path) -> None:
        self._frames_dir = frames_dir
        self._zone_masks: dict[str, ZoneMask | None] = {}

    # ── Zonen-Masken ──────────────────────────────────────────────

    def set_zone_mask(self, source_id: str, mask: ZoneMask | None) -> None:
        self._zone_masks[source_id] = mask

    def forget_source(self, source_id: str) -> None:
        self._zone_masks.pop(source_id, None)

    # ── Kette ─────────────────────────────────────────────────────

    async def finalize(
        self, frame: "Frame", *,
        label_suffix: str = "", save: bool = True, stamp: bool = True,
    ) -> FinalizedFrame:
        def _work() -> FinalizedFrame:
            detect = self._blackout(frame)
            out = self._stamp(detect, label_suffix) if stamp else detect
            path = self._save(out) if save else ""
            return FinalizedFrame(detect, out, path)
        return await asyncio.to_thread(_work)

    async def finalize_pair(
        self, wide: "Frame", zoom: "Frame", *, save: bool = True,
    ) -> PairOutput:
        """Wide- und Zoom-Bild durch die Kette. Ist ``zoom`` dasselbe Objekt
        wie ``wide`` (kein Tele-Objektiv), entsteht kein zweites Bild."""
        wide_out = await self.finalize(wide, save=save)
        zoom_path = ""
        if zoom is not wide:
            zoom_path = (await self.finalize(
                zoom, label_suffix=VISION_OVERLAY_SUFFIX_ZOOM, save=save,
            )).path
        return PairOutput(wide_out, zoom_path)

    # ── Stufen ────────────────────────────────────────────────────

    def _blackout(self, frame: "Frame") -> "Frame":
        """DSGVO-Schwärzung: hat die Quelle eine ``blackout``-Maske, werden
        die Pixel der Zone geschwärzt — Speichern/Face/VLM sehen dann nie den
        öffentlichen Raum. Ohne Maske bleibt der Frame unverändert. Ein Bild,
        das sich nicht schwärzen lässt, wird NICHT unverändert durchgereicht,
        sondern als Fehler gemeldet."""
        mask = self._zone_masks.get(frame.source_id)
        if mask is None or not mask.blacks_out:
            return frame
        import cv2
        import numpy as np
        arr = cv2.imdecode(
            np.frombuffer(frame.image_bytes, np.uint8), cv2.IMREAD_COLOR
        )
        if arr is None:
            raise ValueError(
                f"blackout: Frame von {frame.source_id} nicht dekodierbar"
            )
        ok, buf = cv2.imencode(
            ".jpg", mask.blackout(arr),
            [cv2.IMWRITE_JPEG_QUALITY, _BLACKOUT_JPEG_QUALITY],
        )
        if not ok:
            raise ValueError(
                f"blackout: Frame von {frame.source_id} nicht kodierbar"
            )
        return replace(frame, image_bytes=buf.tobytes())

    @staticmethod
    def _stamp(frame: "Frame", label_suffix: str) -> "Frame":
        """Dokumentations-Overlay (Name + Ort + Aufnahmezeit) einmal ins Bild
        brennen — im Moment, in dem es als Output genommen wird. Derselbe
        gestempelte Frame fließt dann in Speichern, VLM und Gesichtserkennung;
        es gibt keine saubere Zweitkopie. Die Bewegungserkennung läuft
        vorgelagert auf dem Rohstrom und sieht das Overlay nie.

        ``label_suffix``: Zusatz hinter dem Quell-Label — unterscheidet die
        beiden Linsen einer Dual-Lens-Kamera (beide gehören zur selben
        Quelle)."""
        from .vision_utils import annotate_frame, source_overlay_label
        return replace(
            frame,
            image_bytes=annotate_frame(
                frame.image_bytes,
                source_overlay_label(frame.source_id) + label_suffix,
                timestamp=frame.timestamp,
            ),
        )

    def _save(self, frame: "Frame") -> str:
        """JPEG nach ``<frames_dir>/<source>/<date>/`` schreiben. Gibt den
        Pfad zurück, bei Fehler ``""`` (der Fehler wird als Warnung
        gemeldet, das Ereignis entsteht dann ohne Bildpfad)."""
        try:
            day = frame.timestamp.strftime("%Y-%m-%d")
            # source_id kann ``/`` enthalten (z. B. "cam/v4l2_0")
            slug = frame.source_id.replace("/", "_")
            outdir = self._frames_dir / slug / day
            outdir.mkdir(parents=True, exist_ok=True)
            ts_part = frame.timestamp.strftime("%H%M%S_%f")
            outfile = outdir / f"{ts_part}_{uuid.uuid4().hex[:6]}.jpg"
            outfile.write_bytes(frame.image_bytes)
            return str(outfile)
        except OSError as e:
            logger.warning("save_frame failed for %s: %s", frame.source_id, e)
            return ""
