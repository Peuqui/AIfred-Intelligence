"""
Scraper Orchestrator - Parallel web scraping coordination

Handles:
- Scrape limit (RESEARCH_SCRAPE_URLS)
- Parallel scraping with ThreadPoolExecutor
- Progress reporting
- LLM preloading during scraping
"""

import asyncio
from collections.abc import AsyncIterator

from ..formatting import format_number
from ..logging_utils import log_message
from ..tools import scrape_webpage


async def orchestrate_scraping(
    related_urls: list[str],
    llm_client,
    model_choice: str,
    preload_num_ctx: int,
) -> AsyncIterator[dict]:
    """
    Orchestrate parallel web scraping

    Args:
        related_urls: List of URLs to scrape
        llm_client: Main LLM client (for preloading)
        model_choice: Main LLM model name
        preload_num_ctx: Context the following inference uses — from the
            caller's SSOT (get_agent_num_ctx / get_stateless_num_ctx). A
            different value would make Ollama reload the model at the first
            real request.

    Yields:
        Dict: Progress updates, debug messages, scraping results

    Returns (via last yield):
        Tuple[List[Dict], List[Dict]]: (scraped_results, tool_results)
    """
    scraped_results: list[dict] = []
    tool_results: list[dict] = []

    if not related_urls:
        log_message("⚠️ No URLs found, only abstract")
        yield {"type": "scraping_result", "data": (scraped_results, tool_results)}
        return

    log_message(f"📋 {len(related_urls)} URLs found from search engine")

    from ..config import RESEARCH_SCRAPE_URLS
    log_message(f"🔍 Scrape top {RESEARCH_SCRAPE_URLS} URLs")
    urls_to_scrape = related_urls[:RESEARCH_SCRAPE_URLS]

    yield {"type": "debug", "message": "🌐 Web scraping starting (parallel)"}

    # PERFORMANCE: load the main LLM while the pages are scraped. A cold start of a big model takes
    # minutes, also on vLLM (llama-swap unloads it after its ttl); preload_model returns at once
    # when it runs already.
    backend = llm_client._get_backend()
    preload_task = asyncio.create_task(backend.preload_model(model_choice, num_ctx=preload_num_ctx))
    preload_message_sent = False
    log_message(f"🚀 AIfred-LLM ({model_choice}) preloading... [Context={preload_num_ctx}]")
    yield {"type": "debug", "message": f"🚀 AIfred-LLM preloading... [Context={format_number(preload_num_ctx)}]"}

    # Parallel Scraping
    log_message(f"🚀 Parallel Scraping: {len(urls_to_scrape)} URLs simultaneously")

    # Start scraping progress
    yield {"type": "progress", "phase": "scraping", "current": 0, "total": len(urls_to_scrape), "failed": 0}

    # Parallel execution using asyncio.to_thread (non-blocking for event loop)
    all_results: list[tuple[str, int, dict]] = []  # Track all results for failed_sources

    async def _scrape_one(url: str, rank_idx: int) -> tuple[str, int, dict]:
        """Run scrape_webpage in a thread and return result with metadata."""
        result = await asyncio.to_thread(scrape_webpage, url)
        result['rank_index'] = rank_idx
        return url, rank_idx, result

    tasks = [_scrape_one(url, idx) for idx, url in enumerate(urls_to_scrape)]

    for completed_count, coro in enumerate(asyncio.as_completed(tasks), start=1):
        url, rank_idx, scrape_result = await coro
        all_results.append((url, rank_idx, scrape_result))
        url_short = url[:60] + '...' if len(url) > 60 else url

        if scrape_result.get('success'):
            tool_results.append(scrape_result)
            scraped_results.append(scrape_result)
            log_message(f"  ✅ {url_short}: {scrape_result['word_count']} words")
        else:
            log_message(f"  ❌ {url_short}: {scrape_result.get('error', 'Unknown')}")

        # Check if preload finished and send message immediately
        if not preload_message_sent and preload_task.done():
            yield {"type": "debug", "message": _preload_message(*preload_task.result())}
            preload_message_sent = True

        # Update progress
        failed_count = completed_count - len(scraped_results)
        yield {"type": "progress", "phase": "scraping", "current": len(scraped_results), "total": len(urls_to_scrape), "failed": failed_count}

    log_message(f"✅ Parallel Scraping done: {len(scraped_results)}/{len(urls_to_scrape)} successful")

    # ============================================================
    # COLLECT FAILED URLs for UI Display (with rank_index for sorting)
    # ============================================================
    failed_sources = []
    for url, rank_idx, result in all_results:
        if not result.get('success'):
            failed_sources.append({
                'url': url,
                'error': result.get('error', 'Unknown error'),
                'method': result.get('method', 'unknown'),
                'rank_index': rank_idx,
            })

    # Emit failed_sources for UI display (before AI response)
    if failed_sources:
        yield {"type": "failed_sources", "data": failed_sources}
        log_message(f"⚠️ {len(failed_sources)} URLs not scrapeable")

    # Wait for preload task to complete if not done yet
    if not preload_message_sent:
        yield {"type": "debug", "message": _preload_message(*await preload_task)}

    # Return results
    yield {"type": "scraping_result", "data": (scraped_results, tool_results)}


def _preload_message(success: bool, load_time: float) -> str:
    """Debug line for the outcome of preload_model (0.0 s = it ran already)."""
    if not success:
        return f"⚠️ AIfred-LLM preload failed ({format_number(load_time, 1)}s)"
    if load_time == 0.0:
        return "ℹ️ AIfred-LLM already loaded"
    return f"✅ AIfred-LLM preloaded ({format_number(load_time, 1)}s)"
