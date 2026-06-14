"""Person-Detection via YOLO11n (ONNX, onnxruntime).

Erkennt GANZE Personen (Körper) als Bounding-Box — ergänzend zur
InsightFace-Gesichtserkennung: YOLO sagt „ein Mensch ist da" (auch mit
abgewandtem Gesicht/von weitem), InsightFace sagt „wer". Reine
onnxruntime-Inferenz — kein ultralytics-Framework nötig.

Das Modell (``yolo11n.onnx``, ~10 MB) liegt in ``VISION_MODELS_DIR``
(``data/vigilantia/models/``). Initialisierung ist lazy — der Konstruktor
öffnet nichts. Fehlt das Modell, gibt es beim ersten ``detect()`` einen
eindeutigen Fehler (kein stiller Fallback).

Provider/GPU sind konfigurierbar; Default ist CPU, damit die GPUs frei
für LLM/VLM/TTS bleiben (analog FACE_DETECT_USE_GPU).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any

import cv2
import numpy as np

if TYPE_CHECKING:
    from ..frame_sources import Frame

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PersonDetection:
    """Eine einzelne detektierte Person.

    ``bbox`` ist ``(x, y, w, h)`` in Original-Bildkoordinaten.
    ``score`` ist die YOLO-Konfidenz (0..1).
    """

    bbox: tuple[int, int, int, int]
    score: float


def _letterbox(
    img: np.ndarray, size: int
) -> tuple[np.ndarray, float, float, float]:
    """Skaliert ``img`` unter Beibehaltung des Seitenverhältnisses auf
    ``size``×``size`` und füllt den Rest grau (114) auf. Returnt das Bild
    plus ``(scale, pad_x, pad_y)`` zum Zurückrechnen der Boxen."""
    h, w = img.shape[:2]
    scale = min(size / w, size / h)
    new_w, new_h = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    pad_x = (size - new_w) / 2
    pad_y = (size - new_h) / 2
    top, left = int(round(pad_y)), int(round(pad_x))
    canvas[top : top + new_h, left : left + new_w] = resized
    return canvas, scale, pad_x, pad_y


class PersonDetector:
    """Lazy-initialisierter YOLO-Person-Detektor (onnxruntime)."""

    def __init__(
        self,
        *,
        model_path: Path | None = None,
        providers: list[str] | None = None,
        input_size: int | None = None,
        confidence: float | None = None,
        nms_iou: float | None = None,
        class_id: int | None = None,
    ) -> None:
        from ..config import (
            PERSON_DETECT_CLASS_ID,
            PERSON_DETECT_CONFIDENCE,
            PERSON_DETECT_INPUT_SIZE,
            PERSON_DETECT_MODEL,
            PERSON_DETECT_NMS_IOU,
            VISION_MODELS_DIR,
        )

        self._model_path = model_path or (VISION_MODELS_DIR / PERSON_DETECT_MODEL)
        self._providers = providers or ["CPUExecutionProvider"]
        self._input_size = input_size or PERSON_DETECT_INPUT_SIZE
        self._confidence = confidence if confidence is not None else PERSON_DETECT_CONFIDENCE
        self._nms_iou = nms_iou if nms_iou is not None else PERSON_DETECT_NMS_IOU
        self._class_id = class_id if class_id is not None else PERSON_DETECT_CLASS_ID
        self._session: Any | None = None
        self._input_name: str = ""
        self._init_lock = Lock()

    def _ensure_initialized(self) -> Any:
        """Lädt die ONNX-Session beim ersten Aufruf. Idempotent + thread-safe."""
        if self._session is not None:
            return self._session
        with self._init_lock:
            if self._session is not None:
                return self._session
            if not self._model_path.exists():
                raise RuntimeError(
                    f"YOLO person model not found at {self._model_path} — "
                    "download yolo11n.onnx into VISION_MODELS_DIR."
                )
            import onnxruntime as ort

            logger.info(
                "Initializing YOLO person detector %s (providers=%s, size=%d)",
                self._model_path.name, self._providers, self._input_size,
            )
            session = ort.InferenceSession(
                str(self._model_path), providers=self._providers
            )
            inp = session.get_inputs()[0]
            self._input_name = inp.name
            # Adopt the model's own fixed input size when it has one — a
            # YOLO export is locked to its export resolution (e.g. 640), so
            # the config value is only a hint. A dynamic axis (non-int, e.g.
            # 'height') keeps the config size (letterboxed to that square).
            # Camera resolution stays fully generic via the letterbox either
            # way; this only reconciles the MODEL's input square.
            shape = list(getattr(inp, "shape", []) or [])
            if (
                len(shape) == 4
                and isinstance(shape[2], int) and shape[2] > 0
                and isinstance(shape[3], int) and shape[3] > 0
                and shape[2] == shape[3]
            ):
                if shape[2] != self._input_size:
                    logger.info(
                        "person_detect: model expects %d×%d input — overriding "
                        "configured size %d", shape[2], shape[3], self._input_size,
                    )
                self._input_size = shape[2]
            self._session = session
            return session

    def detect(self, frame: "Frame") -> list[PersonDetection]:
        """Detektiert Personen in einem Frame. Leere Liste wenn das Bild
        nicht decodiert werden kann oder keine Person erkannt wird."""
        img = self._decode(frame.image_bytes)
        if img is None:
            return []
        session = self._ensure_initialized()
        blob, scale, pad_x, pad_y = self._preprocess(img)
        try:
            outputs = session.run(None, {self._input_name: blob})
        except Exception as e:  # noqa: BLE001
            logger.warning("YOLO run failed on %s: %s", frame.source_id, e)
            return []
        return self._postprocess(outputs[0], img.shape[:2], scale, pad_x, pad_y)

    def detect_present_categories(
        self, frame: "Frame", coco_map: dict[str, list[int]],
        wanted: set[str],
    ) -> set[str]:
        """Multi-Class-Präsenzcheck in EINER Inferenz. Returnt die Teilmenge
        von ``wanted``, deren COCO-Klassen (aus ``coco_map``) im Bild über der
        Konfidenz-Schwelle auftauchen.

        Für ein Bestätigungs-Gate genügt Präsenz (mind. eine Box über
        Schwelle) — kein NMS nötig, das entfernt nur Duplikate, ändert die
        Präsenz nicht. Das volle COCO-Modell liefert alle 80 Klassen aus
        derselben Inferenz; wir lesen nur die angefragten Spalten."""
        present: set[str] = set()
        if not wanted:
            return present
        # Infrastruktur-Fehler (Decode/Inferenz) propagieren bewusst, damit
        # der Caller sie von "sauber gelaufen, nichts gesehen" unterscheiden
        # kann: ein Crash darf einen echten Alarm nicht still verschlucken
        # (Best-Effort-Allow im Caller), eine leere Menge dagegen schon.
        img = self._decode(frame.image_bytes)
        if img is None:
            raise RuntimeError("frame decode failed")
        session = self._ensure_initialized()
        blob, _scale, _px, _py = self._preprocess(img)
        outputs = session.run(None, {self._input_name: blob})
        arr = np.asarray(outputs[0])
        if arr.ndim == 3:
            arr = arr[0]
        if arr.ndim != 2:
            return present
        if arr.shape[0] < arr.shape[1]:
            arr = arr.T
        n_cols = arr.shape[1]
        for cat in wanted:
            for cid in coco_map.get(cat, []):
                col = 4 + cid
                if col < n_cols and float(arr[:, col].max()) >= self._confidence:
                    present.add(cat)
                    break
        return present

    def _preprocess(
        self, img: np.ndarray
    ) -> tuple[np.ndarray, float, float, float]:
        canvas, scale, pad_x, pad_y = _letterbox(img, self._input_size)
        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
        blob = rgb.astype(np.float32) / 255.0
        blob = np.transpose(blob, (2, 0, 1))[np.newaxis, ...]
        return np.ascontiguousarray(blob), scale, pad_x, pad_y

    def _postprocess(
        self,
        output: np.ndarray,
        orig_hw: tuple[int, int],
        scale: float,
        pad_x: float,
        pad_y: float,
    ) -> list[PersonDetection]:
        # YOLOv8/v11 detect-Export: (1, 4+num_classes, num_anchors).
        # Batch-Achse weg → (channels, anchors), transponieren → (anchors,
        # channels): Zeile = Anchor, Spalten 0-3 = cx,cy,w,h (Input-Pixel),
        # ab 4 = Klassen-Scores. Anchors (~8400) >> channels (~84), daher
        # ist die kleinere Achse die Kanal-Achse.
        arr = np.asarray(output)
        if arr.ndim == 3:
            arr = arr[0]
        if arr.ndim != 2:
            return []
        if arr.shape[0] < arr.shape[1]:
            arr = arr.T
        if arr.shape[1] <= 4 + self._class_id:
            return []
        scores = arr[:, 4 + self._class_id]
        keep = scores >= self._confidence
        if not np.any(keep):
            return []
        rows = arr[keep]
        scores = scores[keep]
        h, w = orig_hw
        boxes: list[list[int]] = []
        for cx, cy, bw, bh in rows[:, :4]:
            # Letterbox rückwärts: Padding raus, durch scale teilen.
            x = (cx - bw / 2 - pad_x) / scale
            y = (cy - bh / 2 - pad_y) / scale
            boxes.append([
                int(max(0, x)),
                int(max(0, y)),
                int(min(w, bw / scale)),
                int(min(h, bh / scale)),
            ])
        idxs = cv2.dnn.NMSBoxes(
            boxes, scores.tolist(), self._confidence, self._nms_iou
        )
        if len(idxs) == 0:
            return []
        flat = idxs.flatten() if hasattr(idxs, "flatten") else idxs
        return [
            PersonDetection(
                bbox=(boxes[i][0], boxes[i][1], boxes[i][2], boxes[i][3]),
                score=float(scores[i]),
            )
            for i in flat
        ]

    @staticmethod
    def _decode(image_bytes: bytes) -> np.ndarray | None:
        if not image_bytes:
            return None
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)


# ── Module-level convenience singleton ────────────────────────────────

_default_detector: PersonDetector | None = None
_default_kwargs: dict[str, Any] = {}
_default_lock = Lock()


def set_default_detector_kwargs(**kwargs: Any) -> None:
    """Konfiguriert den Default-Detector. Muss VOR dem ersten
    ``get_default_detector()``-Call aufgerufen werden (z.B. in Tests)."""
    global _default_kwargs
    with _default_lock:
        if _default_detector is not None:
            logger.warning(
                "set_default_detector_kwargs called after detector was already "
                "instantiated — new settings will be ignored"
            )
            return
        _default_kwargs = dict(kwargs)


def get_default_detector() -> PersonDetector:
    """Singleton-Detector. Provider kommt aus der Config:

    * ``PERSON_DETECT_USE_GPU=False`` (Default): CPUExecutionProvider.
    * ``PERSON_DETECT_USE_GPU=True``: cuDNN/CUDA vorladen + CUDA-EP, sonst
      eindeutiger Fehler (kein silent CPU-Fallback)."""
    global _default_detector
    if _default_detector is not None:
        return _default_detector
    with _default_lock:
        if _default_detector is None:
            kwargs = dict(_default_kwargs)
            if "providers" not in kwargs:
                from ..config import PERSON_DETECT_USE_GPU
                if PERSON_DETECT_USE_GPU:
                    from .face_detect import _preload_cuda_runtime
                    if _preload_cuda_runtime():
                        kwargs["providers"] = ["CUDAExecutionProvider"]
                        logger.info("person_detect: using CUDAExecutionProvider")
                    else:
                        raise RuntimeError(
                            "PERSON_DETECT_USE_GPU=True but CUDA libs could not "
                            "be preloaded. Check nvidia-cudnn-cu12 in venv."
                        )
                else:
                    kwargs["providers"] = ["CPUExecutionProvider"]
                    logger.info("person_detect: using CPUExecutionProvider (config default)")
            _default_detector = PersonDetector(**kwargs)
        return _default_detector
