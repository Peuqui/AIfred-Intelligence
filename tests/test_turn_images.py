"""Images of a turn (lib/turn_images.py) and the image endpoints of the session API: only uploads of
the user's own session count, the history keeps the URL and never the image, a model that sees
gets the images first and ONE text part."""

from __future__ import annotations

import io
import itertools
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image
from starlette.testclient import TestClient

from aifred.lib import session_events, session_storage, turn_images, vision_utils
from aifred.lib.api.app import api_app
from aifred.lib.auth import sign_username
from aifred.lib.auth import USERNAME_COOKIE_NAME

MINE = "a" * 32
THEIRS = "b" * 32


@pytest.fixture
def uploads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "upload" / "images"
    monkeypatch.setattr(vision_utils, "UPLOAD_IMAGES_DIR", directory)
    for session, name in ((MINE, "hund.jpg"), (THEIRS, "katze.jpg")):
        (directory / session).mkdir(parents=True)
        (directory / session / name).write_bytes(b"\xff\xd8bild")
    return directory


def test_only_uploads_of_the_own_session_are_images_of_a_message(uploads: Path) -> None:
    [image] = turn_images.session_images(MINE, [f"/_upload/images/{MINE}/hund.jpg"])
    assert (image.name, image.url, image.path) == ("hund.jpg", f"/_upload/images/{MINE}/hund.jpg", uploads / MINE / "hund.jpg")
    for url in (
        f"/_upload/images/{THEIRS}/katze.jpg",          # another session's upload
        f"/_upload/images/{MINE}/fehlt.jpg",            # gone
        f"/_upload/images/{MINE}/../{THEIRS}/katze.jpg",
        "/_upload/vigilantia/motion/cam/frame.jpg",     # not an upload
    ):
        with pytest.raises(ValueError):
            turn_images.session_images(MINE, [url])


def test_the_history_keeps_the_url_and_the_bubble_the_thumbnail(uploads: Path) -> None:
    images = turn_images.session_images(MINE, [f"/_upload/images/{MINE}/hund.jpg"])
    history = turn_images.history_text("Was ist das?", images)
    assert history.startswith("Was ist das?\n\n") and f"/_upload/images/{MINE}/hund.jpg" in history
    assert "\n" not in turn_images.history_text("", images)          # image only: the anchor alone
    bubble = turn_images.bubble_text("", images, "de")
    assert '<img src="/_upload/images/' in bubble and bubble.endswith("📷 hund.jpg")


def test_a_model_that_sees_gets_the_images_first_and_one_text_part(uploads: Path) -> None:
    images = turn_images.session_images(MINE, [f"/_upload/images/{MINE}/hund.jpg"])
    question = turn_images.seeing_question(images, "Was ist das?", "[Sa 07.11.2026 09:30]", "de")
    assert "Was ist das?" in question and f"/_upload/images/{MINE}/hund.jpg" in question
    assert turn_images.seeing_question(images, "", "[stamp]", "de").strip()          # the default question
    content = turn_images.seeing_content(images, question, no_think=True)
    assert [part["type"] for part in content] == ["text", "image_url", "text"]
    assert content[0]["text"] == "/no_think" and content[2]["text"] == question
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert [part["type"] for part in turn_images.seeing_content(images, question, no_think=False)] == ["image_url", "text"]


# ── The endpoints ────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path: Path, uploads: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(session_storage, "SESSION_DIR", tmp_path / "sessions")
    session_storage._session_meta_cache.clear()
    session_storage.create_empty_session(MINE, owner="ki")
    session_storage.create_empty_session(THEIRS, owner="lord")
    test_client = TestClient(api_app)
    test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
    yield test_client
    session_storage._session_meta_cache.clear()
    for session in (MINE, THEIRS):
        session_events._channels.pop(session, None)


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), "orange").save(buffer, format="PNG")
    return buffer.getvalue()


def test_an_upload_lands_in_the_sessions_folder_and_can_be_taken_off(client: TestClient, uploads: Path) -> None:
    response = client.post(f"/sessions/{MINE}/images", files=[("files", ("foto.png", _png(), "image/png"))])
    assert response.status_code == 200
    [stored] = response.json()
    assert stored["url"].startswith(f"/_upload/images/{MINE}/foto_") and (uploads / MINE / stored["name"]).is_file()
    assert client.delete(f"/sessions/{MINE}/images/{stored['name']}").status_code == 204
    assert not (uploads / MINE / stored["name"]).exists()
    assert client.delete(f"/sessions/{MINE}/images/{stored['name']}").status_code == 404


def test_uploads_and_deletions_stay_in_the_users_own_sessions(client: TestClient, uploads: Path) -> None:
    files = [("files", ("foto.png", _png(), "image/png"))]
    assert client.post(f"/sessions/{THEIRS}/images", files=files).status_code == 404
    assert client.delete(f"/sessions/{THEIRS}/images/katze.jpg").status_code == 404
    assert client.delete(f"/sessions/{MINE}/images/..%2F{THEIRS}%2Fkatze.jpg").status_code == 404
    assert (uploads / THEIRS / "katze.jpg").is_file()
    assert client.post(f"/sessions/{MINE}/images", files=[("files", ("notiz.txt", b"x", "text/plain"))]).status_code == 422


def test_a_message_names_only_images_of_its_session(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.lib import turn_service

    sent: list[turn_service.TurnRequest] = []

    def submit(request: turn_service.TurnRequest) -> bool:
        sent.append(request)
        return False

    monkeypatch.setattr(turn_service, "submit_turn", submit)
    own = f"/_upload/images/{MINE}/hund.jpg"
    assert client.post(f"/sessions/{MINE}/messages", json={"text": "", "images": [own]}).status_code == 202
    assert sent[0].images == (own,)
    foreign = f"/_upload/images/{THEIRS}/katze.jpg"
    assert client.post(f"/sessions/{MINE}/messages", json={"text": "x", "images": [foreign]}).status_code == 422
    assert client.post(f"/sessions/{MINE}/messages", json={"text": " ", "images": []}).status_code == 422


# ── Turning and cutting an uploaded image ────────────────────────────────


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each save gets its own millisecond: two saves within one share the name."""
    ticks = itertools.count()
    monkeypatch.setattr(vision_utils, "filename_timestamp", lambda _now: f"2026-11-07_10-00-00_{next(ticks):03d}")


def _halves(kind: str = "PNG") -> bytes:
    """40 × 30, the left half red, the right half blue: where red ends up tells the direction."""
    picture = Image.new("RGB", (40, 30), "blue")
    picture.paste((255, 0, 0), (0, 0, 20, 30))
    buffer = io.BytesIO()
    picture.save(buffer, format=kind)
    return buffer.getvalue()


def _upload(client: TestClient, content: bytes, filename: str = "foto.png") -> dict[str, str]:
    response = client.post(f"/sessions/{MINE}/images", files=[("files", (filename, content, "image/png"))])
    assert response.status_code == 200
    uploaded: dict[str, str] = response.json()[0]
    return uploaded


def _picture(uploads: Path, name: str) -> Image.Image:
    return Image.open(uploads / MINE / name).convert("RGB")


def test_turning_right_puts_the_left_edge_on_top_under_a_new_name(client: TestClient, uploads: Path, clock: None) -> None:
    before = _upload(client, _halves())
    response = client.post(f"/sessions/{MINE}/images/{before['name']}/rotate", json={"clockwise": True})
    assert response.status_code == 200
    after = response.json()
    assert after["name"] != before["name"] and not (uploads / MINE / before["name"]).exists()
    assert after["url"] == f"/_upload/images/{MINE}/{after['name']}"
    assert vision_utils.upload_stem(after["name"]) == "foto" and after["name"].endswith(".png")   # the name does not grow
    turned = _picture(uploads, after["name"])
    assert turned.size == (30, 40)
    assert turned.getpixel((15, 5)) == (255, 0, 0) and turned.getpixel((15, 35)) == (0, 0, 255)
    back = client.post(f"/sessions/{MINE}/images/{after['name']}/rotate", json={"clockwise": False}).json()
    again = _picture(uploads, back["name"])
    assert again.size == (40, 30) and again.getpixel((5, 15)) == (255, 0, 0)


def test_cutting_keeps_the_part_of_the_box_and_the_whole_box_cuts_nothing(client: TestClient, uploads: Path, clock: None) -> None:
    before = _upload(client, _halves())
    whole = {"x": 0.2, "y": 0, "width": 99.7, "height": 100}
    same = client.post(f"/sessions/{MINE}/images/{before['name']}/crop", json=whole).json()
    assert same["name"] == before["name"] and (uploads / MINE / before["name"]).is_file()
    right_half = {"x": 50, "y": 0, "width": 50, "height": 100}
    after = client.post(f"/sessions/{MINE}/images/{before['name']}/crop", json=right_half).json()
    assert not (uploads / MINE / before["name"]).exists() and after["name"].endswith(".jpg")
    cut = _picture(uploads, after["name"])
    assert cut.size == (20, 30)
    pixel = cut.getpixel((10, 15))
    assert isinstance(pixel, tuple)                                                # RGB, not one band
    red, green, blue = pixel
    assert blue > 200 and red < 50 and green < 50                                  # JPEG: nearly the pure blue


def test_only_own_images_with_a_box_inside_the_image_are_edited(client: TestClient, uploads: Path) -> None:
    before = _upload(client, _halves())
    url = f"/sessions/{MINE}/images/{before['name']}/crop"
    for box in ({"x": 0, "y": 0, "width": 0, "height": 50}, {"x": 100, "y": 0, "width": 10, "height": 10}):
        assert client.post(url, json=box).status_code == 422
    assert client.post(f"/sessions/{THEIRS}/images/katze.jpg/rotate", json={"clockwise": True}).status_code == 404
    assert client.post(f"/sessions/{MINE}/images/fehlt.png/rotate", json={"clockwise": True}).status_code == 404
    assert (uploads / THEIRS / "katze.jpg").is_file() and (uploads / MINE / before["name"]).is_file()


def test_an_edit_within_the_millisecond_of_the_save_keeps_the_image(
    client: TestClient, uploads: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vision_utils, "filename_timestamp", lambda _now: "2026-11-07_10-00-00_000")
    before = _upload(client, _halves())
    after = client.post(f"/sessions/{MINE}/images/{before['name']}/rotate", json={"clockwise": True}).json()
    assert after["name"] == before["name"] and _picture(uploads, after["name"]).size == (30, 40)
