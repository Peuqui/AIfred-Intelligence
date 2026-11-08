"""The face worker: InsightFace in a process of its own, so a release frees its card for real.

Two kinds of worker here. A real one on the CPU (the real module, the real InsightFace, about 2 s per start): the
socket protocol, a crash, the CPU stand-in while GPU-Orc's light is red. And a stand-in process that only holds
its end of the socket and never answers, with nvidia-smi faked: what a release on a card answers and proves.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from datetime import datetime
from multiprocessing.connection import Connection
from typing import Any

import cv2
import numpy as np
import pytest

from aifred.lib.frame_sources import Frame
from aifred.lib.vision_filters import face_worker
from aifred.lib.vision_filters.face_detect import CPU_PROVIDER, CUDA_PROVIDER, AnalyzerSettings
from aifred.lib.vision_filters.face_worker import FaceWorker, FaceWorkerEnded

CARD_UUID = "GPU-00000000-test"


def _frame() -> Frame:
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    return Frame(source_id="cam/test", timestamp=datetime(2026, 11, 8, 18, 0),
                 image_bytes=bytes(cv2.imencode(".jpg", image)[1].tobytes()))


@pytest.fixture
def workers() -> Iterator[list[FaceWorker]]:
    """Every worker a test makes; their processes end afterwards, whatever the test did."""
    made: list[FaceWorker] = []
    yield made
    for worker in made:
        running = worker._running
        if running is not None:
            running.process.kill()
            running.process.wait()


def _cpu_worker(workers: list[FaceWorker], *, card: int | None = None) -> FaceWorker:
    worker = FaceWorker(AnalyzerSettings(providers=(CUDA_PROVIDER, CPU_PROVIDER)), card=card)
    workers.append(worker)
    return worker


def test_a_real_worker_answers_frame_after_frame_and_holds_no_card_on_the_cpu(workers: list[FaceWorker]) -> None:
    worker = _cpu_worker(workers)
    assert worker.analyze(_frame(), None) == []
    assert worker.analyze(_frame(), [(0, 0, 320, 240)]) == []
    running = worker._running
    assert running is not None and running.gpu_uuid is None and not running.stand_in
    assert worker.holders() == []
    result = worker.release(force=False, reason="test")
    assert (result.outcome, result.detail) == ("not_running", "it runs on the CPU and holds no card")
    assert running.process.poll() is None     # nothing to free, so it was left alone


def test_a_worker_that_died_is_replaced_by_the_next_frame(workers: list[FaceWorker]) -> None:
    worker = _cpu_worker(workers)
    worker.analyze(_frame(), None)
    first = worker._running
    assert first is not None
    first.process.kill()
    first.process.wait()
    assert worker.analyze(_frame(), None) == []
    assert worker._running is not None and worker._running.process.pid != first.process.pid


def test_while_the_light_is_red_it_starts_on_the_cpu_and_moves_to_the_card_once_green(
    workers: list[FaceWorker], monkeypatch: pytest.MonkeyPatch,
) -> None:
    light: dict[str, str | None] = {"refusal": "'GLM' lädt gerade"}
    monkeypatch.setattr(face_worker, "_light_refusal", lambda: light["refusal"])
    monkeypatch.setattr(face_worker, "_uuid_of_card", lambda index: pytest.fail("no card while the light is red"))
    worker = _cpu_worker(workers, card=2)
    worker.analyze(_frame(), None)
    stand_in = worker._running
    assert stand_in is not None and stand_in.stand_in and stand_in.gpu_uuid is None
    assert worker.holders() == []

    light["refusal"] = None
    started: list[bool] = []

    def start_on_the_card() -> face_worker._Running:
        started.append(True)
        return stand_in

    monkeypatch.setattr(worker, "_start", start_on_the_card)
    worker._current_worker()
    assert started == [True]
    assert stand_in.process.poll() is not None     # the stand-in on the CPU has ended


# ── release on a card, with a process that only holds its socket and nvidia-smi faked ──

_HOLDER = "import socket, sys, time; s = socket.socket(fileno=int(sys.argv[1])); time.sleep(60)"


@pytest.fixture
def on_card(monkeypatch: pytest.MonkeyPatch, workers: list[FaceWorker]) -> Iterator[dict[str, Any]]:
    """A worker that sits on a card: its process lives until it is killed, nvidia-smi lists it there with 900 MiB
    while ``listed`` says so (by default only while the process lives)."""
    ours, theirs = socket.socketpair()
    with theirs:
        process = subprocess.Popen([sys.executable, "-c", _HOLDER, str(theirs.fileno())], pass_fds=(theirs.fileno(),))
    state: dict[str, Any] = {"process": process, "listed": lambda: process.poll() is None}

    def memory(pid: int, gpu_uuid: str) -> int | None:
        return 900 if pid == process.pid and gpu_uuid == CARD_UUID and state["listed"]() else None

    monkeypatch.setattr(face_worker, "_memory_on_card_mib", memory)
    monkeypatch.setattr(face_worker, "FACE_WORKER_END_TIMEOUT_S", 1.0)
    monkeypatch.setattr(face_worker, "FACE_WORKER_PROOF_POLL_S", 0.05)
    worker = FaceWorker(AnalyzerSettings(providers=(CUDA_PROVIDER, CPU_PROVIDER)), card=2)
    worker._running = face_worker._Running(process, Connection(ours.detach()), CARD_UUID, stand_in=False)
    workers.append(worker)
    state["worker"] = worker
    yield state


def test_on_a_card_it_names_its_card_and_the_memory_nvidia_smi_lists(on_card: dict[str, Any]) -> None:
    assert on_card["worker"].holders() == [
        {"item": "face-worker", "gpu_uuids": [CARD_UUID], "memory_mib": {CARD_UUID: 900}, "busy": False},
    ]


def test_released_means_the_process_ended_and_nvidia_smi_no_longer_lists_it(on_card: dict[str, Any]) -> None:
    result = on_card["worker"].release(force=False, reason="GLM lädt")
    assert result.outcome == "released"
    assert on_card["process"].poll() is not None
    assert on_card["worker"].holders() == [] and on_card["worker"]._running is None


def test_a_card_nvidia_smi_still_lists_is_not_released(on_card: dict[str, Any]) -> None:
    on_card["listed"] = lambda: True     # the driver keeps the memory after the process ended
    result = on_card["worker"].release(force=False, reason="GLM lädt")
    assert result.outcome == "failed" and "still lists it" in result.detail
    assert on_card["process"].poll() is not None


def test_a_running_detection_is_busy_and_only_force_ends_it(on_card: dict[str, Any]) -> None:
    worker: FaceWorker = on_card["worker"]
    raised: list[BaseException] = []

    def detect() -> None:
        try:
            worker.analyze(_frame(), None)     # the holder never answers: the detection waits
        except FaceWorkerEnded as error:
            raised.append(error)

    thread = threading.Thread(target=detect)
    thread.start()
    deadline = time.monotonic() + 5
    while not worker.holders()[0]["busy"]:
        assert time.monotonic() < deadline, "the detection never became busy"
        time.sleep(0.01)

    assert worker.release(force=False, reason="GLM lädt").outcome == "busy"
    assert on_card["process"].poll() is None
    assert worker.release(force=True, reason="GLM lädt").outcome == "released"
    thread.join(timeout=5)
    assert len(raised) == 1 and "released: GLM lädt" in str(raised[0])


def test_nothing_running_is_not_running() -> None:
    worker = FaceWorker(AnalyzerSettings(providers=(CPU_PROVIDER,)), card=None)
    assert worker.release(force=True, reason="test").outcome == "not_running"
    assert worker.holders() == []
