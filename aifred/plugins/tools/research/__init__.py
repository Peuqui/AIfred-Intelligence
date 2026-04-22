"""Web Research plugin (web_search, web_fetch)."""

from dataclasses import dataclass
from typing import Any

from ....lib.function_calling import Tool
from ....lib.plugin_base import PluginContext
from ....lib.i18n import t


@dataclass
class ResearchPlugin:
    name: str = "research"
    display_name: str = "Web Research"

    def is_available(self) -> bool:
        return True

    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        from ....lib.research_tools import get_research_tools
        return get_research_tools(state=ctx.state, lang=ctx.lang, llm_history=ctx.llm_history)

    def get_prompt_instructions(self, lang: str) -> str:
        return ""

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        if tool_name == "web_fetch":
            url = tool_args.get("url", "")
            if url:
                from urllib.parse import urlparse
                parsed = urlparse(url)
                return f"🌐 {parsed.netloc}{parsed.path}"
            return ""
        elif tool_name == "web_search":
            queries = tool_args.get("queries", [])
            if queries:
                return f"🔍 {queries[0][:60]}..."
            return t("tool_search", lang=lang)
        return ""


plugin = ResearchPlugin()
