"""Sandbox (execute_code) plugin.

Vollständig atomarisiert (2026-08-12): die Tool-Fassade (früher
``lib/sandbox_tools.py``) lebt hier im Plugin — Descriptions kommen aus
``prompts/tools/`` (load_tool_description-Konvention), die Anleitung aus
den ``prompts/<de|en>/``-Fragmenten (granted_tools-gated). Die
Maschinerie (Container-Ausführung, Headless-Chrome) bleibt lib:
``lib/sandbox.py`` und ``lib/browser_render.py``.
"""

import json
from dataclasses import dataclass
from typing import Any, Optional

from ....lib.function_calling import Tool
from ....lib.i18n import t
from ....lib.logging_utils import log_message
from ....lib.plugin_base import PluginContext, load_tool_description
from ....lib.sandbox import (
    SANDBOX_HTML_URL_MARKER,
    SANDBOX_IMAGE_URL_MARKER,
    SANDBOX_VISION_FOCUS_MARKER,
)
from ....lib.security import TIER_WRITE_DATA, TIER_WRITE_SYSTEM


def _looks_like_html_document(text: str) -> bool:
    """True when STDOUT starts with a full HTML document.

    Guard against models printing their HTML instead of writing
    ``output.html`` — the printed copy wastes thousands of context tokens
    and skips the interactive chat embed.
    """
    head = text.lstrip()[:64].lower()
    return head.startswith("<!doctype html") or head.startswith("<html")


_HTML_IN_STDOUT_HINT = (
    "HTML document detected in STDOUT — the printed copy was dropped to save "
    "context. Write HTML to a FILE instead: open(\"output.html\", \"w\") — it "
    "is then embedded interactively in the chat and can be verified with the "
    "render_html tool. Re-run your code with the file write."
)


def _vendor_base() -> str:
    """Base URL of the locally mirrored JS libs (frontend-served assets/vendor).

    Deliberately RELATIVE: generated HTML is consumed by two clients — the
    user's browser (arbitrary host: LAN IP, HTTPS reverse proxy) and the
    headless render browser (loads via http://localhost). A relative URL is
    the single scheme that works for both; an absolute localhost URL broke
    every artifact opened from another machine (observed 2026-08-13,
    BryceLand: 3D view dead because three.js pointed at the user's own PC).
    """
    return "/vendor"


def get_sandbox_tools(session_id: Optional[str] = None) -> list[Tool]:
    """Create sandbox tools for LLM function calling.

    Returns three tools:
      - execute_code       (TIER_WRITE_DATA):   documents/ mounted read-only
      - execute_code_write (TIER_WRITE_SYSTEM): documents/ mounted read-write
      - render_html        (TIER_WRITE_DATA):   headless-Chrome verify pass
        for a SANDBOX_HTML_URL (console messages + screenshot)

    The pipeline filters by max_tier — contexts below TIER_WRITE_DATA see
    no sandbox tool at all (execute_code itself is TIER_WRITE_DATA).

    Args:
        session_id: Session ID for output file organization and cleanup.
    """

    async def _run(code: str, description: str, allow_write: bool) -> str:
        from ....lib.sandbox import execute_sandboxed_code

        if not code or not code.strip():
            return json.dumps({"error": "No code provided"})

        tool_label = "execute_code_write" if allow_write else "execute_code"
        log_message(f"🔧 {tool_label}: {description or '(no description)'}")

        result = await execute_sandboxed_code(
            code, session_id=session_id or "", allow_write=allow_write,
        )

        # Format output for LLM
        parts: list[str] = []

        if result.timed_out:
            parts.append("⏰ TIMEOUT: Execution exceeded time limit.")

        if result.stdout and _looks_like_html_document(result.stdout):
            # Educate instead of echoing ~5k tokens of markup back
            if result.html_urls:
                parts.append(
                    "STDOUT contained a full HTML document — dropped "
                    "(your output.html was captured, see below)."
                )
            else:
                parts.append(_HTML_IN_STDOUT_HINT)
        elif result.stdout:
            parts.append(f"STDOUT:\n{result.stdout}")

        if result.stderr:
            parts.append(f"STDERR:\n{result.stderr}")

        if result.html_urls:
            for html_url in result.html_urls:
                parts.append(f"{SANDBOX_HTML_URL_MARKER}{html_url}")
            parts.append("The interactive visualization is automatically embedded in the chat. Do NOT try to display it again. Just describe what was created.")

        if result.images:
            for img_url in result.images:
                parts.append(f"{SANDBOX_IMAGE_URL_MARKER}{img_url}")
            parts.append(
                "The plot image is automatically displayed in the chat — the user "
                "already sees it. Do NOT emit it again in your answer: no markdown "
                "images ![...](...), no img tags, no base64, no URLs. Just describe "
                "the result."
            )

        if result.exit_code != 0 and not result.timed_out:
            parts.append(f"EXIT CODE: {result.exit_code}")

        if not parts:
            parts.append("Code executed successfully (no output).")

        return "\n\n".join(parts)

    async def _execute_code(code: str, description: str = "") -> str:
        return await _run(code, description, allow_write=False)

    async def _execute_code_write(code: str, description: str = "") -> str:
        return await _run(code, description, allow_write=True)

    async def _render_html(
        html_url: str, wait_ms: int = 0, actions: Optional[list] = None,
        vision_focus: str = "",
    ) -> str:
        from ....lib.browser_render import render_html_in_browser

        if not html_url or not html_url.strip():
            return json.dumps({"error": "No html_url provided"})

        log_message(f"🔧 render_html: {html_url} ({len(actions or [])} action(s))")
        result = await render_html_in_browser(
            html_url.strip(), session_id=session_id or "",
            wait_ms=wait_ms or None, actions=actions,
        )

        parts: list[str] = []
        if result.timed_out:
            parts.append("⏰ TIMEOUT: Browser render exceeded time limit.")
        if result.error:
            parts.append(f"ERROR: {result.error}")

        if result.action_errors:
            joined = "\n".join(result.action_errors)
            parts.append(f"ACTION ERRORS ({len(result.action_errors)}):\n{joined}")

        if result.console_messages:
            joined = "\n".join(result.console_messages)
            parts.append(f"BROWSER CONSOLE ({len(result.console_messages)} message(s)):\n{joined}")
        elif not result.error:
            parts.append("BROWSER CONSOLE: no messages (no JS errors detected).")

        if result.screenshot_urls:
            for url in result.screenshot_urls:
                parts.append(f"{SANDBOX_IMAGE_URL_MARKER}{url}")
            if vision_focus and vision_focus.strip():
                # Single-line transport (the marker is parsed line-based in
                # describe_sandbox_screenshots)
                parts.append(
                    f"{SANDBOX_VISION_FOCUS_MARKER}{' '.join(vision_focus.split())}"
                )
            parts.append(
                "The screenshot(s) are automatically displayed in the chat — the "
                "user already sees them. Do NOT emit them again in your answer: "
                "no markdown images ![...](...), no img tags, no URLs. "
                "Check the console messages above for JS errors; an automatic "
                "text description of each screenshot follows below."
            )

        return "\n\n".join(parts)

    params = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python code to execute",
            },
            "description": {
                "type": "string",
                "description": "Brief description of what the code does (for logging)",
            },
        },
        "required": ["code"],
    }

    _vb = _vendor_base()
    return [
        Tool(
            name="execute_code",
            tier=TIER_WRITE_DATA,
            description=load_tool_description(__file__, "execute_code").replace("{VENDOR_BASE}", _vb),
            parameters=params,
            executor=_execute_code,
        ),
        Tool(
            name="execute_code_write",
            tier=TIER_WRITE_SYSTEM,
            description=load_tool_description(__file__, "execute_code_write"),
            parameters=params,
            executor=_execute_code_write,
        ),
        Tool(
            name="render_html",
            tier=TIER_WRITE_DATA,
            description=load_tool_description(__file__, "render_html").replace("{VENDOR_BASE}", _vb),
            parameters={
                "type": "object",
                "properties": {
                    "html_url": {
                        "type": "string",
                        "description": "SANDBOX_HTML_URL from a previous execute_code result",
                    },
                    "wait_ms": {
                        "type": "integer",
                        "description": "Settle time in ms after page load before actions/screenshot (default 2000) — increase for slow-building pages",
                    },
                    "actions": {
                        "type": "array",
                        "description": (
                            "Optional interaction sequence, each item ONE of: "
                            '{"click": "<css-selector>"} | '
                            '{"fill": {"selector": "<css>", "text": "..."}} | '
                            '{"press": "<key e.g. Enter>"} | '
                            '{"mouse_drag": {"from": [x, y], "to": [x, y]}} | '
                            '{"wait_ms": <int>} | '
                            '{"screenshot": true} (intermediate shot). '
                            "A final screenshot is always taken."
                        ),
                        "items": {"type": "object"},
                    },
                    "vision_focus": {
                        "type": "string",
                        "description": (
                            "Specific verification question for the automatic "
                            "screenshot description, e.g. 'Is the wireframe "
                            "terrain visible and does the laser beam end at "
                            "the ground?'. The vision model answers it first, "
                            "then describes the rest. All screenshots of this "
                            "call are described together as one chronological "
                            "sequence — before/after questions across shots "
                            "are encouraged. Pass it whenever you rendered to "
                            "verify a specific change."
                        ),
                    },
                },
                "required": ["html_url"],
            },
            executor=_render_html,
        ),
    ]


@dataclass
class SandboxPlugin:
    name: str = "sandbox"
    display_name: str = "Sandbox"
    description: str = "Sicherer Code-Ausführungsbereich: Python und Bash in isolierter Umgebung — für Berechnungen, Skripte und ad-hoc-Logik."

    def is_available(self) -> bool:
        return True

    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        return get_sandbox_tools(session_id=ctx.session_id)

    def get_prompt_instructions(self, lang: str, granted_tools: "set[str] | None" = None) -> str:
        # Kein Hardcoding — atomare Fragmente in prompts/<de|en>/ beim Plugin.
        from ....lib.plugin_base import load_plugin_instructions
        return load_plugin_instructions(self, lang, granted_tools)

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        if tool_name == "render_html":
            return "🖼️ HTML"
        if tool_name not in ("execute_code", "execute_code_write"):
            return ""
        if not tool_args:
            return t("tool_code_generating", lang=lang)
        desc = tool_args.get("description", "")
        prefix = "✍️" if tool_name == "execute_code_write" else "⚙️"
        return f"{prefix} {desc[:60]}" if desc else t("tool_code_running", lang=lang)


plugin = SandboxPlugin()
