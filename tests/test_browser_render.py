"""Tests for aifred.lib.browser_render (render_html tool backend)."""

import asyncio
import shutil
import uuid

import pytest

from aifred.lib.browser_render import (
    render_html_in_browser,
    resolve_sandbox_html_path,
)
from aifred.plugins.tools.sandbox import _looks_like_html_document

SESSION = uuid.uuid4().hex  # 32-hex like real session ids

_TEST_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<button id="go" onclick="document.getElementById('state').textContent='CLICKED';
console.log('click handled')">Go</button>
<div id="state">initial</div>
<script>console.error('boom'); console.log('ok');</script>
</body></html>"""


def _write_html(out_root, content: str) -> str:
    session_dir = out_root / SESSION
    session_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{uuid.uuid4().hex[:8]}.html"
    (session_dir / fname).write_text(content, encoding="utf-8")
    return fname


@pytest.fixture()
def session_html(tmp_path, monkeypatch):
    """Create a sandbox_output/<session>/ dir with one HTML file."""
    from aifred.lib import config
    out_root = tmp_path / "sandbox_output"
    monkeypatch.setattr(config, "SANDBOX_OUTPUT_DIR", out_root)
    return _write_html(out_root, _TEST_PAGE)


# ── resolve_sandbox_html_path ────────────────────────────────────


def test_resolve_valid_url(session_html):
    url = f"/_upload/sandbox_output/{SESSION}/{session_html}"
    path = resolve_sandbox_html_path(url, SESSION)
    assert path is not None and path.name == session_html


def test_resolve_with_backend_prefix(session_html):
    url = f"http://localhost:8002/_upload/sandbox_output/{SESSION}/{session_html}"
    path = resolve_sandbox_html_path(url, SESSION)
    assert path is not None and path.name == session_html


def test_resolve_rejects_foreign_session(session_html):
    other = uuid.uuid4().hex
    url = f"/_upload/sandbox_output/{SESSION}/{session_html}"
    assert resolve_sandbox_html_path(url, other) is None


def test_resolve_rejects_traversal(session_html):
    url = f"/_upload/sandbox_output/{SESSION}/../../../etc/passwd"
    assert resolve_sandbox_html_path(url, SESSION) is None


def test_resolve_rejects_bad_filename(session_html):
    url = f"/_upload/sandbox_output/{SESSION}/evil.html"
    assert resolve_sandbox_html_path(url, SESSION) is None


# ── vision_analyze can resolve sandbox screenshots ───────────────


def test_url_to_file_path_resolves_sandbox_output(tmp_path, monkeypatch):
    from aifred.lib import config
    from aifred.lib.vision_utils import url_to_file_path
    out_root = tmp_path / "sandbox_output"
    monkeypatch.setattr(config, "SANDBOX_OUTPUT_DIR", out_root)
    shot = out_root / SESSION / "abcd1234.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b"png")
    url = f"/_upload/sandbox_output/{SESSION}/abcd1234.png"
    assert url_to_file_path(url, SESSION) == shot
    # VI7: foreign session must be rejected
    assert url_to_file_path(url, uuid.uuid4().hex) is None


# ── STDOUT HTML guard ────────────────────────────────────────────


def test_html_guard_detects_document():
    assert _looks_like_html_document("<!DOCTYPE html><html>...")
    assert _looks_like_html_document("  \n<html lang=\"de\">")
    assert not _looks_like_html_document("result: 42")
    assert not _looks_like_html_document("use <html> tags for markup")


# ── integration: real headless Chrome via Playwright ─────────────

_chrome_missing = shutil.which("google-chrome") is None


@pytest.mark.skipif(_chrome_missing, reason="google-chrome not installed")
def test_render_real_browser(session_html):
    url = f"/_upload/sandbox_output/{SESSION}/{session_html}"
    result = asyncio.run(render_html_in_browser(url, SESSION, wait_ms=300))
    assert result.error == "" and not result.timed_out
    joined = "\n".join(result.console_messages)
    assert "boom" in joined and "ok" in joined
    assert len(result.screenshot_urls) == 1
    assert result.screenshot_urls[0].endswith(".png")


@pytest.mark.skipif(_chrome_missing, reason="google-chrome not installed")
def test_render_with_actions(session_html):
    url = f"/_upload/sandbox_output/{SESSION}/{session_html}"
    result = asyncio.run(
        render_html_in_browser(
            url, SESSION, wait_ms=300,
            actions=[
                {"screenshot": True},          # initial state
                {"click": "#go"},              # changes #state → CLICKED
                {"wait_ms": 100},
                {"click": "#does-not-exist"},  # must land in action_errors
            ],
        )
    )
    assert result.error == "" and not result.timed_out
    joined = "\n".join(result.console_messages)
    assert "click handled" in joined  # click actually executed page JS
    assert len(result.screenshot_urls) == 2  # intermediate + final
    assert len(result.action_errors) == 1
    assert "does-not-exist" in result.action_errors[0]
