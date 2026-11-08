"""Face-Detection + Embedding via InsightFace buffalo_l.

Zwei Teile, zwei Prozesse:

* ``FaceAnalyzer`` hält ``insightface.app.FaceAnalysis`` und macht **zwei**
  Schritte in einem Pass: RetinaFace-Detection und ArcFace-Embedding-
  Extraktion. Damit ist pro detektiertem Gesicht direkt ein 512-dim
  Embedding da, das gegen die in ``vision_store.face_embeddings``
  registrierten Personen gematched werden kann. Er läuft nur im
  Arbeitsprozess der Gesichtserkennung (``face_worker``).
* ``FaceDetector`` ist die Seite in AIfred: Er reicht jedes Bild an den
  Arbeitsprozess weiter und startet ihn beim ersten Gebrauch. Weil InsightFace
  in einem eigenen Prozess liegt, lässt sich die Karte wirklich freigeben
  (Prozess beenden, GPU-Orc fragt über ``/api/gpu/release``), und ein Absturz
  von onnxruntime trifft AIfred nicht.

Beim ersten Laden holt InsightFace das buffalo_l-Modell (~280 MB) nach
``~/.insightface/models/buffalo_l/``.
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

    Gerufen im Arbeitsprozess, wenn er auf einer Karte läuft (FaceAnalyzer.load). Returnt
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


CUDA_PROVIDER = "CUDAExecutionProvider"
CPU_PROVIDER = "CPUExecutionProvider"


@dataclass(frozen=True)
class AnalyzerSettings:
    """What the face worker loads InsightFace with. ``providers`` are onnxruntime's; on a card the worker
    sees only that card (``CUDA_VISIBLE_DEVICES``), on the CPU only the CPU provider."""

    providers: tuple[str, ...]
    model_name: str = "buffalo_l"
    det_size: int = 640
    # Qualitäts-Filter (0 = aus): Detections unter min_score oder mit
    # einer Box-Kante unter min_size_px werden verworfen — InsightFace
    # halluziniert knapp über seinem internen 0.5-Cutoff Gesichter in
    # Texturen, und aus Mini-Boxen kommt kein brauchbares Embedding.
    min_score: float = 0.0
    min_size_px: int = 0
    # Ab welcher Überdeckung zwei Funde aus überlappenden Regionen
    # dasselbe Gesicht sind (nur ``detect_in_regions``).
    roi_dedupe_iou: float = 0.3


class FaceDetector:
    """The face detection of this process. InsightFace runs in the face worker (``face_worker.FaceWorker``),
    a process of its own started on first use; on the card ``gpu_id`` (nvidia-smi index) when ``providers``
    name CUDA, else on the CPU."""

    def __init__(
        self,
        *,
        providers: list[str],
        gpu_id: int | None,
        model_name: str = "buffalo_l",
        det_size: int = 640,
        min_score: float = 0.0,
        min_size_px: int = 0,
        roi_dedupe_iou: float = 0.3,
    ) -> None:
        from .face_worker import FaceWorker  # it runs FaceAnalyzer from this module

        settings = AnalyzerSettings(
            providers=tuple(providers), model_name=model_name, det_size=det_size,
            min_score=float(min_score), min_size_px=int(min_size_px), roi_dedupe_iou=float(roi_dedupe_iou),
        )
        self.worker = FaceWorker(settings, card=gpu_id if CUDA_PROVIDER in providers else None)

    def detect(self, frame: Frame) -> list[FaceDetection]:
        """Faces + embeddings of one frame (see ``FaceAnalyzer.detect``)."""
        return self.worker.analyze(frame, None)

    def detect_in_regions(
        self, frame: Frame, regions: list[tuple[int, int, int, int]],
    ) -> list[FaceDetection]:
        """Second pass on the regions of the frame (see ``FaceAnalyzer.detect_in_regions``)."""
        if not regions:
            return []
        return self.worker.analyze(frame, list(regions))


class FaceAnalyzer:
    """InsightFace in the process that holds it: the face worker. ``app`` is the prepared ``FaceAnalysis``
    (``load``); InsightFace ist thread-safe für ``get()``-Calls."""

    def __init__(
        self, app: Any, *, min_score: float = 0.0, min_size_px: int = 0, roi_dedupe_iou: float = 0.3,
    ) -> None:
        self._app = app
        self._min_score = float(min_score)
        self._min_size_px = int(min_size_px)
        self._roi_dedupe_iou = float(roi_dedupe_iou)

    @classmethod
    def load(cls, settings: AnalyzerSettings) -> FaceAnalyzer:
        """Load InsightFace with ``settings``: on the one card the process sees (CUDA in the providers), else
        on the CPU. CUDA asked for and its libraries not loadable is an error, not the CPU."""
        on_card = CUDA_PROVIDER in settings.providers
        if on_card and not _preload_cuda_runtime():
            raise RuntimeError(
                "Face detection should run on CUDA, but the CUDA libraries could not be preloaded. "
                "Check nvidia-cudnn-cu12 / nvidia-cuda-runtime-cu12 in the venv."
            )
        try:
            from insightface.app import FaceAnalysis
        except ImportError as e:
            raise RuntimeError(
                "insightface is not installed — install via "
                "`pip install insightface onnxruntime-gpu`"
            ) from e
        logger.info(
            "Initializing InsightFace %s (providers=%s, det_size=%d)",
            settings.model_name, list(settings.providers), settings.det_size,
        )
        app = FaceAnalysis(name=settings.model_name, providers=list(settings.providers))
        # ctx_id 0 = the only card this process sees; below 0 = the CPU
        app.prepare(ctx_id=0 if on_card else -1, det_size=(settings.det_size, settings.det_size))
        return cls(
            app, min_score=settings.min_score, min_size_px=settings.min_size_px,
            roi_dedupe_iou=settings.roi_dedupe_iou,
        )

    def detect(self, frame: Frame) -> list[FaceDetection]:
        """Detect faces + extract embeddings for one frame.

        Returns leere Liste wenn das Frame nicht decodiert werden kann
        oder kein Gesicht erkannt wurde — kein Exception bei „normalen"
        Bedingungen.
        """
        img = self._decode(frame.image_bytes)
        if img is None:
            return []
        try:
            raw = self._app.get(img)
        except Exception as e:  # onnxruntime's errors share no base class: this frame without faces, logged
            logger.warning("InsightFace get() failed on %s: %s", frame.source_id, e, exc_info=True)
            return []
        return self._to_detections(raw, frame.source_id)

    def detect_in_regions(
        self, frame: Frame, regions: list[tuple[int, int, int, int]],
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
                raw = self._app.get(img[y1:y2, x1:x2])
            except Exception as e:  # onnxruntime's errors share no base class: this region without faces, logged
                logger.warning(
                    "InsightFace get() failed on ROI of %s: %s",
                    frame.source_id, e,
                    exc_info=True,
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


# ── Module-level singleton ────────────────────────────────────────────

_default_detector: FaceDetector | None = None
_default_lock = Lock()


def _detector_kwargs() -> dict[str, Any]:
    """What the detector is built with: the vision settings (``face_recognition``: providers, gpu_id where "auto"
    picks a card and None or no card free means the CPU, det_size, model_name), without providers there config's
    FACE_DETECT_USE_GPU. Read when the detector is made, by whoever needs it first: until 08.11.2026 only the vision
    plugin's tools applied the settings, and a watcher that came first after a start ran on the CPU until the next.
    Whether CUDA's libraries load is the face worker's question (``FaceAnalyzer.load``)."""
    from ..config import (
        FACE_DETECT_GPU_ID,
        FACE_DETECT_MIN_SCORE,
        FACE_DETECT_MIN_SIZE_PX,
        FACE_DETECT_ROI_DEDUPE_IOU,
        FACE_DETECT_USE_GPU,
    )
    from ..vision_gpu_select import resolve_gpu_id
    from ..vision_settings import load_vision_settings

    settings = load_vision_settings().get("face_recognition") or {}
    kwargs: dict[str, Any] = {
        "min_score": FACE_DETECT_MIN_SCORE, "min_size_px": FACE_DETECT_MIN_SIZE_PX, "roi_dedupe_iou": FACE_DETECT_ROI_DEDUPE_IOU,
    }
    gpu_id: int | None
    if "providers" in settings:
        providers = list(settings["providers"])
        gpu_id = resolve_gpu_id(settings.get("gpu_id"))
        if gpu_id is None:
            providers = [provider for provider in providers if provider != CUDA_PROVIDER] or [CPU_PROVIDER]
    elif FACE_DETECT_USE_GPU:
        providers, gpu_id = [CUDA_PROVIDER], FACE_DETECT_GPU_ID
    else:
        providers, gpu_id = [CPU_PROVIDER], None
    kwargs["providers"] = providers
    kwargs["gpu_id"] = gpu_id
    if "det_size" in settings:
        kwargs["det_size"] = int(settings["det_size"])
    if "model_name" in settings:
        kwargs["model_name"] = str(settings["model_name"])
    return kwargs


def get_default_detector() -> FaceDetector:
    """The process's detector, built on first use with :func:`_detector_kwargs`."""
    global _default_detector
    if _default_detector is not None:
        return _default_detector
    with _default_lock:
        if _default_detector is None:
            kwargs = _detector_kwargs()
            logger.info("face_detect: providers %s, gpu_id %s", kwargs["providers"], kwargs["gpu_id"])
            _default_detector = FaceDetector(**kwargs)
        return _default_detector


def running_detector() -> FaceDetector | None:
    """The process's detector if one was built, without building one (the GPU release asks what runs)."""
    return _default_detector
