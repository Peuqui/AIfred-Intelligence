"""
Research Module — building blocks of the research pipeline
(``lib/research_tools.py``: execute_research / hub_web_search):

- query_processor: multi-API web search with the given queries
- scraper_orchestrator: parallel web scraping
- url_ranker: LLM-based URL relevance ranking
- context_utils: native model context and per-agent num_ctx
"""

from .query_processor import process_query_and_search
from .scraper_orchestrator import orchestrate_scraping

__all__ = [
    'process_query_and_search',
    'orchestrate_scraping',
]
