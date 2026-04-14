"""Sandbox (execute_code) plugin."""

from dataclasses import dataclass
from typing import Any

from ....lib.function_calling import Tool
from ....lib.plugin_base import PluginContext
from ....lib.i18n import t


@dataclass
class SandboxPlugin:
    name: str = "sandbox"
    display_name: str = "Sandbox"

    def is_available(self) -> bool:
        return True

    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        from ....lib.sandbox_tools import get_sandbox_tools
        return get_sandbox_tools(session_id=ctx.session_id)

    def get_prompt_instructions(self, lang: str) -> str:
        return ""

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        if tool_name not in ("execute_code", "execute_code_write"):
            return ""
        if not tool_args:
            return t("tool_code_generating", lang=lang)
        desc = tool_args.get("description", "")
        prefix = "✍️" if tool_name == "execute_code_write" else "⚙️"
        return f"{prefix} {desc[:60]}" if desc else t("tool_code_running", lang=lang)


plugin = SandboxPlugin()
