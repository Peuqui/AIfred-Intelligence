"""The face worker: InsightFace in a process of its own.

AIfred's side (``FaceWorker``) starts the process on the first detection and hands it every frame over a socket
pair (``multiprocessing.connection``: pickled objects, framed). The worker loads ``FaceAnalyzer`` once and answers
each frame with its faces. Two reasons for the process:

* GPU-Orc can free the card (``GET /api/gpu/holders``, ``POST /api/gpu/release``): ending the process ends its CUDA
  context, and nvidia-smi proves it. Inside AIfred, about 180 MiB of CUDA context stayed on the card after
  InsightFace's sessions were dropped (measured 08.11.2026).
* A crash of onnxruntime ends the worker, not AIfred; the next detection starts a new one.

On a card the worker sees only that card (``CUDA_VISIBLE_DEVICES`` = its UUID). It never starts on a card while
GPU-Orc's traffic light is red (a big model loading, or the cards held free): it then starts on the CPU as a stand-in,
and the first detection after the light turned green moves it back onto the card. The socket and not stdout carries
the frames because ``aifred/__init__.py`` and InsightFace print to stdout.

Run as ``python -m aifred.lib.vision_filters.face_worker <fd>`` by ``FaceWorker`` only.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, replace
from multiprocessing.connection import Connection
from typing import TYPE_CHECKING

from ..config import FACE_WORKER_END_TIMEOUT_S, FACE_WORKER_PROOF_POLL_S, PROJECT_ROOT
from ..gpu_release import GpuReleaseResult
from .face_detect import CPU_PROVIDER, AnalyzerSettings, FaceAnalyzer, FaceDetection

if TYPE_CHECKING:
    from ..frame_sources import Frame

logger = logging.getLogger(__name__)

# The item of the GPU release API (the suites name theirs ``gpu-worker``, ``diarize-worker`` or the engine).
ITEM = "face-worker"
# The worker's first answer once InsightFace is loaded; anything else it sends then is why it could not load.
_READY = "ready"


class FaceWorkerEnded(RuntimeError):
    """The worker ended while a detection waited for it: released by force, or it died."""


@dataclass(frozen=True)
class _Running:
    process: subprocess.Popen[bytes]
    connection: Connection
    gpu_uuid: str | None  # the card it sits on, None on the CPU
    stand_in: bool  # on the CPU only because the light was red when it started


class FaceWorker:
    """AIfred's side of the face worker. ``card``: nvidia-smi index the settings want it on, None = the CPU."""

    def __init__(self, settings: AnalyzerSettings, *, card: int | None) -> None:
        self._settings = settings
        self._card = card
        self._running: _Running | None = None
        self._busy = False  # a detection (or the start for one) is in flight
        self._ended_by = ""  # why the last worker was ended from outside, for the detection that waited on it
        self._requests = threading.Lock()  # one frame at a time through the socket
        self._state = threading.Lock()  # _running, _busy and _ended_by between a detection and a release

    def analyze(self, frame: Frame, regions: list[tuple[int, int, int, int]] | None) -> list[FaceDetection]:
        """The faces of ``frame`` (``regions`` None: the whole frame, else only these boxes). Blocking."""
        with self._requests:
            with self._state:
                self._busy = True
            try:
                running = self._current_worker()
                running.connection.send((frame, regions))
                faces: list[FaceDetection] = running.connection.recv()
            except (EOFError, OSError) as error:
                with self._state:
                    ended_by = self._ended_by
                raise FaceWorkerEnded(ended_by or f"the face worker ended: {error!r}") from error
            finally:
                with self._state:
                    self._busy = False
        return faces

    def holders(self) -> list[dict[str, object]]:
        """What the worker holds on the cards, in the GPU release API's form: nothing on the CPU or when it
        does not run; on a card its memory there as nvidia-smi lists it for the worker's PID."""
        with self._state:
            running, busy = self._running, self._busy
        if running is None or running.gpu_uuid is None or running.process.poll() is not None:
            return []
        memory = _memory_on_card_mib(running.process.pid, running.gpu_uuid)
        return [{
            "item": ITEM,
            "gpu_uuids": [running.gpu_uuid],
            "memory_mib": {running.gpu_uuid: memory} if memory is not None else None,
            "busy": busy,
        }]

    def release(self, *, force: bool, reason: str) -> GpuReleaseResult:
        """End the worker and prove its card free: ``released``, ``not_running`` (none runs, or it runs on the
        CPU and holds no card), ``busy`` (a detection runs, left alone without ``force``) or ``failed``. A
        detection that waited on a worker ended by force raises ``FaceWorkerEnded`` with ``reason``."""
        with self._state:
            running = self._running
            if running is None or running.process.poll() is not None:
                return GpuReleaseResult(ITEM, "not_running", "nothing runs")
            if running.gpu_uuid is None:
                return GpuReleaseResult(ITEM, "not_running", "it runs on the CPU and holds no card")
            if self._busy and not force:
                return GpuReleaseResult(ITEM, "busy", "a face detection is running")
            self._running = None
            self._ended_by = f"the face worker was released: {reason}"
        logger.info("face worker: releasing PID %d on %s (%s)", running.process.pid, running.gpu_uuid, reason)
        unproven = _end(running)
        if unproven:
            return GpuReleaseResult(ITEM, "failed", unproven)
        return GpuReleaseResult(ITEM, "released", f"PID {running.process.pid} ended, nvidia-smi no longer lists it")

    def _current_worker(self) -> _Running:
        """The worker to send the next frame to: the running one, a new one when none runs or it died, the card
        instead of the CPU stand-in once the light is green."""
        with self._state:
            running = self._running
        if running is not None and running.process.poll() is not None:
            logger.error("face worker PID %d died (exit code %s), starting a new one",
                         running.process.pid, running.process.returncode)
            running.connection.close()
            self._drop(running)
            running = None
        if running is not None and running.stand_in and _light_refusal() is None:
            logger.info("face worker: the cards are free again, moving it from the CPU to card %s", self._card)
            _end(running)
            self._drop(running)
            running = None
        return running or self._start()

    def _drop(self, running: _Running) -> None:
        """Forget ``running`` unless a release already replaced it."""
        with self._state:
            if self._running is running:
                self._running = None

    def _start(self) -> _Running:
        gpu_uuid: str | None = None
        stand_in = False
        if self._card is not None:
            refusal = _light_refusal()
            if refusal is None:
                gpu_uuid = _uuid_of_card(self._card)
            else:
                stand_in = True
                logger.warning("face worker starts on the CPU, not on card %s: %s", self._card, refusal)
        settings = self._settings if gpu_uuid is not None else replace(self._settings, providers=(CPU_PROVIDER,))
        ours, theirs = socket.socketpair()
        with theirs:
            process = subprocess.Popen(
                [sys.executable, "-m", __name__, str(theirs.fileno())],
                pass_fds=(theirs.fileno(),),
                env={**os.environ, "CUDA_VISIBLE_DEVICES": gpu_uuid or ""},
                cwd=PROJECT_ROOT,
            )
        running = _Running(process, Connection(ours.detach()), gpu_uuid, stand_in)
        with self._state:
            self._running = running
            self._ended_by = ""
        running.connection.send(settings)
        answer = running.connection.recv()  # EOFError when it was released by force while loading
        if answer != _READY:
            _end(running)
            self._drop(running)
            raise RuntimeError(f"the face worker could not load InsightFace: {answer}")
        logger.info("face worker PID %d ready on %s", process.pid, gpu_uuid or "the CPU")
        return running


def _light_refusal() -> str | None:
    """Why nothing may go on a card right now (GPU-Orc's traffic light), None when it may."""
    from ..vision_routing import load_in_progress

    return load_in_progress()


def _uuid_of_card(index: int) -> str:
    from ..nvidia_smi import query

    for row in query("index,uuid") or []:
        if int(row["index"]) == index:
            return row["uuid"]
    raise RuntimeError(f"nvidia-smi lists no card {index} for the face worker")


def _memory_on_card_mib(pid: int, gpu_uuid: str) -> int | None:
    """The memory nvidia-smi lists for ``pid`` on ``gpu_uuid``; None while it lists the process there not (yet)."""
    from ..nvidia_smi import compute_apps

    for app in compute_apps():
        if app["pid"] == str(pid) and app["gpu_uuid"] == gpu_uuid:
            return int(app["used_memory"])
    return None


def _end(running: _Running) -> str:
    """End the worker and prove it: "" when the process is gone and, on a card, nvidia-smi no longer lists it;
    else what is not proven. ``kill`` on a process that already ended does nothing, so this also proves a worker
    that died by itself."""
    pid = running.process.pid
    running.process.kill()
    try:
        running.process.wait(timeout=FACE_WORKER_END_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return f"PID {pid} still alive {FACE_WORKER_END_TIMEOUT_S:g} s after SIGKILL"
    finally:
        running.connection.close()
    if running.gpu_uuid is None:
        return ""
    deadline = time.monotonic() + FACE_WORKER_END_TIMEOUT_S
    while _memory_on_card_mib(pid, running.gpu_uuid) is not None:
        if time.monotonic() >= deadline:
            return f"PID {pid} ended, but nvidia-smi still lists it on {running.gpu_uuid}"
        time.sleep(FACE_WORKER_PROOF_POLL_S)
    return ""


def main() -> None:
    """The worker: load InsightFace with the settings the parent sends first, then answer frame after frame
    until the parent closes the socket."""
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format=f"[face-worker {os.getpid()}] %(levelname)s %(name)s: %(message)s")
    connection = Connection(int(sys.argv[1]))
    settings: AnalyzerSettings = connection.recv()
    try:
        analyzer = FaceAnalyzer.load(settings)
    except Exception as error:  # whatever the load raises, the parent must hear of it
        logger.exception("loading InsightFace failed")
        connection.send(f"{type(error).__name__}: {error}")
        return
    connection.send(_READY)
    while True:
        try:
            frame, regions = connection.recv()
        except EOFError:
            return
        connection.send(analyzer.detect(frame) if regions is None else analyzer.detect_in_regions(frame, regions))


if __name__ == "__main__":
    main()
