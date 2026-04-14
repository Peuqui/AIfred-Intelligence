"""Sandbox code execution tool for LLM function calling.

Provides a single `execute_code` tool that runs Python in a sandboxed subprocess.
"""

import json
from typing import Optional

from .function_calling import Tool
from .security import TIER_WRITE_DATA, TIER_WRITE_SYSTEM
from .logging_utils import log_message
from .prompt_loader import load_tool_description


def get_sandbox_tools(session_id: Optional[str] = None) -> list[Tool]:
    """Create sandbox tools for LLM function calling.

    Returns two variants:
      - execute_code       (TIER_WRITE_DATA):   documents/ mounted read-only
      - execute_code_write (TIER_WRITE_SYSTEM): documents/ mounted read-write

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

        if result.stdout:
            parts.append(f"STDOUT:\n{result.stdout}")

        if result.stderr:
            parts.append(f"STDERR:\n{result.stderr}")

        if result.html_url:
            parts.append(f"SANDBOX_HTML_URL: {result.html_url}")
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
            description=load_tool_description("execute_code_tool.txt"),
            parameters=params,
            executor=_execute_code,
        ),
        Tool(
            name="execute_code_write",
            tier=TIER_WRITE_SYSTEM,
            description=load_tool_description("execute_code_write_tool.txt"),
            parameters=params,
            executor=_execute_code_write,
        ),
    ]
