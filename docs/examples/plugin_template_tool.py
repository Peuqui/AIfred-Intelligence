"""Template: Tool Plugin for AIfred.

Copy this file to aifred/plugins/tools/hello/__init__.py and customize.
It will be auto-discovered on next AIfred restart.

Directory structure:
    aifred/plugins/tools/hello/
        __init__.py     # This file (plugin code)
        i18n.json       # REQUIRED: plugin_display_name + plugin_description (DE and EN)

This example provides a simple 'hello' tool that the LLM can call.
"""

import json
from dataclasses import dataclass

from ...lib.function_calling import Tool
from ...lib.plugin_base import PluginContext


@dataclass
class HelloPlugin:
    name: str = "hello"  # MUST equal the folder name

    def is_available(self) -> bool:
        """Check if this plugin can run. Return False to disable."""
        return True

    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        """Return tools the LLM can call."""

        async def _execute_hello(name: str = "World") -> str:
            """Tool executor — receives args from LLM, returns result string."""
            return json.dumps({"greeting": f"Hello, {name}!", "lang": ctx.lang})

        return [
            Tool(
                name="hello",
                description="Greet someone by name. Use this when the user wants to test the plugin system.",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Name of the person to greet",
                        },
                    },
                    "required": ["name"],
                },
                executor=_execute_hello,
            ),
        ]

    def get_prompt_instructions(self, lang: str) -> str:
        """Prompt text injected into LLM system prompt. Empty = none."""
        return ""

    def get_ui_status(self, tool_name: str, tool_args: dict, lang: str) -> str:
        """UI status text shown while tool executes. Empty = not owned."""
        if tool_name == "hello":
            return f"👋 Greeting {tool_args.get('name', '...')}..."
        return ""


# Module-level instance — REQUIRED for auto-discovery
plugin = HelloPlugin()
