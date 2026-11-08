"""The file requester's server (lib/api/files.py, VueFinder's contract) on a temporary area: listing, folders,
rename, delete (a link loses only itself, a folder its content without following links), copy and move, upload
without overwriting, search with VueFinder's size filter, links only to folders inside the area, nothing outside
the area or beyond its actions."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from aifred.lib import file_areas
from aifred.lib.file_areas import ACTIONS, FileArea


@pytest.fixture
def area(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "sandbox"
    (root / "Hörbücher" / "Kafka").mkdir(parents=True)
    (root / "Hörbücher" / "Kafka" / "Prozess.mp3").write_bytes(b"x" * 2048)
    (root / "Musik").mkdir()
    (root / "Musik" / "gross.flac").write_bytes(b"y" * (11 << 20))
    outside = tmp_path / "draussen"
    outside.mkdir()
    (outside / "geheim.txt").write_text("geheim", encoding="utf-8")
    (root / "Ausgang").symlink_to(outside)          # an admin's mount: may point out, may not be the target of a new link
    monkeypatch.setitem(file_areas.AREAS, "test", FileArea(lambda: str(root), ACTIONS))
    monkeypatch.setitem(file_areas.AREAS, "lesen", FileArea(lambda: str(root), frozenset({"preview", "download", "search"})))
    yield root


@pytest.fixture
def client(area: Path) -> Iterator[TestClient]:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import USERNAME_COOKIE_NAME, sign_username

    with TestClient(api_app) as test_client:
        test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        yield test_client


def _names(listing: dict) -> list[str]:
    return [entry["basename"] for entry in listing["files"]]


def test_a_folder_lists_folders_first_with_vuefinders_fields(client: TestClient) -> None:
    root = client.get("/files/test").json()
    assert root["dirname"] == "test://" and root["storages"] == ["test"]
    assert _names(root) == ["Ausgang", "Hörbücher", "Musik"]
    kafka = client.get("/files/test", params={"path": "test://Hörbücher/Kafka"}).json()
    entry = kafka["files"][0]
    assert (entry["path"], entry["dir"], entry["type"], entry["extension"], entry["file_size"]) == (
        "test://Hörbücher/Kafka/Prozess.mp3", "test://Hörbücher/Kafka", "file", "mp3", 2048,
    )
    assert entry["mime_type"] == "audio/mpeg"


def test_folders_are_made_renamed_and_never_overwritten(client: TestClient, area: Path) -> None:
    made = client.post("/files/test/create-folder", json={"path": "test://", "name": "Neu"}).json()
    assert "Neu" in _names(made) and (area / "Neu").is_dir()
    assert client.post("/files/test/create-folder", json={"path": "test://", "name": "Neu"}).status_code == 409
    assert client.post("/files/test/create-folder", json={"path": "test://", "name": "a/b"}).status_code == 422
    renamed = client.post("/files/test/rename", json={"path": "test://", "item": "test://Neu", "name": "Hörspiele"}).json()
    assert "Hörspiele" in _names(renamed) and not (area / "Neu").exists()


def test_deleting_a_link_keeps_its_target_and_a_folder_goes_with_its_content(client: TestClient, area: Path) -> None:
    result = client.post("/files/test/delete", json={"path": "test://", "items": [
        {"path": "test://Ausgang", "type": "dir"}, {"path": "test://Hörbücher", "type": "dir"},
    ]}).json()
    assert sorted(entry["basename"] for entry in result["deleted"]) == ["Ausgang", "Hörbücher"]
    assert _names(result) == ["Musik"]
    assert (area.parent / "draussen" / "geheim.txt").read_text(encoding="utf-8") == "geheim"
    assert client.post("/files/test/delete", json={"path": "test://", "items": [{"path": "test://", "type": "dir"}]}).status_code == 422


def test_copy_and_move_stay_in_the_area_and_refuse_a_name_in_use(client: TestClient, area: Path) -> None:
    client.post("/files/test/copy", json={"sources": ["test://Hörbücher/Kafka"], "destination": "test://Musik"})
    assert (area / "Musik" / "Kafka" / "Prozess.mp3").is_file() and (area / "Hörbücher" / "Kafka").is_dir()
    assert client.post("/files/test/copy", json={"sources": ["test://Hörbücher/Kafka"], "destination": "test://Musik"}).status_code == 409
    client.post("/files/test/move", json={"sources": ["test://Musik/gross.flac"], "destination": "test://Hörbücher"})
    assert (area / "Hörbücher" / "gross.flac").is_file() and not (area / "Musik" / "gross.flac").exists()
    assert client.post("/files/test/move", json={"sources": ["test://Hörbücher"], "destination": "test://Hörbücher/Kafka"}).status_code == 422


def test_an_upload_lands_in_the_folder_and_overwrites_nothing(client: TestClient, area: Path) -> None:
    sent = client.post("/files/test/upload", data={"path": "test://Musik"}, files={"file": ("neu.ogg", b"ogg")})
    assert sent.status_code == 200 and (area / "Musik" / "neu.ogg").read_bytes() == b"ogg"
    again = client.post("/files/test/upload", data={"path": "test://Musik"}, files={"file": ("neu.ogg", b"anders")})
    assert again.status_code == 409 and (area / "Musik" / "neu.ogg").read_bytes() == b"ogg"


def test_search_finds_names_below_and_filters_by_size(client: TestClient) -> None:
    deep = client.get("/files/test/search", params={"path": "test://", "filter": "PROZ", "deep": "1"}).json()
    assert [entry["path"] for entry in deep["files"]] == ["test://Hörbücher/Kafka/Prozess.mp3"]
    flat = client.get("/files/test/search", params={"path": "test://", "filter": "proz"}).json()
    assert flat["files"] == []
    large = client.get("/files/test/search", params={"path": "test://", "filter": "", "deep": "1", "size": "large"}).json()
    assert [entry["basename"] for entry in large["files"]] == ["gross.flac"]


def test_a_link_points_only_to_a_folder_inside_the_area(client: TestClient, area: Path) -> None:
    made = client.post("/files/test/symlink", json={"path": "test://Musik", "name": "Kafka-Link", "target": "test://Hörbücher/Kafka"}).json()
    assert "Kafka-Link" in _names(made)
    assert (area / "Musik" / "Kafka-Link").resolve() == (area / "Hörbücher" / "Kafka").resolve()
    out = client.post("/files/test/symlink", json={"path": "test://", "name": "Raus", "target": "test://Ausgang"})
    assert out.status_code == 422 and not (area / "Raus").exists()      # a folder reached through a link leading out


def test_nothing_outside_the_area_and_no_action_it_does_not_allow(client: TestClient) -> None:
    assert client.get("/files/test", params={"path": "test://../draussen"}).status_code == 422
    assert client.get("/files/test", params={"path": "lesen://Musik"}).status_code == 422     # another area's path
    assert client.get("/files/nirgends").status_code == 404
    assert client.post("/files/lesen/create-folder", json={"path": "lesen://", "name": "X"}).status_code == 403
    assert client.get("/files/lesen/download", params={"path": "lesen://Hörbücher/Kafka/Prozess.mp3"}).content == b"x" * 2048
    preview = client.get("/files/lesen/preview", params={"path": "lesen://Hörbücher/Kafka/Prozess.mp3"})
    assert preview.headers["content-type"] == "audio/mpeg" and preview.headers["content-disposition"].startswith("inline")


def test_the_requester_learns_root_and_actions_of_the_area(client: TestClient, area: Path) -> None:
    assert client.get("/files/lesen/info").json() == {"root": str(area), "actions": ["download", "preview", "search"]}
