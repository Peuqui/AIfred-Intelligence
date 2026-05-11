"""
Scraper Orchestrator - Parallel web scraping coordination

Handles:
- Scraping strategy based on mode (quick/deep)
- Parallel scraping with ThreadPoolExecutor
- Progress reporting
- LLM preloading during scraping
"""

import asyncio
from typing import Dict, List, AsyncIterator

from ..tools import scrape_webpage
from ..logging_utils import log_message


async def orchestrate_scraping(
    related_urls: List[str],
    mode: str,
    llm_client,
    model_choice: str
) -> AsyncIterator[Dict]:
    """
    Orchestrate parallel web scraping

    Args:
        related_urls: List of URLs to scrape
        mode: Scraping mode ('quick' or 'deep')
        llm_client: Main LLM client (for preloading)
        model_choice: Main LLM model name

    Yields:
        Dict: Progress updates, debug messages, scraping results

    Returns (via last yield):
        Tuple[List[Dict], List[Dict]]: (scraped_results, tool_results)
    """
    scraped_results: List[Dict] = []
    tool_results: List[Dict] = []

    if not related_urls:
        log_message("⚠️ No URLs found, only abstract")
        yield {"type": "scraping_result", "data": (scraped_results, tool_results)}
        return

    log_message(f"📋 {len(related_urls)} URLs found from search engine")

    # Determine scraping strategy
    from ..config import RESEARCH_QUICK_URLS, RESEARCH_DEEP_URLS

    if mode == "deep":
        scrape_limit = RESEARCH_DEEP_URLS
        log_message(f"🔍 Deep mode: Scrape top {scrape_limit} URLs")
    else:
        scrape_limit = RESEARCH_QUICK_URLS
        log_message(f"⚡ Quick mode: Scrape top {scrape_limit} URLs")

    urls_to_scrape = related_urls[:scrape_limit]

    yield {"type": "debug", "message": "🌐 Web scraping starting (parallel)"}

    # PERFORMANCE: Preload Main LLM during scraping (only for backends that need it)
    # vLLM and TabbyAPI keep models loaded in VRAM, so preloading is unnecessary
    needs_preload = llm_client.backend_type not in ["vllm", "tabbyapi"]
    preload_task = None
    preload_message_sent = False
    unloaded_models = []

    if needs_preload:
        backend = llm_client._get_backend()

        # Use EXACTLY the same function as inference (context_builder.py line 162)
        # Empty messages list is OK - num_ctx calculation doesn't depend on message size
        from ..context_manager import calculate_dynamic_num_ctx
        calibrated_num_ctx, _ = await calculate_dynamic_num_ctx(
            llm_client, model_choice, messages=[], llm_options=None, enable_vram_limit=True
        )

        async def unload_and_preload():
            """Preload main LLM with calibrated num_ctx to avoid reload later"""
            success, load_time = await backend.preload_model(model_choice, num_ctx=calibrated_num_ctx)
            return (success, load_time, [])

        preload_task = asyncio.create_task(unload_and_preload())
        log_message(f"🚀 AIfred-LLM ({model_choice}) preloading... [Context={calibrated_num_ctx}]")
        yield {"type": "debug", "message": f"🚀 AIfred-LLM preloading... [Context={calibrated_num_ctx}]"}
    else:
        log_message(f"ℹ️ AIfred-LLM ({model_choice}) already loaded (Backend: {llm_client.backend_type})")
        yield {"type": "debug", "message": f"ℹ️ AIfred-LLM ({model_choice}) already loaded"}
        preload_message_sent = True  # Skip preload messages

    # Parallel Scraping
    log_message(f"🚀 Parallel Scraping: {len(urls_to_scrape)} URLs simultaneously")

    # Start scraping progress
    yield {"type": "progress", "phase": "scraping", "current": 0, "total": len(urls_to_scrape), "failed": 0}

    # Parallel execution using asyncio.to_thread (non-blocking for event loop)
    all_results: list[tuple[str, int, Dict]] = []  # Track all results for failed_sources

    async def _scrape_one(url: str, rank_idx: int) -> tuple[str, int, Dict]:
        """Run scrape_webpage in a thread and return result with metadata."""
        result = await asyncio.to_thread(scrape_webpage, url)
        result['rank_index'] = rank_idx
        return url, rank_idx, result

    tasks = [_scrape_one(url, idx) for idx, url in enumerate(urls_to_scrape)]
    completed_count = 0

    for coro in asyncio.as_completed(tasks):
        url, rank_idx, scrape_result = await coro
        completed_count += 1
        all_results.append((url, rank_idx, scrape_result))
        url_short = url[:60] + '...' if len(url) > 60 else url

        if scrape_result.get('success'):
            tool_results.append(scrape_result)
            scraped_results.append(scrape_result)
            log_message(f"  ✅ {url_short}: {scrape_result['word_count']} words")
        else:
            log_message(f"  ❌ {url_short}: {scrape_result.get('error', 'Unknown')}")

        # Check if preload finished and send message immediately
        if not preload_message_sent and preload_task and preload_task.done():
            try:
                success, load_time, unloaded_models = preload_task.result()

                if unloaded_models:
                    models_str = ", ".join(unloaded_models)
                    yield {"type": "debug", "message": f"🗑️ Unloaded models: {models_str}"}

                if success:
                    yield {"type": "debug", "message": f"✅ AIfred-LLM preloaded ({load_time:.1f}s)"}
                else:
                    yield {"type": "debug", "message": f"⚠️ AIfred-LLM preload failed ({load_time:.1f}s)"}
                preload_message_sent = True
            except Exception as e:
                log_message(f"⚠️ AIfred-LLM preload exception: {e}")
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
    if not preload_message_sent and preload_task:
        try:
            success, load_time, unloaded_models = await preload_task
            if unloaded_models:
                models_str = ", ".join(unloaded_models)
                yield {"type": "debug", "message": f"🗑️ Unloaded models: {models_str}"}

            # log_message via add_debug when yield processed
            if success:
                yield {"type": "debug", "message": f"✅ AIfred-LLM preloaded ({load_time:.1f}s)"}
            else:
                yield {"type": "debug", "message": f"⚠️ AIfred-LLM preload failed ({load_time:.1f}s)"}
        except Exception as e:
            log_message(f"⚠️ AIfred-LLM preload exception: {e}")

    # Return results
    yield {"type": "scraping_result", "data": (scraped_results, tool_results)}
