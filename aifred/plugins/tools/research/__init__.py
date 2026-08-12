"""Web Research plugin (web_search, web_fetch).

Vollständig atomarisiert (2026-08-12): die Tool-Fassade (früher in
``lib/research_tools.py``) lebt hier im Plugin — Descriptions kommen aus
``prompts/tools/`` (load_tool_description-Konvention), die Anleitung aus
den ``prompts/<de|en>/``-Fragmenten (granted_tools-gated). Die Pipeline
(Search → Ranking → Scraping → Context → Cache) bleibt lib:
``execute_research``/``hub_web_search`` in ``lib/research_tools.py``.
"""

import json
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Optional

from ....lib.function_calling import Tool
from ....lib.i18n import t
from ....lib.plugin_base import PluginContext, load_tool_description
from ....lib.security import TIER_READONLY


def get_research_tools(state: Optional[Any] = None, lang: str = "de", llm_history: Optional[list] = None) -> list[Tool]:
    """Create research tools bound to a specific state instance.

    The web_search tool runs the full pipeline (search + scraping).
    """
    from ....lib.research_tools import execute_research, hub_web_search

    _llm_history: list = llm_history or []

    async def _execute_web_search(queries: list[str]) -> AsyncGenerator[dict[str, Any], None]:
        """Tool executor: runs research pipeline with model-provided queries.

        Async-generator-shaped tool executor (see ToolKit.execute_streaming).
        Yields ``{"progress": ""}`` on each upstream yield from
        ``execute_research`` so the LLM-stream can push UI updates while the
        pipeline runs (the state is mutated via ``state.add_debug``/
        ``state.set_progress`` directly inside execute_research; the empty
        progress marker just nudges the outer generator to yield).
        Terminates with exactly one ``{"result": "..."}`` event.
        """
        if not queries:
            yield {"result": json.dumps({"error": "No search queries provided"})}
            return

        queries = queries[:3]

        if state:
            # Full pipeline with browser State (cache, progress bar, sources HTML).
            # execute_research mutates state and yields between phases — we forward
            # each yield as a progress marker so the UI updates incrementally
            # instead of seeing one big block at the end.
            async for _ in execute_research(
                state=state,
                user_query=queries[0],
                lang=lang,
                pre_generated_queries=queries,
            ):
                yield {"progress": ""}
            result = getattr(state, "_research_context", "")
            if result:
                from ....lib.security import wrap_untrusted_data
                result = wrap_untrusted_data(result, "web_research")
            yield {"result": result if result else json.dumps({"error": "No results found"})}
            return

        # Hub path (Discord, Email) — history passed from PluginContext.
        # No state-mutation pipeline; we just await and emit the final result.
        result = await hub_web_search(queries, _llm_history)
        if result:
            from ....lib.security import wrap_untrusted_data
            result = wrap_untrusted_data(result, "web_research")
        yield {"result": result}

    async def _execute_web_fetch(url: str) -> str:
        """Tool executor: fetch and extract content from a specific URL."""
        from ....lib.logging_utils import log_message
        from ....lib.security import UnsafeURLError, validate_external_url
        from ....lib.tools.registry import scrape_webpage

        try:
            validate_external_url(url)
        except UnsafeURLError as e:
            log_message(f"🛑 web_fetch blocked: {e}", "warning")
            return json.dumps({"error": f"URL rejected: {e}"})

        log_message(f"🌐 web_fetch: {url}")
        result = scrape_webpage(url)

        if result.get("success") and result.get("content"):
            content = result["content"]
            word_count = result.get("word_count", 0)
            log_message(f"✅ web_fetch: {word_count} words from {url}")
            from ....lib.security import wrap_untrusted_data
            return wrap_untrusted_data(f"# Content from {url}\n\n{content}", url)
        else:
            error = result.get("error", "Failed to fetch URL")
            log_message(f"❌ web_fetch failed: {error}")
            return json.dumps({"error": f"Could not fetch {url}: {error}"})

    return [
        Tool(
            name="web_search",
            tier=TIER_READONLY,
            description=load_tool_description(__file__, "web_search"),
            parameters={
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "1-3 search queries (each sent to a different search engine)",
                        "minItems": 1,
                        "maxItems": 3,
                    },
                },
                "required": ["queries"],
            },
            executor=_execute_web_search,
        ),
        Tool(
            name="web_fetch",
            tier=TIER_READONLY,
            description=load_tool_description(__file__, "web_fetch"),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full URL to fetch (must start with http:// or https://)",
                    },
                },
                "required": ["url"],
            },
            executor=_execute_web_fetch,
        ),
    ]


@dataclass
class ResearchPlugin:
    name: str = "research"
    display_name: str = "Web Research"
    description: str = "Web-Recherche per Multi-Query-Suche (Brave, Tavily, SearXNG) plus Inhalts-Scraping und Quellen-Ranking."

    def is_available(self) -> bool:
        return True

    def get_tools(self, ctx: PluginContext) -> list[Tool]:
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
