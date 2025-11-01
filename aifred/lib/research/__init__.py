"""
Research Module - Modular Research Pipeline

Dieses Modul teilt die große perform_agent_research() Funktion auf in:
- cache_handler: Cache-Check und Metadata-Handling
- query_processor: Query-Optimization und Web-Search
- scraper_orchestrator: Parallel Web-Scraping
- context_builder: Context-Building und LLM-Inference
"""

from .cache_handler import handle_cache_hit
from .query_processor import process_query_and_search
from .scraper_orchestrator import orchestrate_scraping
from .context_builder import build_and_generate_response

__all__ = [
    'handle_cache_hit',
    'process_query_and_search',
    'orchestrate_scraping',
    'build_and_generate_response',
]
