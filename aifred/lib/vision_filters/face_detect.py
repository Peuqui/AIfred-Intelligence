"""Face-Detection + Embedding via InsightFace buffalo_l.

Wrapper um ``insightface.app.FaceAnalysis``. Macht **zwei** Schritte in
einem Pass: RetinaFace-Detection und ArcFace-Embedding-Extraktion.
Damit ist pro detektiertem Gesicht direkt ein 512-dim Embedding da,
das gegen die in ``vision_store.face_embeddings`` registrierten
Personen gematched werden kann.

Beim ersten Aufruf von ``detect()`` lädt InsightFace das buffalo_l-
Modell (~280 MB) nach ``~/.insightface/models/buffalo_l/``. Initialisierung
ist lazy — der Konstruktor öffnet das Modell noch nicht. So bleibt der
Modul-Import billig (Tests, Unit-Calls bei nicht-Vision-Setups).

Provider und GPU-ID sind konfigurierbar — auf einem GPU-armen System
kann auf ``CPUExecutionProvider`` umgestellt werden.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING, Any

import cv2
import numpy as np

if TYPE_CHECKING:
    from ..frame_sources import Frame

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FaceDetection:
    """Ein einzelnes detektiertes Gesicht.

    ``bbox`` ist ``(x, y, w, h)`` in Bildkoordinaten. ``embedding`` ist
    ein L2-normalisierter 512-dim ``float32``-Vektor — direkt für
    Cosine-Similarity gegen ``vision_store`` verwendbar.
    """

    bbox: tuple[int, int, int, int]
    embedding: np.ndarray            # shape (512,), float32, L2-normalized
    detection_score: float
    keypoints: np.ndarray | None     # shape (5, 2) — eye/nose/mouth landmarks


class FaceDetector:
    """Lazy-initialized InsightFace-Wrapper.

    Eine Instanz pro Prozess ist genug — InsightFace ist thread-safe
    für ``get()``-Calls. Bei Provider-Wechsel (z.B. CPU↔GPU) muss eine
    neue Instanz erstellt werden.
    """

    def __init__(
        self,
        *,
        model_name: str = "buffalo_l",
        providers: list[str] | None = None,
        gpu_id: int = 0,
        det_size: int = 640,
    ) -> None:
        if providers is None:
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        self._model_name = model_name
        self._providers = providers
        self._gpu_id = gpu_id
        self._det_size = det_size
        self._app: Any | None = None
        self._init_lock = Lock()

    def _ensure_initialized(self) -> Any:
        """Lädt InsightFace-Modell beim ersten Aufruf. Idempotent + thread-safe."""
        if self._app is not None:
            return self._app
        with self._init_lock:
            if self._app is not None:
                return self._app
            try:
                from insightface.app import FaceAnalysis
            except ImportError as e:
                raise RuntimeError(
                    "insightface is not installed — install via "
                    "`pip install insightface onnxruntime-gpu`"
                ) from e
            logger.info(
                "Initializing InsightFace %s (providers=%s, gpu_id=%d, det_size=%d)",
                self._model_name, self._providers, self._gpu_id, self._det_size,
            )
            app = FaceAnalysis(name=self._model_name, providers=self._providers)
            app.prepare(ctx_id=self._gpu_id, det_size=(self._det_size, self._det_size))
            self._app = app
            return app

    def detect(self, frame: "Frame") -> list[FaceDetection]:
        """Detect faces + extract embeddings for one frame.

        Returns leere Liste wenn das Frame nicht decodiert werden kann
        oder kein Gesicht erkannt wurde — kein Exception bei „normalen"
        Bedingungen.
        """
        img = self._decode(frame.image_bytes)
        if img is None:
            return []
        app = self._ensure_initialized()
        try:
            raw = app.get(img)
        except Exception as e:  # noqa: BLE001
            logger.warning("InsightFace get() failed on %s: %s", frame.source_id, e)
            return []
        detections: list[FaceDetection] = []
        for r in raw:
            # InsightFace bbox is (x1, y1, x2, y2) — convert to (x, y, w, h)
            x1, y1, x2, y2 = map(int, r.bbox.tolist() if hasattr(r.bbox, "tolist") else r.bbox)
            bbox = (x1, y1, x2 - x1, y2 - y1)
            # Prefer normed_embedding (L2-normalized) — required for cosine match
            emb = getattr(r, "normed_embedding", None)
            if emb is None:
                emb = getattr(r, "embedding", None)
            if emb is None:
                continue
            embedding = np.asarray(emb, dtype=np.float32).reshape(-1)
            kps = getattr(r, "kps", None)
            kps_arr = np.asarray(kps, dtype=np.float32) if kps is not None else None
            detections.append(
                FaceDetection(
                    bbox=bbox,
                    embedding=embedding,
                    detection_score=float(getattr(r, "det_score", 0.0)),
                    keypoints=kps_arr,
                )
            )
        return detections

    @staticmethod
    def _decode(image_bytes: bytes) -> np.ndarray | None:
        if not image_bytes:
            return None
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)


# ── Module-level convenience singleton ────────────────────────────────
# Settings (provider, gpu_id) können später per-Process geändert werden.
# Tests und CPU-only Hosts setzen vor Erstaufruf:
#   set_default_detector_kwargs(providers=["CPUExecutionProvider"])

_default_detector: FaceDetector | None = None
_default_kwargs: dict[str, Any] = {}
_default_lock = Lock()


def set_default_detector_kwargs(**kwargs: Any) -> None:
    """Konfiguriert den Default-Detector. Muss VOR dem ersten
    ``get_default_detector()``-Call aufgerufen werden, sonst wirkungslos.

    Erlaubte Keys: ``model_name``, ``providers``, ``gpu_id``, ``det_size``.
    """
    global _default_kwargs
    with _default_lock:
        if _default_detector is not None:
            logger.warning(
                "set_default_detector_kwargs called after detector was already "
                "instantiated — new settings will be ignored"
            )
            return
        _default_kwargs = dict(kwargs)


def get_default_detector() -> FaceDetector:
    """Singleton-Detector mit den per ``set_default_detector_kwargs()``
    konfigurierten Optionen (oder Defaults wenn nichts gesetzt wurde)."""
    global _default_detector
    if _default_detector is not None:
        return _default_detector
    with _default_lock:
        if _default_detector is None:
            _default_detector = FaceDetector(**_default_kwargs)
        return _default_detector
