"""The face detector reads the vision settings itself when it is made (face_detect._detector_kwargs). Until
08.11.2026 only the vision plugin's tools applied them: a watcher that saw a face after a start before anyone chatted
built the detector with config's CPU default, and it stayed there until the next start."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from aifred.lib import vision_gpu_select, vision_settings
from aifred.lib.vision_filters import face_detect

CUDA, CPU = "CUDAExecutionProvider", "CPUExecutionProvider"


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    state: dict[str, Any] = {"settings": {}, "card": 2}
    monkeypatch.setattr(vision_settings, "load_vision_settings", lambda: {"face_recognition": state["settings"]})
    monkeypatch.setattr(vision_gpu_select, "resolve_gpu_id", lambda setting: state["card"] if setting == "auto" else setting)
    monkeypatch.setattr(face_detect, "_default_detector", None)
    yield state
    face_detect._default_detector = None


def test_the_settings_put_it_on_the_card_they_pick(world: dict[str, Any]) -> None:
    world["settings"] = {"providers": [CUDA, CPU], "gpu_id": "auto", "det_size": 480}
    kwargs = face_detect._detector_kwargs()
    assert (kwargs["providers"], kwargs["gpu_id"], kwargs["det_size"]) == ([CUDA, CPU], 2, 480)


def test_without_a_card_it_runs_on_the_cpu(world: dict[str, Any]) -> None:
    world["settings"] = {"providers": [CUDA, CPU], "gpu_id": "auto"}
    world["card"] = None
    kwargs = face_detect._detector_kwargs()
    assert kwargs["providers"] == [CPU] and kwargs["gpu_id"] is None


def test_without_providers_in_the_settings_the_config_decides(world: dict[str, Any]) -> None:
    assert face_detect._detector_kwargs()["providers"] == [CPU]     # FACE_DETECT_USE_GPU is False


def test_cuda_without_its_libraries_is_an_error_not_the_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """The face worker loads InsightFace: on a card without CUDA's libraries it refuses, it does not take the CPU."""
    monkeypatch.setattr(face_detect, "_preload_cuda_runtime", lambda: False)
    with pytest.raises(RuntimeError, match="CUDA"):
        face_detect.FaceAnalyzer.load(face_detect.AnalyzerSettings(providers=(CUDA, CPU)))


def test_the_first_user_gets_a_detector_with_the_settings(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[dict[str, Any]] = []

    class _Detector:
        def __init__(self, **kwargs: Any) -> None:
            built.append(kwargs)

    monkeypatch.setattr(face_detect, "FaceDetector", _Detector)
    world["settings"] = {"providers": [CUDA, CPU], "gpu_id": "auto"}
    first = face_detect.get_default_detector()       # a watcher, before any tool of the plugin ran
    assert face_detect.get_default_detector() is first
    assert len(built) == 1 and built[0]["providers"] == [CUDA, CPU] and built[0]["gpu_id"] == 2
