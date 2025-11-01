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
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..agent_tools import scrape_webpage
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
        log_message("⚠️ Keine URLs gefunden, nur Abstract")
        yield {"type": "scraping_result", "data": (scraped_results, tool_results)}
        return

    log_message(f"📋 {len(related_urls)} URLs von Search-Engine gefunden")

    # Determine scraping strategy
    if mode == "quick":
        target_sources = 3
        initial_scrape_count = 3
        log_message("⚡ Schnell-Modus: Scrape Top 3 URLs")
    elif mode == "deep":
        target_sources = 5
        initial_scrape_count = 7
        log_message(f"🔍 Ausführlich-Modus: Scrape Top {initial_scrape_count} URLs (Ziel: {target_sources} erfolgreiche)")
    else:
        target_sources = 3
        initial_scrape_count = 3

    scrape_limit = initial_scrape_count if mode == "deep" else target_sources
    urls_to_scrape = related_urls[:scrape_limit]

    yield {"type": "debug", "message": "🌐 Web-Scraping startet (parallel)"}

    # PERFORMANCE: Preload Main LLM during scraping
    asyncio.create_task(llm_client.preload_model(model_choice))
    log_message(f"🚀 Haupt-LLM ({model_choice}) wird parallel vorgeladen...")
    yield {"type": "debug", "message": f"🚀 Haupt-LLM ({model_choice}) wird vorgeladen..."}

    # Parallel Scraping
    log_message(f"🚀 Parallel Scraping: {len(urls_to_scrape)} URLs gleichzeitig")

    # Start scraping progress
    yield {"type": "progress", "phase": "scraping", "current": 0, "total": len(urls_to_scrape), "failed": 0}

    # Parallel execution
    with ThreadPoolExecutor(max_workers=min(5, len(urls_to_scrape))) as executor:
        future_to_url = {
            executor.submit(scrape_webpage, url): url
            for url in urls_to_scrape
        }

        # Collect results as they complete
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            url_short = url[:60] + '...' if len(url) > 60 else url

            try:
                scrape_result = future.result(timeout=10)

                if scrape_result['success']:
                    tool_results.append(scrape_result)
                    scraped_results.append(scrape_result)
                    log_message(f"  ✅ {url_short}: {scrape_result['word_count']} Wörter")
                else:
                    log_message(f"  ❌ {url_short}: {scrape_result.get('error', 'Unknown')}")

            except Exception as e:
                log_message(f"  ❌ {url_short}: Exception: {e}")

            # Update progress
            completed = len([f for f in future_to_url if f.done()])
            failed = completed - len(scraped_results)
            yield {"type": "progress", "phase": "scraping", "current": len(scraped_results), "total": len(urls_to_scrape), "failed": failed}

    log_message(f"✅ Parallel Scraping fertig: {len(scraped_results)}/{len(urls_to_scrape)} erfolgreich")

    # Return results
    yield {"type": "scraping_result", "data": (scraped_results, tool_results)}
