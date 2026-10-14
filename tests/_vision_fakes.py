"""Gemeinsame Fakes für die Vision-Watcher-Tests (ohne Hardware, ohne Modelle).

Spiegelt die Fakes aus ``test_vision_watcher.py`` (FakeSource, FakeFaceDetector,
FakePersonDetector) und ergänzt, was der Edge-AI-Pfad braucht: eine endlose
Quelle, einen Personendetektor mit steuerbarer YOLO-Zählung und einen
Kamera-Client (``get_ai_state`` + ``snap``) mit steuerbarem Zustand.

Die Fakes hängen nur an öffentlichen Schnittstellen (Frame, FrameSource,
FaceDetector, PersonDetector, ReolinkAIClient) — nicht an privaten Methoden
des VisionWatcher.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import AsyncIterator

import cv2
import numpy as np

from aifred.lib.frame_sources import Frame, SourceInfo
from aifred.lib.reolink_ai import ReolinkAIError
from aifred.lib.vision_filters.face_detect import FaceDetection, FaceDetector
from aifred.lib.vision_filters.person_detect import PersonDetection, PersonDetector


def encode_jpeg(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    assert ok
    return bytes(buf)


def blank_jpeg(color: int = 0) -> bytes:
    return encode_jpeg(np.full((240, 320, 3), color, dtype=np.uint8))


def noise_jpeg(seed: int = 1) -> bytes:
    """Strukturiertes Bild: gross genug für den Snap-Check (>= 1000 Bytes) und
    mit pHash != 0, damit der Live-Clusterer eine cluster_id vergibt."""
    rng = np.random.default_rng(seed)
    img = rng.integers(0, 256, size=(240, 320, 3), dtype=np.uint8)
    return encode_jpeg(img)


class FakeSource:
    """Frame-Source mit festem Frame-Ablauf (endet nach der Liste)."""

    kind: str = "test-cam"

    def __init__(self, source_id: str, frames: list[bytes], delay_sec: float = 0.0):
        self.source_id = source_id
        self.display_name = f"Fake {source_id}"
        self._frames = frames
        self._delay = delay_sec

    def is_available(self) -> bool:
        return True

    async def snapshot(self, *, width: int = 0, height: int = 0) -> Frame:
        return self._make_frame(0)

    async def stream(
        self, fps: float = 1.0, *, width: int = 0, height: int = 0
    ) -> AsyncIterator[Frame]:
        for idx in range(len(self._frames)):
            yield self._make_frame(idx)
            if self._delay > 0:
                await asyncio.sleep(self._delay)

    def info(self) -> SourceInfo:
        return SourceInfo(
            source_id=self.source_id,
            display_name=self.display_name,
            kind=self.kind,
            width=320,
            height=240,
            fps=1.0,
            available=True,
        )

    def _make_frame(self, idx: int) -> Frame:
        return Frame(
            source_id=self.source_id,
            timestamp=datetime.now(),
            image_bytes=self._frames[idx],
            format="jpeg",
            width=320,
            height=240,
            metadata={"sequence_id": "fake-seq", "frame_idx": idx, "kind": "rgb"},
        )


class EndlessSource(FakeSource):
    """Liefert das erste Frame im Takt ``delay_sec`` bis zum Abbruch — jedes
    mit frischem Zeitstempel (wie ein lebender RTSP-Strom)."""

    def __init__(self, source_id: str, delay_sec: float = 0.01):
        super().__init__(source_id, [blank_jpeg()], delay_sec=delay_sec)

    async def stream(
        self, fps: float = 1.0, *, width: int = 0, height: int = 0
    ) -> AsyncIterator[Frame]:
        while True:
            yield self._make_frame(0)
            await asyncio.sleep(self._delay)


class FakeFaceDetector(FaceDetector):
    """Gesichtsdetektor mit fester Antwort; kein Modell (kein super().__init__)."""

    def __init__(
        self,
        detections: list[FaceDetection],
        roi_detections: list[FaceDetection] | None = None,
    ):
        self._detections = detections
        self._roi_detections = roi_detections or []
        self.calls = 0
        self.roi_calls: list[list[tuple[int, int, int, int]]] = []

    def detect(self, frame: Frame) -> list[FaceDetection]:
        self.calls += 1
        return list(self._detections)

    def detect_in_regions(
        self, frame: Frame, regions: list[tuple[int, int, int, int]],
    ) -> list[FaceDetection]:
        self.roi_calls.append(list(regions))
        return list(self._roi_detections)


class ScriptedPersonDetector(PersonDetector):
    """YOLO-Ersatz. ``detect`` liefert feste Personenboxen,
    ``detect_category_counts`` die per ``counts`` vorgegebene Stückzahl je
    angefragter Kategorie (nicht gelistet = 0 = "nicht bestätigt").
    ``category_calls`` hält fest, welche Kategorien der Watcher überhaupt
    hat prüfen lassen. Kein Modell (kein super().__init__)."""

    def __init__(
        self,
        counts: dict[str, int] | None = None,
        persons: list[PersonDetection] | None = None,
        counts_per_call: list[dict[str, int]] | None = None,
    ):
        self.counts = dict(counts or {})
        # Die n-te Kategorie-Abfrage (Weitwinkel, dann Zoom) liefert die n-te Zählung;
        # danach gilt ``counts``.
        self._counts_per_call = list(counts_per_call or [])
        self._persons = list(persons or [])
        self.calls = 0
        self.category_calls: list[set[str]] = []

    def detect(self, frame: Frame):
        self.calls += 1
        return list(self._persons)

    def detect_category_counts(self, frame, coco_map, wanted):
        self.category_calls.append(set(wanted))
        counts = self._counts_per_call.pop(0) if self._counts_per_call else self.counts
        return {cat: counts.get(cat, 0) for cat in wanted}


class FakeAiClient:
    """Ersatz für den geteilten Reolink-Client (``vision_snap.get_client``).

    ``state`` ist der Zustand, den ``get_ai_state`` meldet — der Test ändert
    ihn von aussen (``client.state["person"] = True``). ``fail_next`` Aufrufe
    von ``get_ai_state`` werfen ``ReolinkAIError``."""

    def __init__(self) -> None:
        self.state: dict[str, bool] = {
            "person": False, "vehicle": False, "animal": False,
        }
        self.fail_next = 0
        self.errors_raised = 0
        self.state_calls = 0
        self.state_call_times: list[float] = []
        self.snap_channels: list[int] = []
        self._jpeg = noise_jpeg()

    async def get_ai_state(self, channel: int | None = None) -> dict[str, bool]:
        self.state_calls += 1
        self.state_call_times.append(time.monotonic())
        if self.fail_next > 0:
            self.fail_next -= 1
            self.errors_raised += 1
            raise ReolinkAIError("fake poll failure")
        return dict(self.state)

    async def snap(self, channel: int | None = None) -> bytes:
        self.snap_channels.append(-1 if channel is None else channel)
        # Jede Linse zeigt eine andere Szene (Tele ≠ Weitwinkel), sonst gilt das
        # Tele als Duplikat des Weitwinkels und bekommt kein eigenes Bild.
        if channel:
            return noise_jpeg(seed=1 + channel)
        return self._jpeg

    @property
    def snap_calls(self) -> int:
        return len(self.snap_channels)


def make_face(seed: int = 3) -> FaceDetection:
    emb = np.random.default_rng(seed).standard_normal(512).astype(np.float32)
    emb /= np.linalg.norm(emb)
    return FaceDetection(
        bbox=(10, 10, 80, 80), embedding=emb, detection_score=0.95, keypoints=None,
    )
