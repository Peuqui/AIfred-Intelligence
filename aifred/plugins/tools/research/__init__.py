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
    description: str = "Web-Recherche per Multi-Query-Suche (Brave, Tavily, SearXNG) plus Inhalts-Scraping und Quellen-Ranking."

    def is_available(self) -> bool:
        return True

    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        from ....lib.research_tools import get_research_tools
        return get_research_tools(state=ctx.state, lang=ctx.lang, llm_history=ctx.llm_history)

    def get_prompt_instructions(self, lang: str, granted_tools: "set[str] | None" = None) -> str:
        # Kein Hardcoding — atomare Fragmente in prompts/<de|en>/ beim Plugin.
        from ....lib.plugin_base import load_plugin_instructions
        return load_plugin_instructions(self, lang, granted_tools)

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
                q = str(queries[0])
                return f"🔍 {q[:60]}{'...' if len(q) > 60 else ''}"
            return t("tool_search", lang=lang)
        return ""


plugin = ResearchPlugin()
