"""
Research Orchestrator - Top-Level Research Pipeline Coordination

This module contains the main research orchestration function that coordinates
all research pipeline modules (cache_handler, query_processor, scraper_orchestrator, context_builder).
"""

import time
from typing import Dict, List, Optional, AsyncIterator

from ..llm_client import LLMClient
from .cache_handler import handle_cache_hit
from .query_processor import process_query_and_search
from .scraper_orchestrator import orchestrate_scraping
from .context_builder import build_and_generate_response


async def perform_agent_research(
    user_text: str,
    stt_time: float,
    mode: str,
    model_choice: str,
    automatik_model: str,
    history: List,
    session_id: Optional[str] = None,
    temperature_mode: str = 'auto',
    temperature: float = 0.2,
    llm_options: Optional[Dict] = None,
    backend_type: str = "ollama",
    backend_url: Optional[str] = None
) -> AsyncIterator[Dict]:
    """
    Agent-Recherche mit Query-Optimierung und parallelemWeb-Scraping

    REFACTORED: Diese Funktion ist jetzt ein schlanker Orchestrator,
    der die eigentliche Arbeit an spezialisierte Module delegiert:
    - cache_handler: Cache-Hit Handling
    - query_processor: Query-Optimization + Web-Search
    - scraper_orchestrator: Parallel Web-Scraping
    - context_builder: Context-Building + LLM-Inference

    Args:
        user_text: User-Frage
        stt_time: STT-Zeit
        mode: "quick" oder "deep"
        model_choice: Haupt-LLM für finale Antwort
        automatik_model: Automatik-LLM für Query-Optimierung
        llm_options: Dict mit Ollama-Optionen (num_ctx, etc.) - Optional
        history: Chat History
        session_id: Session-ID für Research-Cache (optional)
        temperature_mode: 'auto' (Intent-Detection) oder 'manual' (fixer Wert)
        temperature: Temperature-Wert (0.0-2.0) - nur bei mode='manual'
        backend_type: LLM Backend ("ollama", "vllm", "tabbyapi")
        backend_url: Backend URL (optional, uses default if not provided)

    Yields:
        Dict with: {"type": "debug"|"content"|"result", ...}
    """

    agent_start = time.time()

    # Initialize LLM clients with correct backend
    llm_client = LLMClient(backend_type=backend_type, base_url=backend_url)
    automatik_llm_client = LLMClient(backend_type=backend_type, base_url=backend_url)

    # ==============================================================
    # PHASE 1: Cache-Hit Check
    # ==============================================================
    cache_handled = False
    async for item in handle_cache_hit(
        session_id=session_id,
        user_text=user_text,
        history=history,
        model_choice=model_choice,
        automatik_model=automatik_model,
        llm_client=llm_client,
        automatik_llm_client=automatik_llm_client,
        llm_options=llm_options,
        temperature_mode=temperature_mode,
        temperature=temperature,
        agent_start=agent_start
    ):
        if item["type"] == "result":
            cache_handled = True
        yield item

    if cache_handled:
        # Cache hit handled everything - we're done
        await llm_client.close()
        await automatik_llm_client.close()
        return

    # ==============================================================
    # PHASE 2: Query Optimization + Web Search
    # ==============================================================
    optimized_query = None
    query_reasoning = None
    query_opt_time = 0.0
    related_urls = []
    tool_results = []

    async for item in process_query_and_search(
        user_text=user_text,
        history=history,
        automatik_model=automatik_model,
        automatik_llm_client=automatik_llm_client
    ):
        if item["type"] == "query_result":
            optimized_query, query_reasoning, query_opt_time, related_urls, tool_results = item["data"]
        else:
            yield item

    # ==============================================================
    # PHASE 3: Parallel Web Scraping
    # ==============================================================
    scraped_results = []

    async for item in orchestrate_scraping(
        related_urls=related_urls,
        mode=mode,
        llm_client=llm_client,
        model_choice=model_choice
    ):
        if item["type"] == "scraping_result":
            scraped_results, scraping_tool_results = item["data"]
            tool_results.extend(scraping_tool_results)
        else:
            yield item

    # ==============================================================
    # PHASE 4: Context Building + LLM Response Generation
    # ==============================================================
    async for item in build_and_generate_response(
        user_text=user_text,
        scraped_results=scraped_results,
        tool_results=tool_results,
        history=history,
        session_id=session_id,
        mode=mode,
        model_choice=model_choice,
        automatik_model=automatik_model,
        query_reasoning=query_reasoning,
        query_opt_time=query_opt_time,
        llm_client=llm_client,
        automatik_llm_client=automatik_llm_client,
        llm_options=llm_options,
        temperature_mode=temperature_mode,
        temperature=temperature,
        agent_start=agent_start,
        stt_time=stt_time
    ):
        yield item

    # Cleanup
    await llm_client.close()
    await automatik_llm_client.close()
