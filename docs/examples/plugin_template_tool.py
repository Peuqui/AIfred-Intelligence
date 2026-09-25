"""Template: Tool Plugin for AIfred.

Copy this file to aifred/plugins/tools/hello/__init__.py and customize.
It is auto-discovered on the next AIfred restart.

Directory structure:
    aifred/plugins/tools/hello/
        __init__.py                 # this file (plugin code)
        i18n.json                   # REQUIRED: plugin_display_name + plugin_description (de + en)
        prompts/tools/hello.txt     # REQUIRED: tool description the LLM sees (English)
        prompts/de/_intro.txt       # optional: prompt instructions (both languages)
        prompts/en/_intro.txt

Nothing the LLM reads is hardcoded here — descriptions and instructions
live in the prompt files. Plugins never import other plugins; shared logic
belongs in aifred/lib/. Guide: docs/en/guides/plugin-development.md

This example provides a simple 'hello' tool that the LLM can call.
"""

import json
from dataclasses import dataclass
from typing import Any

from ....lib.function_calling import Tool
from ....lib.plugin_base import PluginContext, load_tool_description
from ....lib.security import TIER_READONLY


@dataclass
class HelloPlugin:
    name: str = "hello"  # MUST equal the folder name

    def is_available(self) -> bool:
        """Check if this plugin can run. Return False to disable."""
        return True

    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        """Return the tools the LLM can call."""

        async def _execute_hello(name: str = "World") -> str:
            """Tool executor — receives the LLM's arguments, returns a result string."""
            return json.dumps({"greeting": f"Hello, {name}!", "lang": ctx.lang})

        return [
            Tool(
                name="hello",
                tier=TIER_READONLY,  # permission tier, see aifred/lib/security.py
                description=load_tool_description(__file__, "hello"),
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

    def get_prompt_instructions(self, lang: str, granted_tools: "set[str] | None" = None) -> str:
        """Prompt text injected into the system prompt, from prompts/<de|en>/."""
        from ....lib.plugin_base import load_plugin_instructions
        return load_plugin_instructions(self, lang, granted_tools)

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        """UI status text shown while the tool runs. Empty = not owned."""
        if tool_name == "hello":
            return f"👋 {tool_args.get('name', '...')}"
        return ""


# Module-level instance — REQUIRED for auto-discovery
plugin = HelloPlugin()
