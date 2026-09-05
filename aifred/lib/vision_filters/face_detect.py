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


def _preload_cuda_runtime() -> bool:
    """Lädt cuDNN-9 + CUDA-runtime aus dem venv vor, damit onnxruntime
    sie findet — ohne LD_LIBRARY_PATH-Setup wird die cuda-EP-DLL nicht
    geladen.

    Nur gerufen wenn ``FACE_DETECT_USE_GPU=True`` in der config. Returnt
    True wenn alle nötigen Libs geladen wurden, False wenn nicht — der
    Caller entscheidet dann ob CUDA-EP überhaupt registriert wird.

    nvidia-cudnn-cu12 / nvidia-cuda-runtime-cu12 sind als pip-Packages
    im venv installiert; wir nutzen ctypes.CDLL mit RTLD_GLOBAL, sodass
    onnxruntime sie beim dlopen() der EP-DLL findet.
    """
    import ctypes
    from pathlib import Path

    # aifred/lib/vision_filters/face_detect.py
    # → ../../../venv/lib/python3.12/site-packages/nvidia/
    project_root = Path(__file__).resolve().parents[3]
    nvidia_base = (
        project_root / "venv/lib/python3.12/site-packages/nvidia"
    )
    if not nvidia_base.exists():
        logger.debug(
            "nvidia pip packages not found under %s — skipping preload",
            nvidia_base,
        )
        return False

    # Reihenfolge wichtig: cuda-runtime + cublas zuerst, dann cuDNN
    # (graph → ops → Rest). Symbol-Auflösung passiert beim Load der
    # abhängigen Libs.
    candidates = [
        nvidia_base / "cuda_runtime/lib/libcudart.so.12",
        nvidia_base / "cublas/lib/libcublasLt.so.12",
        nvidia_base / "cublas/lib/libcublas.so.12",
        nvidia_base / "cudnn/lib/libcudnn_graph.so.9",
        nvidia_base / "cudnn/lib/libcudnn_ops.so.9",
        nvidia_base / "cudnn/lib/libcudnn_adv.so.9",
        nvidia_base / "cudnn/lib/libcudnn_cnn.so.9",
        nvidia_base / "cudnn/lib/libcudnn_engines_precompiled.so.9",
        nvidia_base / "cudnn/lib/libcudnn_engines_runtime_compiled.so.9",
        nvidia_base / "cudnn/lib/libcudnn_heuristic.so.9",
        nvidia_base / "cudnn/lib/libcudnn.so.9",
    ]
    loaded = 0
    for path in candidates:
        if not path.exists():
            continue
        try:
            ctypes.CDLL(str(path), mode=ctypes.RTLD_GLOBAL)
            loaded += 1
        except OSError as e:
            logger.debug("preload %s failed: %s", path.name, e)
    if loaded:
        logger.info(
            "face_detect: preloaded %d CUDA libs for onnxruntime", loaded,
        )
        return True
    logger.warning("face_detect: no CUDA libs could be preloaded")
    return False


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


def box_iou(
    a: tuple[int, int, int, int], b: tuple[int, int, int, int],
) -> float:
    """Intersection-over-Union zweier ``(x, y, w, h)``-Boxen. SSoT für
    alle, die zwei Detektionen auf „dieselbe Box" prüfen (ROI-Dedupe hier,
    Enroll-Zuordnung in ``face_enroll``)."""
    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh
    ix = max(0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


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
        min_score: float = 0.0,
        min_size_px: int = 0,
        roi_dedupe_iou: float = 0.3,
    ) -> None:
        if providers is None:
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        self._model_name = model_name
        self._providers = providers
        self._gpu_id = gpu_id
        self._det_size = det_size
        # Qualitäts-Filter (0 = aus): Detections unter min_score oder mit
        # einer Box-Kante unter min_size_px werden verworfen — InsightFace
        # halluziniert knapp über seinem internen 0.5-Cutoff Gesichter in
        # Texturen, und aus Mini-Boxen kommt kein brauchbares Embedding.
        self._min_score = float(min_score)
        self._min_size_px = int(min_size_px)
        # Ab welcher Überdeckung zwei Funde aus überlappenden Regionen
        # dasselbe Gesicht sind (nur ``detect_in_regions``).
        self._roi_dedupe_iou = float(roi_dedupe_iou)
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
        return self._to_detections(raw, frame.source_id)

    def detect_in_regions(
        self, frame: "Frame", regions: list[tuple[int, int, int, int]],
    ) -> list[FaceDetection]:
        """Zweiter Durchgang auf Bildausschnitten — für Gesichter, die im
        Vollbild unter die Wahrnehmungsschwelle fallen.

        Der Detektor skaliert JEDE Eingabe auf ``det_size`` (640). Ein
        Gesicht, das im 4K-Weitwinkel auf wenige Pixel schrumpft, kommt im
        Ausschnitt einer Personenbox in brauchbarer Größe dort an und wird
        gefunden. Gedacht als Nachschlag, wenn ``detect()`` auf demselben
        Frame nichts fand.

        Die Ausschnitte werden **unskaliert** herausgeschnitten (1:1 aus dem
        Originalbild). Das ist der Punkt, an dem die Qualitätsschwellen ehrlich
        bleiben: Boxkanten im Ausschnitt sind Originalpixel, ``min_size_px``
        misst also weiterhin die echte Gesichtsgröße. Würde man den Ausschnitt
        vor der Detektion vergrößern, wäre jede Box rechnerisch groß genug und
        der Filter gegen unbrauchbare Embeddings ausgehebelt.

        ``regions`` sind ``(x, y, w, h)``-Boxen in Bildkoordinaten (Format der
        Personenerkennung). Zurück kommen Detektionen in Koordinaten des
        VOLLBILDS, ohne Dubletten aus überlappenden Regionen.
        """
        if not regions:
            return []
        img = self._decode(frame.image_bytes)
        if img is None:
            return []
        app = self._ensure_initialized()
        height, width = img.shape[:2]
        found: list[FaceDetection] = []
        for x, y, w, h in regions:
            x1 = max(0, int(x))
            y1 = max(0, int(y))
            x2 = min(width, x1 + int(w))
            y2 = min(height, y1 + int(h))
            # Zu kleine Region kann kein Gesicht der Mindestgröße enthalten.
            if x2 - x1 < self._min_size_px or y2 - y1 < self._min_size_px:
                continue
            try:
                raw = app.get(img[y1:y2, x1:x2])
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "InsightFace get() failed on ROI of %s: %s",
                    frame.source_id, e,
                )
                continue
            for det in self._to_detections(raw, frame.source_id, offset=(x1, y1)):
                if any(
                    box_iou(det.bbox, seen.bbox) > self._roi_dedupe_iou
                    for seen in found
                ):
                    continue
                found.append(det)
        if found:
            logger.info(
                "face_detect: %d face(s) found in person ROI on %s that the "
                "full frame missed", len(found), frame.source_id,
            )
        return found

    def _to_detections(
        self, raw: Any, source_id: str, *, offset: tuple[int, int] = (0, 0),
    ) -> list[FaceDetection]:
        """InsightFace-Rohergebnisse → ``FaceDetection`` (Qualitätsfilter,
        Embedding-Wahl, Box-Konvertierung). Gemeinsam genutzt von
        ``detect()`` und ``detect_in_regions()`` — ``offset`` verschiebt die
        Boxen eines Ausschnitts zurück in Vollbild-Koordinaten."""
        off_x, off_y = offset
        detections: list[FaceDetection] = []
        for r in raw:
            # InsightFace bbox is (x1, y1, x2, y2) — convert to (x, y, w, h)
            x1, y1, x2, y2 = map(int, r.bbox.tolist() if hasattr(r.bbox, "tolist") else r.bbox)
            bbox = (x1 + off_x, y1 + off_y, x2 - x1, y2 - y1)
            score = float(getattr(r, "det_score", 0.0))
            if score < self._min_score or min(bbox[2], bbox[3]) < self._min_size_px:
                logger.debug(
                    "face_detect: dropped low-quality detection on %s "
                    "(score=%.2f, size=%dx%d)",
                    source_id, score, bbox[2], bbox[3],
                )
                continue
            # Prefer normed_embedding (L2-normalized) — required for cosine match
            emb = getattr(r, "normed_embedding", None)
            if emb is None:
                emb = getattr(r, "embedding", None)
            if emb is None:
                continue
            embedding = np.asarray(emb, dtype=np.float32).reshape(-1)
            kps = getattr(r, "kps", None)
            kps_arr = np.asarray(kps, dtype=np.float32) if kps is not None else None
            if kps_arr is not None and offset != (0, 0):
                kps_arr = kps_arr + np.asarray(offset, dtype=np.float32)
            detections.append(
                FaceDetection(
                    bbox=bbox,
                    embedding=embedding,
                    detection_score=score,
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

    Erlaubte Keys: ``model_name``, ``providers``, ``gpu_id``, ``det_size``,
    ``min_score``, ``min_size_px``, ``roi_dedupe_iou``.
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
    """Singleton-Detector — Provider-Liste kommt aus der config:

    * ``FACE_DETECT_USE_GPU=False`` (Default): CPUExecutionProvider only,
      GPU bleibt frei für VLM/TTS/LLMs.
    * ``FACE_DETECT_USE_GPU=True``: lädt cuDNN/CUDA vor + nutzt
      CUDAExecutionProvider (falls Preload klappte), sonst eindeutiger
      Fehler — kein silent CPU-Fallback.

    Explizite ``set_default_detector_kwargs(...)``-Calls vor dem Erst-
    Aufruf überschreiben das Verhalten — z.B. für Tests."""
    global _default_detector
    if _default_detector is not None:
        return _default_detector
    with _default_lock:
        if _default_detector is None:
            # Provider aus config nur dann setzen, wenn der User nichts
            # eigenes über set_default_detector_kwargs() gesetzt hat.
            kwargs = dict(_default_kwargs)
            from ..config import (
                FACE_DETECT_MIN_SCORE,
                FACE_DETECT_MIN_SIZE_PX,
                FACE_DETECT_ROI_DEDUPE_IOU,
            )
            kwargs.setdefault("min_score", FACE_DETECT_MIN_SCORE)
            kwargs.setdefault("min_size_px", FACE_DETECT_MIN_SIZE_PX)
            kwargs.setdefault("roi_dedupe_iou", FACE_DETECT_ROI_DEDUPE_IOU)
            if "providers" not in kwargs:
                from ..config import FACE_DETECT_GPU_ID, FACE_DETECT_USE_GPU
                if FACE_DETECT_USE_GPU:
                    if _preload_cuda_runtime():
                        kwargs["providers"] = ["CUDAExecutionProvider"]
                        kwargs.setdefault("gpu_id", FACE_DETECT_GPU_ID)
                        logger.info(
                            "face_detect: using CUDAExecutionProvider on GPU %d",
                            FACE_DETECT_GPU_ID,
                        )
                    else:
                        # GPU angefragt, aber Preload gescheitert —
                        # eindeutiger Hinweis statt silent fallback.
                        raise RuntimeError(
                            "FACE_DETECT_USE_GPU=True but CUDA libs could "
                            "not be preloaded. Check nvidia-cudnn-cu12 / "
                            "nvidia-cuda-runtime-cu12 in venv."
                        )
                else:
                    kwargs["providers"] = ["CPUExecutionProvider"]
                    logger.info("face_detect: using CPUExecutionProvider (config default)")
            _default_detector = FaceDetector(**kwargs)
        return _default_detector
