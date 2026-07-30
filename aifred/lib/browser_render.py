"""Headless-browser rendering for sandbox-generated HTML.

Backs the generic ``render_html`` tool: loads an HTML file previously
produced by ``execute_code`` (SANDBOX_HTML_URL) in headless Chrome,
captures console messages (incl. uncaught JS errors) and a screenshot.
This closes the build→verify loop for HTML/JS output that the Python
sandbox itself cannot execute (no browser inside bubblewrap).

No CDP / no extra dependencies — plain Chrome CLI flags:
``--headless=new --screenshot=... --enable-logging=stderr`` emit console
lines as ``[pid:tid:date:INFO:CONSOLE:<line>] "msg", source: <url> (<line>)``
on stderr, which is all we need.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .logging_utils import log_message

# Sandbox HTML filenames are uuid4().hex[:8] + ".html" (see sandbox._collect_html)
_SANDBOX_HTML_NAME_RE = re.compile(r"^[0-9a-f]{8}\.html$")
_CONSOLE_LINE_RE = re.compile(r":CONSOLE:\d+\]\s*(.*)$")


@dataclass
class RenderResult:
    console_messages: list[str] = field(default_factory=list)
    screenshot_url: str = ""
    error: str = ""
    timed_out: bool = False


def resolve_sandbox_html_path(html_url: str, session_id: str) -> Optional[Path]:
    """Map a SANDBOX_HTML_URL back to its file inside the session's output dir.

    Accepts the URL with or without the BACKEND_URL prefix. Returns None
    unless the URL points into THIS session's sandbox_output directory and
    the filename matches the sandbox naming scheme (path-traversal guard).
    """
    from .sandbox import _safe_session_subdir

    session_dir = _safe_session_subdir(session_id)
    if session_dir is None:
        return None

    # Strip scheme+host if present, keep the path part
    path_part = html_url.split("://", 1)[-1]
    if "/" in path_part and "://" in html_url:
        path_part = "/" + path_part.split("/", 1)[1]

    prefix = f"/_upload/sandbox_output/{session_id}/"
    if not path_part.startswith(prefix):
        return None
    filename = path_part[len(prefix):]
    if not _SANDBOX_HTML_NAME_RE.match(filename):
        return None

    candidate = session_dir / filename
    return candidate if candidate.is_file() else None


def parse_console_messages(stderr_text: str) -> list[str]:
    """Extract console messages from Chrome's --enable-logging=stderr output."""
    messages: list[str] = []
    for line in stderr_text.splitlines():
        m = _CONSOLE_LINE_RE.search(line)
        if m:
            messages.append(m.group(1).strip())
    return messages


async def render_html_in_browser(
    html_url: str, session_id: str, wait_ms: Optional[int] = None
) -> RenderResult:
    """Render a sandbox HTML file in headless Chrome.

    Returns console messages and a screenshot URL (saved next to the HTML
    in sandbox_output/{session_id}/ so the chat pipeline can embed it).
    """
    from .config import (
        BROWSER_RENDER_BINARY,
        BROWSER_RENDER_TIMEOUT_SECONDS,
        BROWSER_RENDER_VIRTUAL_TIME_MS,
        BROWSER_RENDER_WINDOW_SIZE,
    )
    from .sandbox import _sandbox_url, _session_output_dir

    binary = shutil.which(BROWSER_RENDER_BINARY)
    if not binary:
        return RenderResult(error=f"Browser binary not found: {BROWSER_RENDER_BINARY}")

    html_path = resolve_sandbox_html_path(html_url, session_id)
    if html_path is None:
        return RenderResult(
            error=(
                "Invalid html_url: must be a SANDBOX_HTML_URL from a previous "
                "execute_code call in this session."
            )
        )

    virtual_time = wait_ms if wait_ms and wait_ms > 0 else BROWSER_RENDER_VIRTUAL_TIME_MS

    profile_dir = tempfile.mkdtemp(prefix="aifred_render_")
    screenshot_tmp = Path(profile_dir) / "screenshot.png"
    cmd = [
        binary,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        f"--user-data-dir={profile_dir}",
        "--enable-logging=stderr",
        "--v=0",
        f"--window-size={BROWSER_RENDER_WINDOW_SIZE}",
        f"--virtual-time-budget={virtual_time}",
        f"--screenshot={screenshot_tmp}",
        f"file://{html_path}",
    ]

    log_message(f"render_html: rendering {html_path.name} (virtual time {virtual_time} ms)")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=BROWSER_RENDER_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return RenderResult(timed_out=True, error="Browser render timed out")

        result = RenderResult(
            console_messages=parse_console_messages(
                stderr_bytes.decode("utf-8", errors="replace")
            )
        )

        if screenshot_tmp.is_file() and screenshot_tmp.stat().st_size > 0:
            output_dir = _session_output_dir(session_id)
            filename = f"{uuid.uuid4().hex[:8]}.png"
            shutil.copy2(screenshot_tmp, output_dir / filename)
            result.screenshot_url = _sandbox_url(session_id, filename)
        else:
            result.error = "Browser produced no screenshot"
        return result
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)
