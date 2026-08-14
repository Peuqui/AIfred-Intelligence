"""Headless-browser rendering for sandbox-generated HTML.

Backs the generic ``render_html`` tool: loads an HTML file previously
produced by ``execute_code`` (SANDBOX_HTML_URL) in headless Chrome via
Playwright, optionally performs interactions (clicks, fills, mouse drags),
captures console messages (incl. uncaught JS errors) and screenshots.
This closes the build→verify loop for HTML/JS output that the Python
sandbox itself cannot execute (no browser inside bubblewrap).

Playwright drives the SYSTEM Chrome (``channel``-launch) — no bundled
browser downloads involved.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .logging_utils import log_message

# Sandbox HTML filenames are uuid4().hex[:8] + ".html" (see sandbox._collect_html)
_SANDBOX_HTML_NAME_RE = re.compile(r"^[0-9a-f]{8}\.html$")


@dataclass
class RenderResult:
    console_messages: list[str] = field(default_factory=list)
    action_errors: list[str] = field(default_factory=list)
    screenshot_urls: list[str] = field(default_factory=list)
    error: str = ""
    timed_out: bool = False


def resolve_sandbox_html_path(html_url: str, session_id: str) -> Optional[Path]:
    """Map a render_html URL to its file on disk.

    Two accepted sources (path-traversal guarded each):
      - SANDBOX_HTML_URL: /_upload/sandbox_output/<THIS session>/<hash>.html —
        other sessions' outputs stay unreachable (session isolation).
      - Documents: /_upload/documents/<path>.html (or bare documents/<path>.html)
        — the shared agent workspace; deliverables saved there can be rendered
        and verified DIRECTLY, without copying them into the sandbox first.
        No session check: documents/ is readable by every agent anyway.

    Accepts URLs with or without the scheme+host prefix.
    """
    from .sandbox import _safe_session_subdir

    # Strip scheme+host if present, keep the path part
    path_part = html_url.split("://", 1)[-1]
    if "/" in path_part and "://" in html_url:
        path_part = "/" + path_part.split("/", 1)[1]

    # Documents workspace (shared across agents/sessions)
    if path_part.startswith("documents/"):
        path_part = f"/_upload/{path_part}"
    docs_prefix = "/_upload/documents/"
    if path_part.startswith(docs_prefix):
        from urllib.parse import unquote

        from .config import DOCUMENTS_DIR
        rel = unquote(path_part[len(docs_prefix):])
        if not rel.lower().endswith((".html", ".htm")):
            return None
        docs_root = Path(DOCUMENTS_DIR).resolve()
        candidate = (docs_root / rel).resolve()
        # resolve() follows symlinks, so a link escaping documents/ fails here
        if not candidate.is_relative_to(docs_root):
            return None
        return candidate if candidate.is_file() else None

    # Session sandbox output
    session_dir = _safe_session_subdir(session_id)
    if session_dir is None:
        return None

    prefix = f"/_upload/sandbox_output/{session_id}/"
    if not path_part.startswith(prefix):
        return None
    filename = path_part[len(prefix):]
    if not _SANDBOX_HTML_NAME_RE.match(filename):
        return None

    candidate = session_dir / filename
    return candidate if candidate.is_file() else None


async def _apply_action(page: Any, action: dict, index: int, result: RenderResult,
                        take_screenshot) -> None:
    """Execute one interaction step; failures are reported, not swallowed."""
    try:
        if "click" in action:
            await page.click(str(action["click"]))
        elif "fill" in action:
            spec = action["fill"]
            await page.fill(str(spec["selector"]), str(spec["text"]))
        elif "press" in action:
            await page.keyboard.press(str(action["press"]))
        elif "mouse_drag" in action:
            spec = action["mouse_drag"]
            x1, y1 = spec["from"]
            x2, y2 = spec["to"]
            await page.mouse.move(float(x1), float(y1))
            await page.mouse.down()
            await page.mouse.move(float(x2), float(y2), steps=10)
            await page.mouse.up()
        elif "wait_ms" in action:
            await page.wait_for_timeout(min(int(action["wait_ms"]), 30_000))
        elif "screenshot" in action:
            await take_screenshot()
        else:
            result.action_errors.append(f"action {index + 1}: unknown action {action!r}")
    except Exception as exc:  # noqa: BLE001 — report per-action failure to the model
        result.action_errors.append(f"action {index + 1} {action!r} failed: {exc}")


async def render_html_in_browser(
    html_url: str,
    session_id: str,
    wait_ms: Optional[int] = None,
    actions: Optional[list[dict]] = None,
) -> RenderResult:
    """Render a sandbox HTML file in headless Chrome via Playwright.

    Loads the page, waits ``wait_ms`` (animations run in real time), applies
    the optional ``actions`` sequence (click/fill/press/mouse_drag/wait_ms/
    screenshot), and always takes a final screenshot. Console messages and
    uncaught page errors are collected throughout.
    """
    from .config import (
        BROWSER_RENDER_ACTION_TIMEOUT_MS,
        BROWSER_RENDER_CHANNEL,
        BROWSER_RENDER_DEFAULT_WAIT_MS,
        BROWSER_RENDER_TIMEOUT_SECONDS,
        BROWSER_RENDER_WINDOW_SIZE,
    )
    from .sandbox import SCREENSHOT_PREFIX, _sandbox_url, _session_output_dir

    html_path = resolve_sandbox_html_path(html_url, session_id)
    if html_path is None:
        return RenderResult(
            error=(
                "Invalid html_url: must be a SANDBOX_HTML_URL from a previous "
                "execute_code call in this session, or an existing document "
                "(/_upload/documents/<path>.html)."
            )
        )

    width, height = (int(v) for v in BROWSER_RENDER_WINDOW_SIZE.split(","))
    settle_ms = wait_ms if wait_ms and wait_ms > 0 else BROWSER_RENDER_DEFAULT_WAIT_MS
    result = RenderResult()
    output_dir = _session_output_dir(session_id)
    tmp_shots: list[Path] = []

    async def _run() -> None:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(channel=BROWSER_RENDER_CHANNEL, headless=True)
            try:
                page = await browser.new_page(viewport={"width": width, "height": height})
                page.set_default_timeout(BROWSER_RENDER_ACTION_TIMEOUT_MS)
                page.on("console", lambda m: result.console_messages.append(f"{m.type}: {m.text}"))
                page.on("pageerror", lambda e: result.console_messages.append(f"pageerror: {e}"))

                async def take_screenshot() -> None:
                    shot = output_dir / f"__render_{uuid.uuid4().hex[:8]}.png"
                    await page.screenshot(path=str(shot))
                    tmp_shots.append(shot)

                # SECURITY: block all external network from the render browser.
                # render_html verifies the model's OWN, self-contained/local HTML
                # — it must never become an egress channel (see the
                # sandbox-escape study). Allow only file:/data:/blob: and
                # localhost; abort everything else (fail-closed).
                #
                # Generated HTML references the mirrored JS libs with RELATIVE
                # /vendor/... URLs so the same artifact works in the user's
                # browser regardless of host (an absolute localhost URL broke
                # every artifact opened from another machine). Under the file://
                # base those resolve to file:///vendor/... — fulfilled here
                # directly from assets/vendor/ (basename only, no traversal).
                from urllib.parse import urlparse as _urlparse

                vendor_dir = Path(__file__).parent.parent.parent / "assets" / "vendor"
                _vendor_types = {".js": "text/javascript", ".css": "text/css"}

                async def _block_external(route: Any) -> None:
                    parsed = _urlparse(route.request.url)
                    host = (parsed.hostname or "").lower()
                    if parsed.path.startswith("/vendor/"):
                        lib = vendor_dir / Path(parsed.path).name
                        if lib.is_file():
                            await route.fulfill(
                                body=lib.read_bytes(),
                                content_type=_vendor_types.get(lib.suffix, "application/octet-stream"),
                            )
                        else:
                            await route.abort()
                        return
                    if parsed.scheme in ("file", "data", "blob") or host in (
                        "localhost", "127.0.0.1", "::1",
                    ):
                        await route.continue_()
                    else:
                        await route.abort()

                await page.route("**/*", _block_external)

                await page.goto(f"file://{html_path}")
                await page.wait_for_timeout(settle_ms)

                for i, action in enumerate(actions or []):
                    if not isinstance(action, dict):
                        result.action_errors.append(f"action {i + 1}: not an object: {action!r}")
                        continue
                    await _apply_action(page, action, i, result, take_screenshot)

                await take_screenshot()
            finally:
                await browser.close()

    log_message(
        f"render_html: rendering {html_path.name} "
        f"(wait {settle_ms} ms, {len(actions or [])} action(s))"
    )
    try:
        await asyncio.wait_for(_run(), timeout=BROWSER_RENDER_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        result.timed_out = True
        result.error = "Browser render timed out"
    except Exception as exc:  # noqa: BLE001 — surface launch/render failure to the model
        result.error = f"Browser render failed: {exc}"

    # Publish screenshots under the sandbox naming scheme (stable URLs)
    for shot in tmp_shots:
        if shot.is_file() and shot.stat().st_size > 0:
            filename = f"{SCREENSHOT_PREFIX}{uuid.uuid4().hex[:8]}.png"
            shutil.move(str(shot), output_dir / filename)
            result.screenshot_urls.append(_sandbox_url(session_id, filename))
    if not result.screenshot_urls and not result.error:
        result.error = "Browser produced no screenshot"
    return result
