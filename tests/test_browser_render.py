"""Tests for aifred.lib.browser_render (render_html tool backend)."""

import shutil
import uuid

import pytest

from aifred.lib.browser_render import (
    parse_console_messages,
    render_html_in_browser,
    resolve_sandbox_html_path,
)
from aifred.lib.sandbox_tools import _looks_like_html_document

SESSION = uuid.uuid4().hex  # 32-hex like real session ids


@pytest.fixture()
def session_html(tmp_path, monkeypatch):
    """Create a sandbox_output/<session>/ dir with one HTML file."""
    from aifred.lib import config, sandbox
    out_root = tmp_path / "sandbox_output"
    monkeypatch.setattr(config, "SANDBOX_OUTPUT_DIR", out_root)
    monkeypatch.setattr(sandbox, "SESSION_ID_RE", sandbox.SESSION_ID_RE)  # no-op, clarity
    session_dir = out_root / SESSION
    session_dir.mkdir(parents=True)
    fname = f"{uuid.uuid4().hex[:8]}.html"
    (session_dir / fname).write_text(
        "<!DOCTYPE html><html><body><script>console.error('boom');"
        "console.log('ok');</script></body></html>",
        encoding="utf-8",
    )
    return fname


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


# ── parse_console_messages ───────────────────────────────────────


def test_parse_console_lines():
    stderr = (
        '[123:123:0730/152724.382259:INFO:CONSOLE:6] "render ok", source: file:///x.html (6)\n'
        "[123:123:0730/152724.382281:WARNING:something_else.cc(42)] unrelated\n"
        '[123:123:0730/152724.382295:INFO:CONSOLE:8] "Uncaught ReferenceError: nope", source: file:///x.html (8)\n'
    )
    msgs = parse_console_messages(stderr)
    assert len(msgs) == 2
    assert "render ok" in msgs[0]
    assert "Uncaught ReferenceError" in msgs[1]


# ── STDOUT HTML guard ────────────────────────────────────────────


def test_html_guard_detects_document():
    assert _looks_like_html_document("<!DOCTYPE html><html>...")
    assert _looks_like_html_document("  \n<html lang=\"de\">")
    assert not _looks_like_html_document("result: 42")
    assert not _looks_like_html_document("use <html> tags for markup")


# ── integration: real headless Chrome ────────────────────────────


@pytest.mark.skipif(
    shutil.which("google-chrome") is None, reason="google-chrome not installed"
)
def test_render_real_browser(session_html):
    import asyncio
    url = f"/_upload/sandbox_output/{SESSION}/{session_html}"
    result = asyncio.run(render_html_in_browser(url, SESSION, wait_ms=1500))
    assert result.error == "" and not result.timed_out
    joined = "\n".join(result.console_messages)
    assert "boom" in joined and "ok" in joined
    assert result.screenshot_url.endswith(".png")
