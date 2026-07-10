"""Motion-Detection mit cv2 BackgroundSubtractor.

Stateful pro Source: jede ``MotionDetector``-Instanz baut eine eigene
Background-History auf. Bei mehreren Cams: eine Instanz pro Cam.

Algorithmus: MOG2 (Mixture of Gaussians) — Standard-Wahl für Outdoor und
Indoor. Liefert Foreground-Maske, daraus berechnen wir Bewegungs-Anteil
(``area_ratio``) und größtes Bounding-Box. ``min_area_ratio`` filtert
Mikro-Rauschen (Wind im Baum, Kompressions-Artefakte).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import cv2
import numpy as np

if TYPE_CHECKING:
    from ..frame_sources import Frame
    from .zone_mask import ZoneMask


# Ego-Motion-Gate: Mindest-Qualität des Phasenkorrelations-Peaks, damit
# dem gemessenen Shift überhaupt geglaubt wird. Empirisch (2026-07-10):
# texturlose Bilder liefern resp ≈ 0.0 (kein Anker), ein sauberer globaler
# Shift ≈ 1.0, ein Objekt auf leerem Grund ≈ 0.5. 0.3 lässt reale Schwenks
# (Motion-Blur drückt die response) durch, verwirft aber Anker-loses Rauschen.
_EGO_MIN_RESPONSE = 0.3
# ... und Mindest-Foreground-Fläche: der real gemessene Schwenk lag bei
# ≈ 55 %, lokale Objekte deutlich darunter. Zusammen mit response schließt
# das den Fall "großes Objekt auf strukturlosem Grund sieht aus wie ein
# Schwenk" weitgehend aus (Restrisiko: Person füllt >25 % UND der Grund
# ist völlig texturlos — dann verliert einmalig das Gate, nie der Alarm
# dauerhaft: nach dem Warmup triggert die weiter anwesende Person erneut).
_EGO_MIN_AREA = 0.25


@dataclass(frozen=True)
class MotionResult:
    """Ergebnis einer Motion-Detection auf einem einzelnen Frame.

    ``area_ratio`` ist der Anteil der Pixel im Foreground (0.0 – 1.0).
    ``bbox`` ist ``(x, y, w, h)`` der größten Kontur in Bildkoordinaten,
    None wenn ``motion=False``.
    """

    motion: bool
    area_ratio: float
    bbox: tuple[int, int, int, int] | None
    foreground_mask: np.ndarray | None = None


class MotionDetector:
    """Stateful Motion-Detector mit cv2 BackgroundSubtractorMOG2.

    Aufruf-Muster::

        det = MotionDetector(min_area_ratio=0.01)
        async for frame in source.stream(fps=2.0):
            result = det.process(frame)
            if result.motion:
                # Trigger face_detect, VLM, etc.
                ...

    ``history`` Frames werden zur Background-Adaption verwendet; bei
    fps=2.0 entsprechen 500 Frames ~4 Minuten — sinnvoll für eine
    Haustür-Cam mit eher statischem Hintergrund. Bei ``warmup_frames``
    werden initial die ersten N Frames als nicht-motion behandelt,
    damit der Hintergrund sich erst stabilisieren kann.
    """

    def __init__(
        self,
        *,
        history: int = 500,
        var_threshold: float = 16.0,
        detect_shadows: bool = False,
        min_area_ratio: float = 0.01,
        warmup_frames: int = 10,
        return_mask: bool = False,
        zone_mask: "ZoneMask | None" = None,
        ego_shift_px: float = 3.0,
    ) -> None:
        self._history = history
        self._var_threshold = var_threshold
        self._detect_shadows = detect_shadows
        self._bg = cv2.createBackgroundSubtractorMOG2(
            history=history,
            varThreshold=var_threshold,
            detectShadows=detect_shadows,
        )
        self._min_area_ratio = min_area_ratio
        self._warmup_frames = warmup_frames
        self._warmup_remaining = warmup_frames
        self._return_mask = return_mask
        self._frames_processed = 0
        # Optionale Ignorier-Zonen (z.B. Bäume): in der Zone wird der
        # Foreground auf 0 gesetzt, bevor area_ratio/bbox berechnet werden.
        self._zone_mask = zone_mask
        # Ego-Motion-Gate über Phasenkorrelation: Beim PTZ-Schwenk
        # verschiebt sich das GANZE Bild kohärent (globaler Shift), bei
        # Szenen-Bewegung (Person) nur ein lokaler Bereich vor stehendem
        # Hintergrund. Die Foreground-FLÄCHE taugt nicht als Kriterium —
        # real gemessen (2026-07-10): Schwenk ≈ 55 %, Person nah an der
        # Linse 60-90 % — die Fälle überlappen genau falsch herum.
        # ``ego_shift_px`` ist der Mindest-Shift auf dem 160 px breiten
        # Analysebild (3 px ≈ 2 % Bildbreite zwischen zwei Frames);
        # konservativ genug, dass Mast-Wackeln im Wind nicht dauernd
        # resettet, klein genug für jeden echten Schwenk.
        self._ego_shift_px = float(ego_shift_px)
        self._prev_small: np.ndarray | None = None

    def set_zone_mask(self, zone_mask: "ZoneMask | None") -> None:
        """Zonen-Maske zur Laufzeit austauschen (Live-Reload aus dem
        Editor), ohne den Background-Subtractor zurückzusetzen."""
        self._zone_mask = zone_mask

    def set_min_area_ratio(self, ratio: float) -> None:
        """Bewegungs-Schwellwert zur Laufzeit ändern (Slider in den Vision-
        Settings), ohne den Background-Subtractor zurückzusetzen."""
        self._min_area_ratio = float(ratio)

    def process(self, frame: "Frame") -> MotionResult:
        """Apply background subtraction and report motion.

        Decodiert ``frame.image_bytes`` (JPEG) in ein cv2-Array und
        evaluiert die Foreground-Maske. CPU only, sehr schnell
        (~5-15 ms bei 640×480).
        """
        img = self._decode(frame.image_bytes)
        if img is None:
            return MotionResult(motion=False, area_ratio=0.0, bbox=None)

        # Pro-Frame Reduktion auf Graustufen + leichter Blur dämpft
        # JPEG-Quant-Rauschen → weniger False-Positives.
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)

        # Globaler Verschiebungsvektor zwischen zwei Frames per Phasen-
        # korrelation (FFT auf 160er-Breite, sub-ms) — Grundlage des
        # Ego-Motion-Gates weiter unten. ``response`` = Schärfe des
        # Korrelationspeaks: auf texturlosen Bildern gibt es keinen
        # stabilen Anker (response → 0), dann ist der Shift bedeutungslos.
        h_img, w_img = blurred.shape
        small = cv2.resize(blurred, (160, max(1, int(160 * h_img / w_img))))
        small_f = small.astype(np.float32)
        ego_shift = 0.0
        ego_response = 0.0
        if (
            self._prev_small is not None
            and self._prev_small.shape == small_f.shape
        ):
            (dx, dy), ego_response = cv2.phaseCorrelate(self._prev_small, small_f)
            ego_shift = (dx * dx + dy * dy) ** 0.5
        self._prev_small = small_f

        mask = self._bg.apply(blurred)

        # Zonen-Maske anwenden: Bewegung wird auf die keep-Zone beschränkt
        # (Ignorier-/Schwärz-Zonen raus; bei vorhandener ROI nur dort).
        if self._zone_mask is not None:
            mask = self._zone_mask.apply_to_motion(mask)

        self._frames_processed += 1
        if self._warmup_remaining > 0:
            self._warmup_remaining -= 1
            return MotionResult(
                motion=False,
                area_ratio=0.0,
                bbox=None,
                foreground_mask=mask if self._return_mask else None,
            )

        h, w = mask.shape
        # Schwellwert relativ zur BEOBACHTETEN Fläche (keep-Pixel): maskiert
        # man große Bereiche weg, bliebe die Empfindlichkeit im Rest sonst
        # zu niedrig (2% vom Gesamtbild wären mehr als 2% des ROI).
        if self._zone_mask is not None:
            observed = float(self._zone_mask.observed_pixels(h, w))
        else:
            observed = float(h * w)
        foreground = float(int(np.count_nonzero(mask)))
        area_ratio = foreground / observed if observed > 0 else 0.0

        # Ego-Motion-Gate (PTZ-Schwenk/Auto-Tracking): NUR wenn drei
        # Signale zusammenkommen, gilt der Frame als Kamerabewegung —
        # dann frisches Hintergrundmodell + Warmup statt Fehltrigger:
        #   1. globaler Shift >= Schwelle (das Bild ist als Ganzes gewandert),
        #   2. Korrelations-response >= Minimum (die Messung hat einen echten
        #      Anker — auf texturlosen Bildern ist sie ~0 und bedeutungslos;
        #      gemessen: uniform+Objekt resp 0.0, echter Shift resp ~1.0),
        #   3. großflächige Änderung (real gemessener Schwenk ≈ 55 % — ein
        #      lokales Objekt, dem die Korrelation auf leerem Grund folgen
        #      könnte, bleibt deutlich darunter).
        # Im Zweifel gewinnt der ALARM, nicht die Schwenk-Vermutung.
        if (
            ego_shift >= self._ego_shift_px
            and ego_response >= _EGO_MIN_RESPONSE
            and area_ratio >= _EGO_MIN_AREA
        ):
            self._bg = cv2.createBackgroundSubtractorMOG2(
                history=self._history,
                varThreshold=self._var_threshold,
                detectShadows=self._detect_shadows,
            )
            self._warmup_remaining = self._warmup_frames
            return MotionResult(
                motion=False,
                area_ratio=area_ratio,
                bbox=None,
                foreground_mask=mask if self._return_mask else None,
            )

        motion = area_ratio >= self._min_area_ratio

        bbox = self._largest_bbox(mask) if motion else None
        return MotionResult(
            motion=motion,
            area_ratio=area_ratio,
            bbox=bbox,
            foreground_mask=mask if self._return_mask else None,
        )

    @staticmethod
    def _decode(image_bytes: bytes) -> np.ndarray | None:
        if not image_bytes:
            return None
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return img

    @staticmethod
    def _largest_bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
        """Größte zusammenhängende Foreground-Kontur als ``(x, y, w, h)``."""
        # Morphological opening to drop single-pixel noise islands
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        cleaned = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        contours, _ = cv2.findContours(
            cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        if not contours:
            return None
        biggest = max(contours, key=cv2.contourArea)
        x, y, w, h = cv2.boundingRect(biggest)
        return (int(x), int(y), int(w), int(h))
