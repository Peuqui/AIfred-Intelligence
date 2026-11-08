"""The vision's events (lib/casus.py, VisionStore, api/casus.py): list, count, slideshow ids and delete-by-filter
treat the same events as matching (07.11.2026: "only unassigned" listed the unsure faces, but counted and deleted
without them), one row per occurrence counted in SQL (the Reflex Casus grouped at most 5000 events in Python and
cut the rest silently), the type filter with persons, vehicles and animals (it had none of them), the film of an
occurrence oldest first, a person assigned only to a face event, and the signal of a stored event across threads."""

import asyncio
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from aifred.lib import casus, vision_store
from aifred.lib.casus import EventFilter
from aifred.lib.vision_store import VisionStore

START = datetime(2026, 11, 4, 14, 0, 0)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> VisionStore:
    """Two persons; an occurrence of three person events and one of two motion events (the last with a tele picture),
    a vehicle and an animal on their own, a known, an unsure (the recognition guesses a person) and an unknown face."""
    monkeypatch.setattr(vision_store, "DEFAULT_DB_PATH", tmp_path / "vision.db")
    store = VisionStore()
    anna, bert = store.add_face("Anna"), store.add_face("Bert")
    events = [
        ("person", "a", None, {}), ("person", "a", None, {}), ("person", "a", None, {}),
        ("motion", "b", None, {}), ("motion", "b", None, {"zoom_frame_path": "/tele.jpg"}),
        ("vehicle", "", None, {}), ("animal", "", None, {}),
        ("face_known", "", anna, {}), ("face_unsure", "", bert, {}), ("face_unknown", "", None, {}),
    ]
    for minute, (kind, cluster, face, classification) in enumerate(events):
        store.add_event("cam/garden", kind, timestamp=START + timedelta(minutes=minute), frame_path=f"/f{minute}.jpg",
                        face_id=face, cluster_id=cluster, classification=classification)
    return store


def _types(rows: list[dict]) -> list[str]:
    return [row["event_type"] for row in rows]


def _face_of(store: VisionStore, event_id: int) -> int | None:
    event = store.get_event(event_id)
    assert event is not None
    face: int | None = event["face_id"]
    return face


def test_unassigned_is_the_same_set_for_list_count_ids_and_delete(store: VisionStore) -> None:
    unconfirmed = EventFilter(face="unknown").query()
    listed = store.list_events_with_summary(**unconfirmed)
    assert set(_types(listed)) == {"person", "motion", "vehicle", "animal", "face_unsure", "face_unknown"}
    assert store.count_events(**unconfirmed) == len(listed) == len(store.list_event_ids(**unconfirmed))
    assert store.delete_events_filtered(**unconfirmed) == len(listed)
    assert _types(store.list_events_with_summary()) == ["face_known"]


def test_grouped_lists_one_row_per_occurrence_with_its_newest_event_and_counts_them(store: VisionStore) -> None:
    rows, total = casus.page(EventFilter(), grouped=True, offset=0, limit=50)
    assert total == len(rows) == 7     # 2 occurrences + 5 events on their own
    people = next(row for row in rows if row["cluster_id"] == "a")
    assert people["cluster_member_count"] == 3 and people["timestamp"] == (START + timedelta(minutes=2)).isoformat(timespec="microseconds")
    assert next(row for row in rows if row["event_type"] == "vehicle")["cluster_member_count"] == 0
    flat, flat_total = casus.page(EventFilter(), grouped=False, offset=0, limit=50)
    assert flat_total == 10 and {row["cluster_member_count"] for row in flat} == {0}


def test_pages_neither_overlap_nor_skip_events_of_the_same_moment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vision_store, "DEFAULT_DB_PATH", tmp_path / "vision.db")
    store = VisionStore()
    for _ in range(7):
        store.add_event("cam/a", "motion", timestamp=START)          # a burst: the same timestamp
    seen = [row["id"] for at in range(0, 7, 3) for row in casus.page(EventFilter(), grouped=False, offset=at, limit=3)[0]]
    assert sorted(seen) == sorted(set(seen)) and len(seen) == 7


@pytest.mark.parametrize(("kind", "expected"), [("person", 3), ("vehicle", 1), ("animal", 1), ("face", 3), ("vlm", 0)])
def test_the_type_filter_knows_persons_vehicles_and_animals(store: VisionStore, kind: str, expected: int) -> None:
    assert casus.page(EventFilter(type=kind), grouped=False, offset=0, limit=50)[1] == expected


@pytest.mark.parametrize("bad", [EventFilter(type="ufo"), EventFilter(face="Anna")])
def test_an_unknown_filter_is_refused(bad: EventFilter) -> None:
    with pytest.raises(ValueError):
        bad.query()


def test_the_film_runs_oldest_first_and_knows_its_tele_pictures(store: VisionStore) -> None:
    motion = [row for row in store.list_events_with_summary() if row["cluster_id"] == "b"]
    newest, oldest = motion[0]["id"], motion[1]["id"]
    assert casus.film(newest) == [(oldest, False), (newest, True)]
    vehicle = next(row["id"] for row in store.list_events_with_summary() if row["event_type"] == "vehicle")
    assert casus.film(vehicle) == [(vehicle, False)]
    with pytest.raises(KeyError):
        casus.film(9999)


def test_a_person_is_assigned_only_to_a_face_event_and_only_if_it_exists(store: VisionStore) -> None:
    rows = {row["event_type"]: row for row in store.list_events_with_summary()}
    anna = store.get_face_by_name("Anna")
    assert anna is not None
    casus.assign_face(rows["face_unknown"]["id"], int(anna["id"]))
    assert _face_of(store, rows["face_unknown"]["id"]) == anna["id"]
    casus.assign_face(rows["face_unknown"]["id"], None)
    assert _face_of(store, rows["face_unknown"]["id"]) is None
    with pytest.raises(ValueError):
        casus.assign_face(rows["face_unknown"]["id"], 9999)
    with pytest.raises(ValueError):
        casus.assign_face(rows["vehicle"]["id"], int(anna["id"]))
    with pytest.raises(KeyError):
        casus.assign_face(9999, None)


def test_an_event_stored_in_another_thread_wakes_a_follower(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vision_store, "DEFAULT_DB_PATH", tmp_path / "vision.db")

    async def follow() -> int:
        followed = vision_store.stored_events()
        step = asyncio.ensure_future(followed.__anext__())
        await asyncio.sleep(0)            # the follower waits now
        writer = threading.Thread(target=lambda: VisionStore().add_event("cam/a", "motion"))
        writer.start()
        await asyncio.wait_for(step, 5)
        writer.join()
        await followed.aclose()
        return len(vision_store._followers)

    assert asyncio.run(follow()) == 0     # the follower leaves with its consumer


@pytest.fixture
def client(store: VisionStore) -> Iterator[TestClient]:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import sign_username
    from aifred.lib.browser_storage import USERNAME_COOKIE_NAME

    with TestClient(api_app) as test_client:
        test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        yield test_client


def test_the_page_the_slideshow_position_and_the_choices_over_the_api(client: TestClient) -> None:
    page = client.get("/vision/casus/events", params={"grouped": "true"}).json()
    assert page["total"] == 7 and page["page_size"] == 50 and "frame_path" not in page["events"][0]
    newest = page["events"][0]
    assert (newest["event_type"], newest["has_frame"], newest["has_zoom"]) == ("face_unknown", True, False)
    second = client.get("/vision/casus/events", params={"offset": 1, "limit": 1}).json()
    assert [event["event_type"] for event in second["events"]] == ["face_unsure"] and second["total"] == 10
    assert client.get("/vision/casus/events", params={"type": "ufo"}).status_code == 422
    offered = client.get("/vision/casus/choices").json()
    assert [kind["id"] for kind in offered["types"]] == list(casus.TYPE_FILTERS)
    assert [source["id"] for source in offered["sources"]] == ["cam/garden"]
    assert [face["label"] for face in offered["faces"]] == ["Anna", "Bert"]


def test_assigning_and_deleting_over_the_api(client: TestClient, store: VisionStore) -> None:
    rows = {row["event_type"]: row["id"] for row in store.list_events_with_summary()}
    anna = store.get_face_by_name("Anna")
    assert anna is not None
    assert client.put(f"/vision/casus/events/{rows['face_unknown']}/face", json={"face_id": anna["id"]}).status_code == 204
    assert client.put(f"/vision/casus/events/{rows['vehicle']}/face", json={"face_id": anna["id"]}).status_code == 422
    assert client.put("/vision/casus/events/9999/face", json={"face_id": None}).status_code == 404
    assert client.get("/vision/casus/events/9999/film").status_code == 404
    assert client.delete(f"/vision/casus/events/{rows['vehicle']}").status_code == 204
    assert client.delete(f"/vision/casus/events/{rows['vehicle']}").status_code == 404
    deleted = client.post("/vision/casus/events/delete", json={"type": "person"}).json()
    assert deleted == {"deleted": 3}
    assert client.post("/vision/casus/events/delete", json={"face": "x"}).status_code == 422
    assert store.count_events() == 6


# ── The image model on events: runs of the server, one at a time each ─────────────────────────────────────────


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """No run before the test."""
    monkeypatch.setattr(casus, "_analysis", casus.Analysis())
    monkeypatch.setattr(casus, "_describe_all", casus.DescribeAll())


def test_one_event_at_a_time_goes_to_the_image_model_and_a_failure_is_kept(
    store: VisionStore, runs: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aifred.lib import vision_event_analysis

    rows = {row["event_type"]: row["id"] for row in store.list_events_with_summary()}
    asked: list[int] = []

    async def analyse(event_id: int) -> str:
        asked.append(event_id)
        await asyncio.sleep(0)
        if event_id == rows["animal"]:
            raise RuntimeError("the model did not answer")
        return "ein Fuchs"

    monkeypatch.setattr(vision_event_analysis, "analyze_event_with_vlm", analyse)

    async def scenario() -> list[casus.Analysis]:
        seen = []
        run = casus.start_analysis(rows["vehicle"])
        seen.append(casus.analysis())
        with pytest.raises(casus.RunInProgress):
            casus.start_analysis(rows["animal"])
        await run
        seen.append(casus.analysis())
        await casus.start_analysis(rows["animal"])
        seen.append(casus.analysis())
        return seen

    assert asyncio.run(scenario()) == [
        casus.Analysis(running=rows["vehicle"]),
        casus.Analysis(last=rows["vehicle"]),
        casus.Analysis(last=rows["animal"], error="the model did not answer"),
    ]
    assert asked == [rows["vehicle"], rows["animal"]]
    with pytest.raises(KeyError):
        casus.start_analysis(9999)


def test_an_event_without_a_picture_is_not_sent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runs: None) -> None:
    monkeypatch.setattr(vision_store, "DEFAULT_DB_PATH", tmp_path / "vision.db")
    event = VisionStore().add_event("cam/a", "vehicle")
    with pytest.raises(ValueError):
        casus.start_analysis(event)


def test_describing_every_event_reports_its_progress_and_stops_when_asked(runs: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.lib import vision_bulk
    from aifred.lib.vision_bulk import BulkDescribeResult

    seen: list[str] = []

    async def bulk(*, progress_cb: vision_bulk.ProgressCb, cancel_cb: vision_bulk.CancelCb, check_vram: bool) -> BulkDescribeResult:
        assert check_vram
        seen.append(casus.describe_all().message("de"))
        await progress_cb(12, 0, 0)
        seen.append(casus.describe_all().message("de"))
        await progress_cb(12, 4, 1)
        seen.append(casus.describe_all().message("de"))
        casus.cancel_describe_all()
        seen.append(casus.describe_all().message("de"))
        assert await cancel_cb()
        return BulkDescribeResult(total_events=12, total_clusters=4, processed=1, cancelled=True)

    monkeypatch.setattr(vision_bulk, "run_bulk_describe", bulk)

    async def scenario() -> None:
        run = casus.start_describe_all()
        with pytest.raises(casus.RunInProgress):
            casus.start_describe_all()
        await run

    asyncio.run(scenario())
    assert seen == [
        "Prüfe, ob das Bildmodell auf eine Karte passt …",
        "Gruppiere 12 Ereignisse ohne Beschreibung …",
        "Analysiere 1 / 4 Cluster (aus 12 Ereignissen)",
        "Wird abgebrochen …",
    ]
    state = casus.describe_all()
    assert not state.running and state.outcome == "cancelled"
    assert state.message("de") == "Abgebrochen — 1 / 4 Cluster analysiert (0 Fehler)"
    assert state.message("en") == "Cancelled — 1 / 4 clusters analysed (0 failed)"


@pytest.mark.parametrize(("result", "outcome", "message"), [
    ({"total_events": 30, "total_clusters": 5, "processed": 5}, "done", "Fertig — 5 Cluster analysiert"),
    ({"total_events": 30, "total_clusters": 5, "processed": 5, "failed": 2}, "done", "Fertig — 5 Cluster analysiert, 2 Fehler"),
    ({}, "nothing", "Keine Ereignisse ohne Beschreibung"),
    ({"skipped": True}, "busy", "Läuft bereits (Nachtlauf oder Chat) — bitte warten"),
    ({"aborted_vram": True, "vram_message": "VLM braucht 7.735 MiB"}, "no_room", "⚠️ VLM braucht 7.735 MiB"),
])
def test_how_a_run_of_every_event_ended(runs: None, monkeypatch: pytest.MonkeyPatch, result: dict, outcome: str, message: str) -> None:
    from aifred.lib import vision_bulk
    from aifred.lib.vision_bulk import BulkDescribeResult

    async def bulk(**_kwargs: object) -> BulkDescribeResult:
        return BulkDescribeResult(**result)

    monkeypatch.setattr(vision_bulk, "run_bulk_describe", bulk)

    async def scenario() -> None:
        await casus.start_describe_all()

    asyncio.run(scenario())
    assert (casus.describe_all().outcome, casus.describe_all().message("de")) == (outcome, message)


def test_the_runs_over_the_api(client: TestClient, store: VisionStore, runs: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.lib import vision_event_analysis

    rows = {row["event_type"]: row["id"] for row in store.list_events_with_summary()}

    async def analyse(event_id: int) -> str:
        return "ein Auto"

    monkeypatch.setattr(vision_event_analysis, "analyze_event_with_vlm", analyse)
    assert client.get("/vision/casus/runs").json()["describe_all"]["message"] == ""
    started = client.post(f"/vision/casus/events/{rows['vehicle']}/analyze")
    assert started.status_code == 202 and started.json()["analysis"]["running"] == rows["vehicle"]
    assert client.post("/vision/casus/events/9999/analyze").status_code in (404, 409)
    deadline = time.monotonic() + 5
    while client.get("/vision/casus/runs").json()["analysis"]["running"] and time.monotonic() < deadline:
        time.sleep(0.02)
    assert client.get("/vision/casus/runs").json()["analysis"] == {"running": 0, "last": rows["vehicle"], "error": ""}
    assert client.post("/vision/casus/events/9999/analyze").status_code == 404


def test_the_badge_counts_the_last_minutes_and_lists_the_newest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vision_store, "DEFAULT_DB_PATH", tmp_path / "vision.db")
    store = VisionStore()
    now = datetime.now()
    for minutes_ago in (90, 30, 9, 2, 1):
        store.add_event("cam/a", "person", timestamp=now - timedelta(minutes=minutes_ago))
    events, count = casus.recent(10, 2)
    assert count == 3 and len(events) == 2 and events[0]["timestamp"] > events[1]["timestamp"]


def test_the_badge_over_the_api(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.lib import plugin_registry
    from aifred.lib.config import VISION_BADGE_EVENTS, VISION_BADGE_WINDOW_MIN

    monkeypatch.setattr(plugin_registry, "is_plugin_enabled", lambda name: name == "vision")
    badge = client.get("/vision/casus/recent").json()
    assert badge["plugin_enabled"] is True and badge["window_minutes"] == VISION_BADGE_WINDOW_MIN
    assert badge["recent"] == 0                                          # the fixture's events are from 04.11.
    assert len(badge["events"]) == min(VISION_BADGE_EVENTS, 10) and badge["refresh_seconds"] > 0
