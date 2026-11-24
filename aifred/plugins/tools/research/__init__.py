"""Web Research plugin (web_search, web_fetch).

Vollständig atomarisiert (2026-08-12): die Tool-Fassade (früher in
``lib/research_tools.py``) lebt hier im Plugin — Descriptions kommen aus
``prompts/tools/`` (load_tool_description-Konvention), die Anleitung aus
den ``prompts/<de|en>/``-Fragmenten (granted_tools-gated). Die Pipeline
(Search → Ranking → Scraping → Context) bleibt lib: ``run_web_search`` in
``lib/research_tools.py``.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from collections.abc import AsyncGenerator
from urllib.parse import parse_qsl, urlparse

from ....lib.config import WEB_FETCH_WATCH_PART_CHARS
from ....lib.function_calling import Tool
from ....lib.i18n import t
from ....lib.plugin_base import PluginContext, load_plugin_i18n, load_tool_description, load_tool_parameters
from ....lib.security import TIER_READONLY

_URL_RE = re.compile(r"https?://[^\s<>\"'`)\]]+")


def carried_part(url: str, known: set[str], own_words: str) -> str:
    """The longest part of ``url`` (path piece, parameter value, fragment) that could carry data out, empty when
    the URL is known (the user wrote it, a search result or fetched page of this turn showed it) or no part reaches
    WEB_FETCH_WATCH_PART_CHARS. The watchdog of web_fetch: it reports, it never blocks."""
    if url in known or url in own_words:
        return ""
    parsed = urlparse(url)
    parts = [*parsed.path.split("/"), *(value for _, value in parse_qsl(parsed.query, keep_blank_values=True)),
             parsed.fragment]
    longest = max(parts, key=len)
    return longest if len(longest) >= WEB_FETCH_WATCH_PART_CHARS else ""


def get_research_tools(
    lang: str = "de", llm_history: list | None = None, user_query: str = "", session_id: str = "",
) -> list[Tool]:
    """web_search (search, ranking against the conversation, scraping) and web_fetch."""
    from ....lib.context_manager import message_text
    from ....lib.research_tools import research_result_text, run_web_search

    _llm_history: list = llm_history or []
    # What the watchdog counts as known: the user's own words and the addresses this turn's results showed.
    own_words = "\n".join([user_query, *(message_text(msg) for msg in _llm_history if msg.get("role") == "user")])
    shown: set[str] = set()

    async def _execute_web_search(queries: list[str]) -> AsyncGenerator[dict[str, Any], None]:
        """Tool executor (async-generator-shaped, see ToolKit.execute_streaming): the result text of the llm_history
        (``note``: the source count; the queries stand in the call's arguments), then exactly one ``result``."""
        if not queries:
            yield {"result": json.dumps({"error": "No search queries provided"})}
            return

        queries = queries[:3]
        result, source_count, read_urls = await run_web_search(queries, _llm_history)
        shown.update(_URL_RE.findall(result or ""))
        if result:
            from ....lib.security import wrap_untrusted_data
            result = wrap_untrusted_data(result, "web_research")
        yield {"note": research_result_text(source_count, lang)}
        yield {"read": read_urls}
        yield {"result": result}

    async def _execute_web_fetch(url: str) -> AsyncGenerator[dict[str, Any], None]:
        """Tool executor: fetch and extract content from a specific URL.

        Async generator like web_search: every call leaves a result text for the
        llm_history (fetched or failed; the URL stands in the call's arguments), then the result.
        """
        from ....lib.logging_utils import log_message
        from ....lib.prompt_loader import load_prompt
        from ....lib.security import UnsafeURLError, validate_external_url
        from ....lib.tools.registry import scrape_webpage

        try:
            validate_external_url(url)
        except UnsafeURLError as e:
            log_message(f"🛑 web_fetch blocked: {e}", "warning")
            yield {"note": load_prompt("shared/web_fetch_failed_result", lang=lang)}
            yield {"result": json.dumps({"error": f"URL rejected: {e}"})}
            return

        log_message(f"🌐 web_fetch: {url}")
        carried = carried_part(url, shown, own_words)
        if carried:
            host = urlparse(url).netloc
            log_message(f"🐕 web_fetch watchdog: {host} with a {len(carried)}-char part not seen before: {url[:300]}",
                        "warning")
            if session_id:
                from ....lib import session_events
                text = load_plugin_i18n(Path(__file__).parent)["web_fetch_watch_notice"][lang if lang == "en" else "de"]
                session_events.publish(session_id, "notice", {"level": "warning",
                                                              "text": text.format(host=host, chars=len(carried))})
        result = scrape_webpage(url)

        if result.get("success") and result.get("content"):
            content = result["content"]
            shown.update(_URL_RE.findall(content))
            word_count = result.get("word_count", 0)
            log_message(f"✅ web_fetch: {word_count} words from {url}")
            from ....lib.security import wrap_untrusted_data
            yield {"note": load_prompt("shared/web_fetch_result", lang=lang)}
            yield {"read": [url]}
            yield {"result": wrap_untrusted_data(f"# Content from {url}\n\n{content}", url)}
        else:
            error = result.get("error", "Failed to fetch URL")
            log_message(f"❌ web_fetch failed: {error}")
            yield {"note": load_prompt("shared/web_fetch_failed_result", lang=lang)}
            yield {"result": json.dumps({"error": f"Could not fetch {url}: {error}"})}

    return [
        Tool(
            name="web_search",
            tier=TIER_READONLY,
            description=load_tool_description(__file__, "web_search"),
            parameters=load_tool_parameters(__file__, "web_search"),
            executor=_execute_web_search,
        ),
        Tool(
            name="web_fetch",
            tier=TIER_READONLY,
            description=load_tool_description(__file__, "web_fetch"),
            parameters=load_tool_parameters(__file__, "web_fetch"),
            executor=_execute_web_fetch,
        ),
    ]


@dataclass
class ResearchPlugin:
    name: str = "research"

    def is_available(self) -> bool:
        return True

    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        return get_research_tools(lang=ctx.lang, llm_history=ctx.llm_history, user_query=ctx.user_query,
                                  session_id=ctx.session_id)

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
