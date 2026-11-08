"""Learning a person from several poses (lib/enrollment.py, /vision/enroll/*): a session in the server, one at a
time; a capture takes the largest face of the camera's picture, cropped like every face crop (the old dialog cut its
own frame), and keeps its embedding in the server (the old dialog kept it, base64, in the browser's state); undo,
skip, a page holding an old session told so; finishing makes the person if new, stores every embedding with its crop
and ends the session. Camera and face model are stand-ins."""

import asyncio
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from starlette.testclient import TestClient

from aifred.lib import enrollment, face_crop_store, vision_store
from aifred.lib.vision_filters import face_detect, face_recognize
from aifred.lib.user_error import Missing
from aifred.lib.vision_store import VisionStore


def _picture() -> bytes:
    image = np.full((480, 640, 3), 90, dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return bytes(encoded)


@pytest.fixture
def camera(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """A camera whose picture holds the faces the test sets (a small and a large one by default); the store and the
    crops in tmp."""
    monkeypatch.setattr(vision_store, "DEFAULT_DB_PATH", tmp_path / "vision.db")
    monkeypatch.setattr(enrollment, "_session", None)
    crops = face_crop_store.FaceCropStore(tmp_path / "crops")
    monkeypatch.setattr(face_crop_store, "get_default_store", lambda: crops)
    world: dict[str, list] = {"faces": [
        SimpleNamespace(bbox=(10, 10, 30, 30), embedding=np.full(512, 0.1, dtype=np.float64), detection_score=0.6),
        SimpleNamespace(bbox=(200, 100, 120, 140), embedding=np.full(512, 0.9, dtype=np.float64), detection_score=0.91),
    ]}

    async def fresh(source_id: str) -> SimpleNamespace:
        if source_id != "cam/door":
            raise Missing("unknown_camera", camera=source_id)
        return SimpleNamespace(image_bytes=_picture())

    monkeypatch.setattr(enrollment, "fresh_face_frame", fresh)
    monkeypatch.setattr(face_detect, "get_default_detector", lambda: SimpleNamespace(detect=lambda frame: world["faces"]))
    return world


def test_a_capture_keeps_the_largest_face_cropped_and_its_embedding_in_the_server(camera: dict[str, list]) -> None:
    session = enrollment.start()
    asyncio.run(enrollment.capture(session.token, "cam/door"))
    taken = session.captures[0]
    assert (taken.pose, taken.score, session.step, session.pose()) == ("frontal", 0.91, 1, "left")
    assert taken.embedding.dtype == np.float32 and float(taken.embedding[0]) == pytest.approx(0.9)
    crop = cv2.imdecode(np.frombuffer(enrollment.crop(session.token, 0), np.uint8), cv2.IMREAD_COLOR)
    assert crop is not None and crop.shape[:2] == (140 + 70 + 42, 120 + 60)   # the face crop store's padding


def test_no_face_is_said_and_nothing_is_taken(camera: dict[str, list]) -> None:
    camera["faces"] = []
    session = enrollment.start()
    with pytest.raises(enrollment.NoFace):
        asyncio.run(enrollment.capture(session.token, "cam/door"))
    assert session.captures == [] and session.step == 0


def test_undo_skip_and_a_session_replaced_by_a_new_one(camera: dict[str, list]) -> None:
    session = enrollment.start()
    asyncio.run(enrollment.capture(session.token, "cam/door"))
    enrollment.undo(session.token)
    assert (session.captures, session.step) == ([], 0)
    for _ in range(len(enrollment.POSES) + 2):
        enrollment.skip(session.token)
    assert session.step == len(enrollment.POSES) and session.pose() == ""
    enrollment.start()
    with pytest.raises(enrollment.SessionGone):
        enrollment.skip(session.token)


def test_finishing_makes_the_person_stores_every_pose_and_ends_the_session(camera: dict[str, list]) -> None:
    session = enrollment.start()
    with pytest.raises(ValueError):
        enrollment.finish(session.token, "Lisa")                   # nothing captured yet
    for _ in range(2):
        asyncio.run(enrollment.capture(session.token, "cam/door"))
    with pytest.raises(ValueError):
        enrollment.finish(session.token, "  ")                     # a new person needs a name
    epoch = face_recognize._enrollment_epoch
    face_id, name, count = enrollment.finish(session.token, "Lisa")
    stored = VisionStore().list_embeddings(face_id)
    assert (name, count, len(stored)) == ("Lisa", 2, 2)
    assert all(row["crop_url"].startswith("/_upload/face_crops/") for row in stored)
    assert face_recognize._enrollment_epoch == epoch + 1
    with pytest.raises(enrollment.SessionGone):
        enrollment.crop(session.token, 0)
    more = enrollment.start(face_id)
    assert more.name == "Lisa"
    with pytest.raises(KeyError):
        enrollment.start(9999)


@pytest.fixture
def client(camera: dict[str, list]) -> Iterator[TestClient]:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import sign_username
    from aifred.lib.auth import USERNAME_COOKIE_NAME

    with TestClient(api_app) as test_client:
        test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        yield test_client


def test_the_session_over_the_api(client: TestClient, camera: dict[str, list]) -> None:
    started = client.post("/vision/enroll", json={}).json()
    token = started["token"]
    assert [pose["id"] for pose in started["poses"]] == list(enrollment.POSES) and started["preview_interval_ms"] > 0
    assert started["poses"][0]["label"] == "Schaue gerade in die Kamera."
    taken = client.post(f"/vision/enroll/{token}/capture", json={"source_id": "cam/door"}).json()
    assert taken["step"] == 1 and "embedding" not in str(taken) and taken["captures"][0]["score"] == 0.91
    picture = client.get(f"/vision/enroll/{token}/captures/0")
    assert picture.headers["content-type"] == "image/jpeg"
    assert client.post(f"/vision/enroll/{token}/capture", json={"source_id": "cam/none"}).status_code == 404
    camera["faces"] = []
    no_face = client.post(f"/vision/enroll/{token}/capture", json={"source_id": "cam/door"})
    assert (no_face.status_code, no_face.json()["detail"]) == (422, "Kein Gesicht erkannt — bitte mittig vor die Kamera.")
    camera["faces"] = [SimpleNamespace(bbox=(200, 100, 120, 140), embedding=np.ones(512), detection_score=0.8)]
    client.post(f"/vision/enroll/{token}/capture", json={"source_id": "cam/door"})
    assert client.post(f"/vision/enroll/{token}/finish", json={}).status_code == 422
    done = client.post(f"/vision/enroll/{token}/finish", json={"name": "Lisa"}).json()
    assert done["message"] == "✓ 2 Posen für „Lisa“ gespeichert"
    assert client.post(f"/vision/enroll/{token}/skip").status_code == 409
    assert client.post("/vision/enroll", json={"face_id": 9999}).status_code == 404
