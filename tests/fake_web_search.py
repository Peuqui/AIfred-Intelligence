"""Stand-ins for the chain of a web search (lib/research_tools.run_web_search): settings, model client, search
APIs and scraper. The search answers without titles, so the URL ranking is skipped; building the context runs
for real. For the tests of the search and of the phases a turn reports."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import pytest


@dataclass
class FakeWebSearch:
    urls: list[str]                     # what the search found
    pages: list[dict[str, Any]]         # what the scraper hands back (the pages it could read)
    progress: list[tuple[int, int]] = field(default_factory=list)   # (pages read, unreachable) per step
    after_progress: Callable[[], None] = lambda: None                # runs after each step

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from aifred.lib import config, llm_client, settings
        from aifred.lib.research import context_utils, query_processor, scraper_orchestrator

        search_answer = {"success": True, "source": "Multi-API Search (Multi-Query)", "related_urls": self.urls}

        async def search(**_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
            yield {"type": "query_result", "data": (None, None, None, self.urls, [], [], [search_answer])}

        async def scrape(**_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
            for current, failed in self.progress:
                yield {"type": "progress", "phase": "scraping", "current": current, "total": len(self.urls), "failed": failed}
                self.after_progress()
            yield {"type": "scraping_result", "data": (self.pages, self.pages)}

        class _Client:
            def __init__(self, **_kwargs: Any) -> None: ...
            async def close(self) -> None: ...

        monkeypatch.setattr(settings, "load_settings", dict)
        monkeypatch.setattr(config, "get_effective_model_from_settings", lambda _agent="aifred": "qwen3:8b")
        monkeypatch.setattr(llm_client, "LLMClient", _Client)
        monkeypatch.setattr(query_processor, "process_query_and_search", search)
        monkeypatch.setattr(context_utils, "get_model_native_context", lambda *_args: 32768)
        monkeypatch.setattr(context_utils, "get_stateless_num_ctx", lambda *_args: (32768, "test"))
        monkeypatch.setattr(scraper_orchestrator, "orchestrate_scraping", scrape)
