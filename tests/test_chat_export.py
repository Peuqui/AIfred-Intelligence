"""The chat export (lib/chat_export.py, POST /api/sessions/{id}/export): one standalone HTML file of a session.

Until 08.11.2026 only the Reflex interface could export (state/_export_mixin.py); the Vue app had nothing. The port
heads a reply with the agent's own name (the old export knew Sokrates and Salomo by name), keeps an embedded sandbox
page sandboxed (without the attribute its script ran in AIfred's origin) and writes the page's /vendor/ libraries
into it (a standalone file has no server behind it).
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from aifred.lib import audio_processing, config, formatting, session_storage, vision_utils
from aifred.lib.api.app import api_app
from aifred.lib.auth import USERNAME_COOKIE_NAME, sign_username
from aifred.lib.chat_export import _download_name, export_session

SESSION = "a" * 32
OTHER = "b" * 32
PLOT = b"\x89PNG\r\n\x1a\nplot"
WAV = b"RIFFwave"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setattr(session_storage, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(config, "SANDBOX_OUTPUT_DIR", tmp_path / "sandbox_output")
    monkeypatch.setattr(config, "VENDOR_DIR", tmp_path / "vendor")
    monkeypatch.setattr(formatting, "_HTML_PREVIEW_DIR", tmp_path / "html_preview")
    monkeypatch.setattr(vision_utils, "UPLOAD_IMAGES_DIR", tmp_path / "images")
    monkeypatch.setattr(audio_processing, "SESSION_AUDIO_DIR", tmp_path / "audio")
    (tmp_path / "sessions").mkdir()
    session_storage._session_meta_cache.clear()

    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "three.min.js").write_text('var THREE={REVISION:"160"};var s="</script>";')
    sandbox = tmp_path / "sandbox_output" / SESSION
    sandbox.mkdir(parents=True)
    (sandbox / "page-1.html").write_text('<script src="/vendor/three.min.js"></script><p id="out">3D</p>')
    (sandbox / "plot-1.png").write_bytes(PLOT)
    foreign = tmp_path / "sandbox_output" / OTHER
    foreign.mkdir()
    (foreign / "page-2.html").write_text("<p>fremd</p>")
    (tmp_path / "images" / SESSION).mkdir(parents=True)
    from PIL import Image
    Image.new("RGB", (4, 4), "red").save(tmp_path / "images" / SESSION / "foto.jpg")
    (tmp_path / "audio" / SESSION).mkdir(parents=True)
    (tmp_path / "audio" / SESSION / "reply.wav").write_bytes(WAV)

    session_storage.create_empty_session(SESSION, owner="ki")
    session_storage.create_empty_session(OTHER, owner="lord")
    reply = (
        "Hier die **Antwort**.\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n"
        "<details><summary>💭 Denkprozess</summary>überlegt</details>\n\n"
        f'<details data-sandbox><summary>📊</summary><iframe src="/_upload/sandbox_output/{SESSION}/page-1.html" '
        'sandbox="allow-scripts" loading="lazy"></iframe></details>\n\n'
        f'<iframe src="/_upload/sandbox_output/{OTHER}/page-2.html" sandbox="allow-scripts"></iframe>\n\n'
        f'<img src="/_upload/sandbox_output/{SESSION}/plot-1.png" alt="Plot" />'
    )
    session_storage.update_chat_data(SESSION, [
        {"role": "user", "content": f'<img src="/_upload/images/{SESSION}/foto.jpg"> Was ist das?'},
        {"role": "assistant", "content": reply, "agent": "pater", "agent_display_name": "Pater Tuck",
         "agent_emoji": "🍺", "metadata": {"audio_urls": [f"/_upload/audio/{SESSION}/reply.wav"],
                                         "playback_rate": "1.25x"}},
    ], llm_history=[])
    session_storage.update_session_title(SESSION, "Ein Bild")
    yield tmp_path
    session_storage._session_meta_cache.clear()


def _page(world: Path) -> str:
    done = export_session(SESSION)
    assert done.messages == 2 and done.download_name == "AIfred-Chat-Ein_Bild.html"
    saved = world / "html_preview" / Path(done.url).name
    return saved.read_text(encoding="utf-8")


def test_the_page_holds_the_chat_with_its_markdown_and_the_bubbles_html(world: Path) -> None:
    page = _page(world)
    assert "🍺 Pater Tuck" in page and "Sokrates" not in page
    assert "<strong>Antwort</strong>" in page and "<table>" in page
    assert "<details><summary>💭 Denkprozess</summary>überlegt</details>" in page
    assert "@@" not in page                                        # every placeholder of the page is filled


def test_images_audio_and_plots_are_in_the_file(world: Path) -> None:
    page = _page(world)
    assert 'src="data:image/jpeg;base64,' in page                  # the user's image
    assert 'src="data:image/png;base64,' in page                   # the sandbox plot
    assert 'src="data:audio/wav;base64,' in page and "this.playbackRate=1.25" in page


def test_a_sandbox_page_comes_with_its_library_and_stays_sandboxed(world: Path) -> None:
    page = _page(world)
    start = page.index("<iframe srcdoc=")
    frame = page[start:page.index(">", page.index('sandbox="', start))]
    assert 'sandbox="allow-scripts"' in frame
    assert "REVISION" in frame and "/vendor/three.min.js" not in frame
    assert "&lt;\\/script&gt;" in frame                            # the library's own "</script>" ends nothing


def test_a_sandbox_page_of_another_session_is_not_embedded(world: Path) -> None:
    page = _page(world)
    assert "fremd" not in page                                      # not read
    assert f'<iframe src="/_upload/sandbox_output/{OTHER}/' not in page   # nor left as a frame that loads it


def test_the_download_name_is_plain_ascii() -> None:
    assert _download_name("Bär: 🎩 Test/1") == "AIfred-Chat-Br__Test1.html"
    assert _download_name("") == "AIfred-Chat.html"


def test_the_api_exports_only_the_users_own_session_with_a_chat(world: Path) -> None:
    client = TestClient(api_app)
    client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
    answer = client.post(f"/sessions/{SESSION}/export")
    assert answer.status_code == 200 and answer.json()["url"].startswith("/_upload/html_preview/")
    assert client.post(f"/sessions/{OTHER}/export").status_code == 404
    session_storage.update_chat_data(SESSION, [], llm_history=[])
    assert client.post(f"/sessions/{SESSION}/export").status_code == 422
