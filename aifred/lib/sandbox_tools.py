"""Sandbox code execution tool for LLM function calling.

Provides a single `execute_code` tool that runs Python in a sandboxed subprocess.
"""

import json
from typing import Optional

from .function_calling import Tool
from .security import TIER_WRITE_DATA, TIER_WRITE_SYSTEM
from .logging_utils import log_message
from .prompt_loader import load_shared_tool_description


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


def get_sandbox_tools(session_id: Optional[str] = None) -> list[Tool]:
    """Create sandbox tools for LLM function calling.

    Returns three tools:
      - execute_code       (TIER_WRITE_DATA):   documents/ mounted read-only
      - execute_code_write (TIER_WRITE_SYSTEM): documents/ mounted read-write
      - render_html        (TIER_WRITE_DATA):   headless-Chrome verify pass
        for a SANDBOX_HTML_URL (console messages + screenshot)

    The pipeline filters by max_tier — low-tier contexts only see execute_code.

    Args:
        session_id: Session ID for output file organization and cleanup.
    """

    async def _run(code: str, description: str, allow_write: bool) -> str:
        from .sandbox import execute_sandboxed_code

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
                parts.append(f"SANDBOX_HTML_URL: {html_url}")
            parts.append("The interactive visualization is automatically embedded in the chat. Do NOT try to display it again. Just describe what was created.")

        if result.images:
            for img_url in result.images:
                parts.append(f"SANDBOX_IMAGE_URL: {img_url}")
            parts.append("The plot image is automatically displayed in the chat. Do NOT try to show it again or generate base64. Just describe the result.")

        if result.exit_code != 0 and not result.timed_out:
            parts.append(f"EXIT CODE: {result.exit_code}")

        if not parts:
            parts.append("Code executed successfully (no output).")

        return "\n\n".join(parts)

    async def _execute_code(code: str, description: str = "") -> str:
        return await _run(code, description, allow_write=False)

    async def _execute_code_write(code: str, description: str = "") -> str:
        return await _run(code, description, allow_write=True)

    async def _render_html(html_url: str, wait_ms: int = 0) -> str:
        from .browser_render import render_html_in_browser

        if not html_url or not html_url.strip():
            return json.dumps({"error": "No html_url provided"})

        log_message(f"🔧 render_html: {html_url}")
        result = await render_html_in_browser(
            html_url.strip(), session_id=session_id or "", wait_ms=wait_ms or None,
        )

        parts: list[str] = []
        if result.timed_out:
            parts.append("⏰ TIMEOUT: Browser render exceeded time limit.")
        if result.error:
            parts.append(f"ERROR: {result.error}")

        if result.console_messages:
            joined = "\n".join(result.console_messages)
            parts.append(f"BROWSER CONSOLE ({len(result.console_messages)} message(s)):\n{joined}")
        elif not result.error:
            parts.append("BROWSER CONSOLE: no messages (no JS errors detected).")

        if result.screenshot_url:
            parts.append(f"SANDBOX_IMAGE_URL: {result.screenshot_url}")
            parts.append(
                "The screenshot is automatically displayed in the chat. "
                "Do NOT try to show it again. Check the console messages above "
                "for JS errors; if the page has vision-analysis available, you "
                "may inspect the screenshot visually."
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

    return [
        Tool(
            name="execute_code",
            tier=TIER_WRITE_DATA,
            description=load_shared_tool_description("execute_code_tool.txt"),
            parameters=params,
            executor=_execute_code,
        ),
        Tool(
            name="execute_code_write",
            tier=TIER_WRITE_SYSTEM,
            description=load_shared_tool_description("execute_code_write_tool.txt"),
            parameters=params,
            executor=_execute_code_write,
        ),
        Tool(
            name="render_html",
            tier=TIER_WRITE_DATA,
            description=load_shared_tool_description("render_html_tool.txt"),
            parameters={
                "type": "object",
                "properties": {
                    "html_url": {
                        "type": "string",
                        "description": "SANDBOX_HTML_URL from a previous execute_code result",
                    },
                    "wait_ms": {
                        "type": "integer",
                        "description": "Virtual time budget in ms before the screenshot (default 5000) — increase for long-running animations",
                    },
                },
                "required": ["html_url"],
            },
            executor=_render_html,
        ),
    ]
