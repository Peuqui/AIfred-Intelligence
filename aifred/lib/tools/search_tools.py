"""
Search Tool Classes - Web Search APIs

Extracted from agent_tools.py for better modularity.
Includes: Brave, Tavily, SearXNG, MultiAPI Search
"""

import httpx
import requests
import logging
import os
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from .base import BaseTool, RateLimitError, APIKeyMissingError
from .url_utils import deduplicate_urls, deduplicate_urls_with_metadata

# Logging Setup
logger = logging.getLogger(__name__)


def classify_search_error(exc: BaseException) -> str:
    """Classify a search-request failure so callers can tell 'offline' apart
    from a server-side problem.

    Returns ``'network'`` when the request never reached a server (DNS
    resolution failure, connection refused, timeout) — i.e. the host is
    effectively offline. Returns ``'http'`` for everything else (the server
    answered, just with an error). This lets the aggregation distinguish a
    genuine "nothing found" from "we couldn't even ask", so the agent never
    mistakes a network outage for an empty result.
    """
    network_types = (
        requests.exceptions.ConnectionError,  # incl. NameResolutionError (DNS)
        requests.exceptions.Timeout,          # incl. ConnectTimeout/ReadTimeout
        httpx.ConnectError,
        httpx.TimeoutException,
    )
    return "network" if isinstance(exc, network_types) else "http"

# ============================================================
# BRAVE SEARCH API (Primary)
# ============================================================

class BraveSearchTool(BaseTool):
    """
    Brave Search API - Primary Search Engine

    - 2,000 free queries/month
    - Privacy-focused, own index (30B+ pages)
    - Best quality for news/current events
    - API Key: https://brave.com/search/api/
    """

    def __init__(self, api_key: Optional[str] = None):
        super().__init__()
        self.name = "Brave Search"
        self.description = "Brave Search API (Primary)"
        self.api_key = api_key or os.getenv('BRAVE_API_KEY')
        self.api_url = "https://api.search.brave.com/res/v1/web/search"
        self.min_call_interval = 1.0

    def execute(self, query: str, **kwargs) -> Dict:
        """
        Execute Brave Search

        Returns:
            {
                'success': bool,
                'source': 'Brave Search',
                'query': str,
                'related_urls': List[str],
                'titles': List[str],
                'snippets': List[str],
                'content': str,
                'error': str (optional)
            }
        """
        if not self.api_key:
            raise APIKeyMissingError("Brave API Key missing! Set BRAVE_API_KEY env variable.")

        self._rate_limit_check()

        try:
            logger.info(f"🦁 Brave Search: {query}")

            response = requests.get(
                self.api_url,
                params={'q': query, 'count': '10'},
                headers={
                    'Accept': 'application/json',
                    'X-Subscription-Token': self.api_key
                },
                timeout=15
            )

            # Check rate limit
            if response.status_code == 429:
                logger.warning("Brave Search: Rate limit reached!")
                raise RateLimitError("Brave Search API rate limit exceeded")

            response.raise_for_status()
            data = response.json()

            # Extract results
            web_results = data.get('web', {}).get('results', [])
            related_urls, titles, snippets, content = self._extract_urls_from_results(
                web_results, url_key='url', title_key='title', content_key='description', max_results=10
            )

            result = {
                'success': True,
                'source': 'Brave Search',
                'query': query,
                'related_urls': related_urls,
                'titles': titles,
                'snippets': snippets,
                'content': content,
                'url': related_urls[0] if related_urls else ''
            }

            logger.info(f"✅ Brave Search: {len(related_urls)} URLs found")

            # DEBUG: Log all found URLs with titles
            logger.info("📋 Brave Search raw data (all URLs before AI ranking):")
            for i, (url, title) in enumerate(zip(related_urls, titles), 1):
                logger.info(f"   {i}. {title}")
                logger.info(f"      URL: {url}")

            return result

        except RateLimitError:
            raise  # Re-raise for fallback

        except (requests.exceptions.RequestException, httpx.HTTPError) as e:
            logger.error(f"❌ Brave Search error: {e}")
            return {
                'success': False,
                'source': 'Brave Search',
                'query': query,
                'related_urls': [],
                'error': str(e),
                'error_type': classify_search_error(e)
            }


# ============================================================
# TAVILY AI (Fallback 1)
# ============================================================

class TavilySearchTool(BaseTool):
    """
    Tavily AI - RAG-optimized Search

    - 1,000 free queries/month
    - Built specifically for AI/LLM
    - Built-in news filter
    - API Key: https://www.tavily.com/
    """

    def __init__(self, api_key: Optional[str] = None):
        super().__init__()
        self.name = "Tavily AI"
        self.description = "Tavily AI Search (RAG-optimiert)"
        self.api_key = api_key or os.getenv('TAVILY_API_KEY')
        self.api_url = "https://api.tavily.com/search"
        self.min_call_interval = 1.0

    def execute(self, query: str, **kwargs) -> Dict:
        """Execute Tavily Search"""
        if not self.api_key:
            raise APIKeyMissingError("Tavily API Key missing! Set TAVILY_API_KEY env variable.")

        self._rate_limit_check()

        try:
            logger.info(f"🔍 Tavily AI: {query}")

            payload = {
                'api_key': self.api_key,
                'query': query,
                'search_depth': 'basic',  # basic oder advanced
                'include_answer': False,
                'include_raw_content': False,
                'max_results': 10,
                'include_domains': [],
                'exclude_domains': []
            }

            response = requests.post(
                self.api_url,
                json=payload,
                timeout=15
            )

            if response.status_code == 429:
                logger.warning("Tavily: Rate limit reached!")
                raise RateLimitError("Tavily API rate limit exceeded")

            response.raise_for_status()
            data = response.json()

            # Extract results
            results = data.get('results', [])
            related_urls, titles, snippets, content = self._extract_urls_from_results(
                results, url_key='url', title_key='title', content_key='content', max_results=10
            )

            result = {
                'success': True,
                'source': 'Tavily AI',
                'query': query,
                'related_urls': related_urls,
                'titles': titles,
                'snippets': snippets,
                'content': content,
                'url': related_urls[0] if related_urls else ''
            }

            logger.info(f"✅ Tavily AI: {len(related_urls)} URLs found")

            # DEBUG: Log all found URLs with titles
            logger.info("📋 Tavily AI raw data (all URLs before AI ranking):")
            for i, (url, title) in enumerate(zip(related_urls, titles), 1):
                logger.info(f"   {i}. {title}")
                logger.info(f"      URL: {url}")

            return result

        except RateLimitError:
            raise

        except (requests.exceptions.RequestException, httpx.HTTPError) as e:
            logger.error(f"❌ Tavily AI error: {e}")
            return {
                'success': False,
                'source': 'Tavily AI',
                'query': query,
                'related_urls': [],
                'error': str(e),
                'error_type': classify_search_error(e)
            }


# ============================================================
# SEARXNG (Last Resort - Self-Hosted)
# ============================================================

class SearXNGSearchTool(BaseTool):
    """
    SearXNG - Self-Hosted Meta-Search

    - Unlimited (self-hosted)
    - Privacy-focused (no tracking)
    - Meta-Search (queries Google/Bing/DDG)
    - Setup: docker run -p 8888:8080 searxng/searxng
    """

    def __init__(self, base_url: str = "http://localhost:8888"):
        super().__init__()
        self.name = "SearXNG"
        self.description = "Self-Hosted Meta-Search (Unlimited)"
        self.base_url = base_url.rstrip('/')
        self.min_call_interval = 0.5  # Local, can be faster

    def execute(self, query: str, **kwargs) -> Dict:
        """Execute SearXNG Search"""
        self._rate_limit_check()

        try:
            logger.info(f"🌐 SearXNG (Self-Hosted): {query}")

            params = {
                'q': query,
                'format': 'json',
                'language': 'de',
                'pageno': '1'
            }

            response = requests.get(
                f"{self.base_url}/search",
                params=params,
                timeout=15
            )

            response.raise_for_status()
            data = response.json()

            # Extract results
            results = data.get('results', [])
            related_urls, titles, snippets, content = self._extract_urls_from_results(
                results, url_key='url', title_key='title', content_key='content', max_results=10
            )

            result = {
                'success': True,
                'source': 'SearXNG (Self-Hosted)',
                'query': query,
                'related_urls': related_urls,
                'titles': titles,
                'snippets': snippets,
                'content': content,
                'url': related_urls[0] if related_urls else ''
            }

            logger.info(f"✅ SearXNG: {len(related_urls)} URLs found")

            # DEBUG: Log all found URLs with titles
            logger.info("📋 SearXNG raw data (all URLs before AI ranking):")
            for i, (url, title) in enumerate(zip(related_urls, titles), 1):
                logger.info(f"   {i}. {title}")
                logger.info(f"      URL: {url}")

            return result

        except (requests.exceptions.RequestException, httpx.HTTPError) as e:
            logger.error(f"❌ SearXNG error: {e}")
            return {
                'success': False,
                'source': 'SearXNG',
                'query': query,
                'related_urls': [],
                'error': str(e),
                'error_type': classify_search_error(e)
            }


# ============================================================
# MULTI-API SEARCH mit FALLBACK
# ============================================================

class MultiAPISearchTool(BaseTool):
    """
    Meta-Tool: Uses all Search APIs with automatic fallback

    Order:
    1. Tavily AI (1,000/month) - AI-optimized for RAG, most current articles
    2. Brave Search (2,000/month) - Privacy-focused, good quality
    3. SearXNG (unlimited) - Self-hosted, always available
    """

    def __init__(self,
                 brave_key: Optional[str] = None,
                 tavily_key: Optional[str] = None,
                 searxng_url: str = "http://localhost:8888"):
        super().__init__()
        self.name = "Multi-API Search"
        self.description = "3-Tier Fallback Search"

        # Initialize all APIs with explicit type. Order matters because
        # the multi-query loop hits them round-robin: query 1 → apis[0],
        # query 2 → apis[1], etc. Self-hosted SearXNG comes first so the
        # most common case (≤2 queries) always includes a private,
        # unlimited source — and because AIfred re-ranks + scrapes URLs
        # itself, Tavily's pre-filtered snippets aren't worth more than
        # SearXNG's broader meta-search aggregation.
        self.apis: List[BaseTool] = []

        # SearXNG (Primary) - Self-hosted, unlimited, private
        self.apis.append(SearXNGSearchTool(searxng_url))
        logger.info("✅ SearXNG enabled (Primary, self-hosted)")

        # Tavily (Fallback 1) - AI-optimized snippets, good actuality
        if tavily_key or os.getenv('TAVILY_API_KEY'):
            try:
                self.apis.append(TavilySearchTool(tavily_key))
                logger.info("✅ Tavily AI enabled (Fallback 1)")
            except (APIKeyMissingError, ValueError, RuntimeError) as e:
                logger.warning(f"⚠️ Tavily AI could not be initialized: {e}")

        # Brave (Fallback 2)
        if brave_key or os.getenv('BRAVE_API_KEY'):
            try:
                self.apis.append(BraveSearchTool(brave_key))
                logger.info("✅ Brave Search API enabled (Fallback 2)")
            except (APIKeyMissingError, ValueError, RuntimeError) as e:
                logger.warning(f"⚠️ Brave Search API could not be initialized: {e}")

    @staticmethod
    def _outcome_kind(result: Dict) -> str:
        """Classify a single API result dict for the network-vs-empty decision:
        'urls' (usable hits), 'empty' (server answered but 0 results),
        'network' (request never reached a server), or 'http' (server error).
        """
        if result.get("success") and result.get("related_urls"):
            return "urls"
        if result.get("success"):
            return "empty"  # reached server, just no hits
        return str(result.get("error_type", "http"))  # 'network' or 'http'

    def execute(self, query: str, **kwargs) -> Dict:
        """
        Execute search in PARALLEL - collect URLs from ALL APIs!

        Parallel Execution: All APIs start simultaneously.
        Collect All: Wait for all APIs, collect all URLs.
        Deduplication: Remove duplicate URLs (www, trailing slash, etc.)
        """
        if not self.apis:
            logger.error("❌ No Search APIs configured!")
            return {
                'success': False,
                'source': 'Multi-API Search',
                'query': query,
                'related_urls': [],
                'error': 'No Search APIs available'
            }

        logger.info(f"🚀 Parallel Search: {len(self.apis)} APIs simultaneously")

        # Parallel Execution - Collect ALL results
        all_urls = []
        successful_apis = []
        failed_apis = []
        network_failures = 0  # APIs that never reached a server (DNS/conn/timeout)
        server_reached = 0    # APIs that got an answer (hits, empty, or HTTP error)

        with ThreadPoolExecutor(max_workers=len(self.apis)) as executor:
            # Start all APIs in parallel
            future_to_api = {
                executor.submit(api.execute, query, **kwargs): api
                for api in self.apis
            }

            # Collect results from ALL APIs
            for future in as_completed(future_to_api):
                api = future_to_api[future]
                try:
                    result = future.result(timeout=15)  # Max 15s per API
                    kind = self._outcome_kind(result)

                    if kind == "urls":
                        urls = result['related_urls']
                        logger.info(f"✅ {api.name}: {len(urls)} URLs found")
                        all_urls.extend(urls)
                        successful_apis.append(api.name)
                        server_reached += 1
                    elif kind == "network":
                        logger.warning(f"⚠️ {api.name}: unreachable ({result.get('error')})")
                        failed_apis.append((api.name, "network"))
                        network_failures += 1
                    else:  # 'empty' or 'http' — server answered
                        logger.warning(f"⚠️ {api.name}: No URLs found")
                        failed_apis.append((api.name, "No URLs"))
                        server_reached += 1

                except RateLimitError as e:
                    logger.warning(f"⚠️ {api.name}: {e}")
                    failed_apis.append((api.name, str(e)))
                    server_reached += 1  # 429 means the server did answer

                except APIKeyMissingError as e:
                    logger.warning(f"⚠️ {api.name}: {e}")
                    failed_apis.append((api.name, str(e)))  # config issue, neither

                except Exception as e:
                    logger.error(f"❌ {api.name}: {e}")
                    failed_apis.append((api.name, str(e)))
                    network_failures += 1  # future timeout / never completed

        # At least one API successful?
        if not all_urls:
            logger.error("❌ All Search APIs failed!")
            error_summary = ", ".join([f"{name}: {err}" for name, err in failed_apis])
            response = {
                'success': False,
                'source': 'Multi-API Search',
                'query': query,
                'related_urls': [],
                'error': f'All APIs failed. Details: {error_summary}'
            }
            # Pure network outage: no server was ever reached, yet at least one
            # API failed on the network. Flag it so callers report "offline"
            # instead of "no results" (which the agent would treat as fact).
            if network_failures > 0 and server_reached == 0:
                response['error_type'] = 'network'
            return response

        # Deduplication
        unique_urls = deduplicate_urls(all_urls)

        logger.info(f"🔄 Collected: {len(all_urls)} URLs from {len(successful_apis)} APIs → {len(unique_urls)} unique URLs")

        return {
            'success': True,
            'source': 'Multi-API Search',
            'apis_used': successful_apis,
            'query': query,
            'related_urls': unique_urls,
            'stats': {
                'total_urls': len(all_urls),
                'unique_urls': len(unique_urls),
                'duplicates_removed': len(all_urls) - len(unique_urls),
                'successful_apis': len(successful_apis),
                'failed_apis': len(failed_apis)
            }
        }

    def execute_multi_query(self, queries: List[str], **kwargs) -> Dict:
        """
        Distribute multiple queries across available APIs (1:1 mapping)

        Query 1 → API 1 (Tavily)
        Query 2 → API 2 (Brave)
        Query 3 → API 3 (SearXNG)

        If more queries than APIs: Round-Robin (Query 4 → API 1, etc.)
        If fewer queries than APIs: Only the first N APIs are used

        Args:
            queries: List of search queries (typically 1-3)

        Returns:
            Dict with aggregated results and detailed stats
        """
        if not queries:
            logger.error("❌ No queries provided!")
            return {
                'success': False,
                'source': 'Multi-API Search (Multi-Query)',
                'queries': [],
                'related_urls': [],
                'error': 'No queries provided'
            }

        if not self.apis:
            logger.error("❌ No Search APIs configured!")
            return {
                'success': False,
                'source': 'Multi-API Search (Multi-Query)',
                'queries': queries,
                'related_urls': [],
                'error': 'No Search APIs available'
            }

        num_queries = len(queries)
        num_apis = len(self.apis)

        logger.info(f"🚀 Multi-Query Search: {num_queries} Queries → {num_apis} APIs")

        # Create Query-API mapping (Round-Robin when more queries than APIs)
        query_api_pairs = []
        for i, query in enumerate(queries):
            api = self.apis[i % num_apis]  # Round-Robin
            query_api_pairs.append((query, api))
            logger.info(f"   Query {i+1} → {api.name}: {query[:50]}...")

        # Parallel Execution
        all_urls = []
        all_titles = []
        all_snippets = []
        successful_apis = []
        failed_apis = []
        query_results = []  # Detailed results per query
        network_failures = 0  # queries whose API never reached a server
        server_reached = 0    # queries whose API answered (hits, empty, or error)

        with ThreadPoolExecutor(max_workers=len(query_api_pairs)) as executor:
            # Start all Query-API combinations in parallel
            future_to_pair = {
                executor.submit(api.execute, query, **kwargs): (query, api)
                for query, api in query_api_pairs
            }

            # Collect results
            for future in as_completed(future_to_pair):
                query, api = future_to_pair[future]
                try:
                    result = future.result(timeout=15)
                    kind = self._outcome_kind(result)

                    if kind == "urls":
                        urls = result['related_urls']
                        titles = result.get('titles', [])
                        snippets = result.get('snippets', [])
                        logger.info(f"✅ {api.name} ({query[:30]}...): {len(urls)} URLs")
                        all_urls.extend(urls)
                        all_titles.extend(titles + [""] * (len(urls) - len(titles)))
                        all_snippets.extend(snippets + [""] * (len(urls) - len(snippets)))
                        successful_apis.append(api.name)
                        server_reached += 1
                        query_results.append({
                            'query': query,
                            'api': api.name,
                            'urls_found': len(urls),
                            'success': True
                        })
                    else:
                        is_net = kind == "network"
                        if is_net:
                            logger.warning(f"⚠️ {api.name} ({query[:30]}...): unreachable")
                            network_failures += 1
                        else:  # 'empty' or 'http' — server answered
                            logger.warning(f"⚠️ {api.name} ({query[:30]}...): No URLs")
                            server_reached += 1
                        failed_apis.append((api.name, "network" if is_net else "No URLs"))
                        query_results.append({
                            'query': query,
                            'api': api.name,
                            'urls_found': 0,
                            'success': False,
                            'error': result.get('error', 'No URLs found'),
                            'error_type': result.get('error_type') if is_net else None
                        })

                except RateLimitError as e:
                    logger.warning(f"⚠️ {api.name}: {e}")
                    failed_apis.append((api.name, str(e)))
                    server_reached += 1  # 429 means the server did answer
                    query_results.append({
                        'query': query, 'api': api.name, 'urls_found': 0,
                        'success': False, 'error': str(e)
                    })

                except APIKeyMissingError as e:
                    logger.warning(f"⚠️ {api.name}: {e}")
                    failed_apis.append((api.name, str(e)))  # config issue, neither
                    query_results.append({
                        'query': query, 'api': api.name, 'urls_found': 0,
                        'success': False, 'error': str(e)
                    })

                except Exception as e:
                    logger.error(f"❌ {api.name}: {e}")
                    failed_apis.append((api.name, str(e)))
                    network_failures += 1  # future timeout / never completed
                    query_results.append({
                        'query': query, 'api': api.name, 'urls_found': 0,
                        'success': False, 'error': str(e)
                    })

        # At least one query successful?
        if not all_urls:
            logger.error("❌ All queries failed!")
            error_summary = ", ".join([f"{name}: {err}" for name, err in failed_apis])
            response = {
                'success': False,
                'source': 'Multi-API Search (Multi-Query)',
                'queries': queries,
                'related_urls': [],
                'titles': [],
                'snippets': [],
                'query_results': query_results,
                'error': f'All queries failed. Details: {error_summary}'
            }
            # Pure network outage: no server reached, at least one network fail.
            if network_failures > 0 and server_reached == 0:
                response['error_type'] = 'network'
            return response

        # Deduplication (with metadata preservation)
        unique_urls, unique_titles, unique_snippets = deduplicate_urls_with_metadata(
            all_urls, all_titles, all_snippets
        )

        logger.info(f"🔄 Multi-Query result: {len(all_urls)} URLs → {len(unique_urls)} unique")

        return {
            'success': True,
            'source': 'Multi-API Search (Multi-Query)',
            'apis_used': list(set(successful_apis)),  # Unique API names
            'queries': queries,
            'related_urls': unique_urls,
            'titles': unique_titles,
            'snippets': unique_snippets,
            'query_results': query_results,
            'stats': {
                'total_queries': num_queries,
                'successful_queries': len([r for r in query_results if r['success']]),
                'total_urls': len(all_urls),
                'unique_urls': len(unique_urls),
                'duplicates_removed': len(all_urls) - len(unique_urls),
                'successful_apis': len(set(successful_apis)),
                'failed_apis': len(failed_apis)
            }
        }

