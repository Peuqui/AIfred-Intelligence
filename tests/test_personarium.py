"""The persons of the vision (lib/personarium.py, /vision/face/*): the open faces one card per crop file and without
a cap (the Reflex Personarium read at most 2000 although it said "no cap"), a card dismissed or assigned with every
event behind it, rename and delete checked and making the recognisers load again (renaming over the API did not),
the embeddings newest first, and recognising the open faces again as a run of the server with its outcome in words.
The face model is a stand-in."""

import asyncio
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from starlette.testclient import TestClient

from aifred.lib import face_enroll, personarium, vision_store
from aifred.lib.user_error import Conflict, Invalid
from aifred.lib.vision_filters import face_recognize
from aifred.lib.vision_store import VisionStore

START = datetime(2026, 11, 4, 14, 0, 0)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> VisionStore:
    """Anna with two embeddings; open faces: a sighting of three unknown events sharing one crop, an unsure face
    guessing Anna, one dismissed and one confirmed (neither open), one without a crop."""
    monkeypatch.setattr(vision_store, "DEFAULT_DB_PATH", tmp_path / "vision.db")
    monkeypatch.setattr(personarium, "_rematch", personarium.Rematch())
    store = VisionStore()
    anna = store.add_face("Anna")
    for quality in (0.6, 0.9):
        store.add_embedding(anna, np.ones(512, dtype=np.float32) * quality, quality_score=quality)
    faces: list[tuple[str, int | None, dict[str, Any]]] = [
        ("face_unknown", None, {"crop_url": "/_upload/face_crops/a.jpg"}),
        ("face_unknown", None, {"crop_url": "/_upload/face_crops/a.jpg"}),
        ("face_unknown", None, {"crop_url": "/_upload/face_crops/a.jpg"}),
        ("face_unsure", anna, {"crop_url": "/_upload/face_crops/b.jpg", "matched_name": "Anna"}),
        ("face_unknown", None, {"crop_url": "/_upload/face_crops/c.jpg", "untagged_dismissed": True}),
        ("face_unsure", anna, {"crop_url": "/_upload/face_crops/d.jpg", "identity_confirmed": True}),
        ("face_unknown", None, {}),
    ]
    for minute, (kind, face, classification) in enumerate(faces):
        store.add_event("cam/door", kind, timestamp=START + timedelta(minutes=minute), face_id=face, classification=classification)
    return store


def _epoch() -> int:
    return face_recognize._enrollment_epoch


def _name_of(store: VisionStore, face_id: int) -> str:
    face = store.get_face_by_id(face_id)
    assert face is not None
    return str(face["name"])


def _face_of(store: VisionStore, event_id: int) -> int | None:
    event = store.get_event(event_id)
    assert event is not None
    face: int | None = event["face_id"]
    return face


def test_the_open_faces_are_one_card_per_crop_file_with_every_event_behind_it(store: VisionStore) -> None:
    cards = personarium.open_faces()
    assert [(card.crop_url.rsplit("/", 1)[1], len(card.member_ids), card.event_type, card.matched_name) for card in cards] == [
        ("b.jpg", 1, "face_unsure", "Anna"), ("a.jpg", 3, "face_unknown", "")]
    assert cards[1].id == max(cards[1].member_ids)      # the newest event leads


def test_the_open_faces_have_no_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vision_store, "DEFAULT_DB_PATH", tmp_path / "vision.db")
    store = VisionStore()
    for number in range(260):
        store.add_event("cam/door", "face_unknown", timestamp=START + timedelta(seconds=number),
                        classification={"crop_url": f"/_upload/face_crops/{number}.jpg"})
    assert len(personarium.open_faces()) == 260


def test_a_card_is_dismissed_with_every_event_behind_it(store: VisionStore) -> None:
    sighting = personarium.open_faces()[1]
    assert personarium.dismiss([sighting.id]) == 3
    assert [card.crop_url for card in personarium.open_faces()] == ["/_upload/face_crops/b.jpg"]
    with pytest.raises(KeyError):
        personarium.dismiss([sighting.id])           # no longer open


def test_rename_and_delete_are_checked_and_make_the_recognisers_load_again(store: VisionStore) -> None:
    anna = store.get_face_by_name("Anna")
    assert anna is not None
    store.add_face("Bert")
    before = _epoch()
    personarium.rename(int(anna["id"]), "  Anna B.  ")
    assert _name_of(store, int(anna["id"])) == "Anna B."
    assert _epoch() == before + 1
    with pytest.raises(Invalid):
        personarium.rename(int(anna["id"]), "  ")
    with pytest.raises(Conflict):
        personarium.rename(int(anna["id"]), "Bert")
    personarium.rename(int(anna["id"]), "Anna B.")      # its own name is no conflict
    with pytest.raises(KeyError):
        personarium.rename(9999, "X")
    assert personarium.delete(int(anna["id"])) == ("Anna B.", 2)
    with pytest.raises(KeyError):
        personarium.delete(int(anna["id"]))


def test_the_embeddings_newest_first_and_one_deleted(store: VisionStore) -> None:
    anna = store.get_face_by_name("Anna")
    assert anna is not None
    listed = personarium.embeddings(int(anna["id"]))
    assert [round(row["quality"], 1) for row in listed] == [0.9, 0.6] and "embedding" not in listed[0]
    personarium.delete_embedding(listed[0]["id"])
    assert len(personarium.embeddings(int(anna["id"]))) == 1
    with pytest.raises(KeyError):
        personarium.delete_embedding(listed[0]["id"])
    with pytest.raises(KeyError):
        personarium.embeddings(9999)


def test_assigning_a_card_learns_its_newest_event_and_assigns_the_others(store: VisionStore, monkeypatch: pytest.MonkeyPatch) -> None:
    sighting = personarium.open_faces()[1]
    learned: list[tuple[int, dict[str, Any]]] = []

    async def enroll(event_id: int, **who: Any) -> dict[str, Any]:
        learned.append((event_id, who))
        return {"face_id": store.get_or_create_face(who["name"]), "name": who["name"], "is_new": True, "quality": 0.81,
                "embedding_skipped": False, "similarity": 0.0, "siblings_resolved": 2}

    monkeypatch.setattr(face_enroll, "enroll_face_from_event", enroll)
    done = asyncio.run(personarium.assign(sighting.id, name="Carla"))
    assert learned == [(sighting.id, {"name": "Carla"})]
    carla = store.get_face_by_name("Carla")
    assert carla is not None
    others = [member for member in sighting.member_ids if member != sighting.id]
    assert all(_face_of(store, member) == carla["id"] for member in others)
    assert done.message("de") == "✓ Gesicht als „Carla“ gelernt (Qualität 0,81) — 2 weitere Aufnahmen des Vorkommnisses mit zugeordnet"
    with pytest.raises(KeyError):
        asyncio.run(personarium.assign(9999, name="X"))


def test_recognising_again_is_one_run_at_a_time_and_tells_its_outcome(store: VisionStore, monkeypatch: pytest.MonkeyPatch) -> None:
    async def rematch() -> dict[str, Any]:
        await asyncio.sleep(0)
        return {"checked": 3, "resolved": 2, "by_name": {"Anna": 2}}

    monkeypatch.setattr(face_enroll, "rematch_untagged_faces", rematch)

    async def scenario() -> str:
        run = personarium.start_rematch()
        running = personarium.rematch().message("de")
        with pytest.raises(personarium.RematchRunning):
            personarium.start_rematch()
        await run
        return running

    assert personarium.rematch().message("de") == ""
    assert asyncio.run(scenario()) == "Erkenne die offenen Gesichter neu …"
    assert personarium.rematch().message("de") == "✓ 2 von 3 Aufnahmen erkannt und zugeordnet (Anna: 2) — 1 bleibt unzugeordnet"
    assert personarium.rematch().message("en") == "✓ 2 of 3 captures recognised and assigned (Anna: 2) — 1 stays unassigned"


@pytest.fixture
def client(store: VisionStore) -> Iterator[TestClient]:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import sign_username
    from aifred.lib.browser_storage import USERNAME_COOKIE_NAME

    with TestClient(api_app) as test_client:
        test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        yield test_client


def test_the_persons_and_the_open_faces_over_the_api(client: TestClient, store: VisionStore) -> None:
    anna = store.get_face_by_name("Anna")
    assert anna is not None
    listed = client.get("/vision/face/list").json()["faces"]
    assert [(face["name"], face["embedding_count"], face["last_seen_display"]) for face in listed] == [("Anna", 2, "04.11.2026 14:05")]
    before = _epoch()
    assert client.post(f"/vision/face/{anna['id']}/rename", json={"name": "Anna B."}).status_code == 200
    assert _epoch() == before + 1
    store.add_face("Bert")
    assert client.post(f"/vision/face/{anna['id']}/rename", json={"name": "Bert"}).status_code == 409
    assert client.get("/vision/face/9999/embeddings").status_code == 404
    cards = client.get("/vision/face/open").json()
    assert [len(card["member_ids"]) for card in cards] == [1, 3]
    assert client.post(f"/vision/face/open/{cards[1]['id']}/assign", json={}).status_code == 422
    assert client.post(f"/vision/face/open/{cards[1]['id']}/assign", json={"face_id": 1, "name": "X"}).status_code == 422
    assert client.post("/vision/face/open/dismiss", json={"cards": [9999]}).status_code == 404
    assert client.post("/vision/face/open/dismiss", json={"cards": [cards[1]["id"]]}).json() == {"message": "✓ 3 Aufnahmen verworfen"}
    deleted = client.delete(f"/vision/face/{anna['id']}").json()
    assert deleted["message"] == "✓ „Anna B.“ gelöscht (2 Embeddings)"


def test_recognising_again_checks_exactly_the_events_behind_the_open_cards(store: VisionStore, monkeypatch: pytest.MonkeyPatch) -> None:
    # 08.11.2026: it checked only faces without a person (never the unsure ones the cards show), counted faces without
    # a crop the page does not show, stopped at 2000; its "x of y" did not match the cards.
    from types import SimpleNamespace

    from aifred.lib.vision_filters import face_detect

    checked: list[int] = []

    async def detect(event: dict[str, Any], _detector: object) -> SimpleNamespace:
        checked.append(int(event["id"]))
        return SimpleNamespace(embedding=np.zeros(512, dtype=np.float32))

    class Unsure:
        def match(self, _embedding: object) -> SimpleNamespace:
            return SimpleNamespace(confidence_band="unsure", face_id=0, name="")

    monkeypatch.setattr(face_enroll, "_best_detection_for_event", detect)
    monkeypatch.setattr(face_enroll, "_shared_recognizer", Unsure)
    monkeypatch.setattr(face_detect, "get_default_detector", object)
    result = asyncio.run(face_enroll.rematch_untagged_faces())
    assert sorted(checked) == sorted(event for card in personarium.open_faces() for event in card.member_ids)
    assert result == {"checked": 4, "resolved": 0, "by_name": {}}      # the sighting of three and the unsure face
