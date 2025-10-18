"""
Agent Tools für Voice Assistant - Multi-API Version

Implementiert 3-Stufen-Fallback für Web-Suche:
1. Brave Search API (Primary) - 2.000/Monat, privacy-focused
2. Tavily AI (Fallback 1) - 1.000/Monat, RAG-optimiert
3. SearXNG (Last Resort) - Unlimited, self-hosted

Plus: Web-Scraping, Context-Building

Author: Claude Code
Date: 2025-10-13
"""

import requests
from bs4 import BeautifulSoup
import time
import logging
import os
from typing import Dict, List, Optional
import re
import json

# Logging Setup
logger = logging.getLogger(__name__)


# ============================================================
# EXCEPTION CLASSES
# ============================================================

class RateLimitError(Exception):
    """Wird geworfen wenn API Rate Limit erreicht ist"""
    pass


class APIKeyMissingError(Exception):
    """Wird geworfen wenn API Key fehlt"""
    pass


# ============================================================
# BASE TOOL CLASS
# ============================================================

class BaseTool:
    """Basis-Klasse für alle Agent-Tools"""

    def __init__(self):
        self.name = ""
        self.description = ""
        self.last_call_time = 0
        self.min_call_interval = 1.0  # Minimum 1s zwischen Aufrufen

    def execute(self, query: str, **kwargs) -> Dict:
        """
        Führt Tool aus und gibt Ergebnis zurück

        Args:
            query: Such-Query oder URL
            **kwargs: Zusätzliche Parameter

        Returns:
            Dict mit Tool-Ergebnis
        """
        raise NotImplementedError

    def _rate_limit_check(self):
        """Einfaches Rate-Limiting"""
        now = time.time()
        time_since_last_call = now - self.last_call_time

        if time_since_last_call < self.min_call_interval:
            wait_time = self.min_call_interval - time_since_last_call
            logger.debug(f"{self.name}: Rate limit, warte {wait_time:.1f}s")
            time.sleep(wait_time)

        self.last_call_time = time.time()


# ============================================================
# BRAVE SEARCH API (Primary)
# ============================================================

class BraveSearchTool(BaseTool):
    """
    Brave Search API - Primary Search Engine

    - 2.000 kostenlose Queries/Monat
    - Privacy-focused, eigener Index (30B+ Seiten)
    - Beste Qualität für News/aktuelle Events
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
        Führt Brave Search durch

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
            raise APIKeyMissingError("Brave API Key fehlt! Setze BRAVE_API_KEY env variable.")

        self._rate_limit_check()

        try:
            logger.info(f"🦁 Brave Search: {query}")

            response = requests.get(
                self.api_url,
                params={'q': query, 'count': 10},
                headers={
                    'Accept': 'application/json',
                    'X-Subscription-Token': self.api_key
                },
                timeout=15
            )

            # Check rate limit
            if response.status_code == 429:
                logger.warning("Brave Search: Rate Limit erreicht!")
                raise RateLimitError("Brave Search API rate limit exceeded")

            response.raise_for_status()
            data = response.json()

            # Extrahiere Ergebnisse
            web_results = data.get('web', {}).get('results', [])

            related_urls = []
            titles = []
            snippets = []

            for result in web_results[:10]:
                url = result.get('url', '')
                title = result.get('title', '')
                description = result.get('description', '')

                if url:
                    related_urls.append(url)
                    titles.append(title)
                    snippets.append(description)

            # Baue Content für Context
            content_parts = []
            for i, (title, snippet) in enumerate(zip(titles[:5], snippets[:5]), 1):
                content_parts.append(f"{i}. **{title}**: {snippet}")

            content = "\n\n".join(content_parts)

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

            logger.info(f"✅ Brave Search: {len(related_urls)} URLs gefunden")

            # DEBUG: Logge alle gefundenen URLs mit Titeln
            logger.info("📋 Brave Search Rohdaten (alle URLs vor KI-Bewertung):")
            for i, (url, title) in enumerate(zip(related_urls, titles), 1):
                logger.info(f"   {i}. {title}")
                logger.info(f"      URL: {url}")

            return result

        except RateLimitError:
            raise  # Re-raise für Fallback

        except Exception as e:
            logger.error(f"❌ Brave Search Fehler: {e}")
            return {
                'success': False,
                'source': 'Brave Search',
                'query': query,
                'related_urls': [],
                'error': str(e)
            }


# ============================================================
# TAVILY AI (Fallback 1)
# ============================================================

class TavilySearchTool(BaseTool):
    """
    Tavily AI - RAG-optimierte Suche

    - 1.000 kostenlose Queries/Monat
    - Speziell für AI/LLM gebaut
    - News-Filter eingebaut
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
        """Führt Tavily Search durch"""
        if not self.api_key:
            raise APIKeyMissingError("Tavily API Key fehlt! Setze TAVILY_API_KEY env variable.")

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
                logger.warning("Tavily: Rate Limit erreicht!")
                raise RateLimitError("Tavily API rate limit exceeded")

            response.raise_for_status()
            data = response.json()

            # Extrahiere Ergebnisse
            results = data.get('results', [])

            related_urls = []
            titles = []
            snippets = []

            for result in results:
                url = result.get('url', '')
                title = result.get('title', '')
                content = result.get('content', '')

                if url:
                    related_urls.append(url)
                    titles.append(title)
                    snippets.append(content)

            # Baue Content
            content_parts = []
            for i, (title, snippet) in enumerate(zip(titles[:5], snippets[:5]), 1):
                content_parts.append(f"{i}. **{title}**: {snippet}")

            content = "\n\n".join(content_parts)

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

            logger.info(f"✅ Tavily AI: {len(related_urls)} URLs gefunden")

            # DEBUG: Logge alle gefundenen URLs mit Titeln
            logger.info("📋 Tavily AI Rohdaten (alle URLs vor KI-Bewertung):")
            for i, (url, title) in enumerate(zip(related_urls, titles), 1):
                logger.info(f"   {i}. {title}")
                logger.info(f"      URL: {url}")

            return result

        except RateLimitError:
            raise

        except Exception as e:
            logger.error(f"❌ Tavily AI Fehler: {e}")
            return {
                'success': False,
                'source': 'Tavily AI',
                'query': query,
                'related_urls': [],
                'error': str(e)
            }


# ============================================================
# SEARXNG (Last Resort - Self-Hosted)
# ============================================================

class SearXNGSearchTool(BaseTool):
    """
    SearXNG - Self-Hosted Meta-Search

    - Unlimited (self-hosted)
    - Privacy-focused (keine Tracking)
    - Meta-Search (fragt Google/Bing/DDG ab)
    - Setup: docker run -p 8888:8080 searxng/searxng
    """

    def __init__(self, base_url: str = "http://localhost:8888"):
        super().__init__()
        self.name = "SearXNG"
        self.description = "Self-Hosted Meta-Search (Unlimited)"
        self.base_url = base_url.rstrip('/')
        self.min_call_interval = 0.5  # Lokal, kann schneller sein

    def execute(self, query: str, **kwargs) -> Dict:
        """Führt SearXNG Search durch"""
        self._rate_limit_check()

        try:
            logger.info(f"🌐 SearXNG (Self-Hosted): {query}")

            params = {
                'q': query,
                'format': 'json',
                'language': 'de',
                'pageno': 1
            }

            response = requests.get(
                f"{self.base_url}/search",
                params=params,
                timeout=15
            )

            response.raise_for_status()
            data = response.json()

            # Extrahiere Ergebnisse
            results = data.get('results', [])

            related_urls = []
            titles = []
            snippets = []

            for result in results[:10]:
                url = result.get('url', '')
                title = result.get('title', '')
                content = result.get('content', '')

                if url:
                    related_urls.append(url)
                    titles.append(title)
                    snippets.append(content)

            # Baue Content
            content_parts = []
            for i, (title, snippet) in enumerate(zip(titles[:5], snippets[:5]), 1):
                content_parts.append(f"{i}. **{title}**: {snippet}")

            content = "\n\n".join(content_parts)

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

            logger.info(f"✅ SearXNG: {len(related_urls)} URLs gefunden")

            # DEBUG: Logge alle gefundenen URLs mit Titeln
            logger.info("📋 SearXNG Rohdaten (alle URLs vor KI-Bewertung):")
            for i, (url, title) in enumerate(zip(related_urls, titles), 1):
                logger.info(f"   {i}. {title}")
                logger.info(f"      URL: {url}")

            return result

        except Exception as e:
            logger.error(f"❌ SearXNG Fehler: {e}")
            return {
                'success': False,
                'source': 'SearXNG',
                'query': query,
                'related_urls': [],
                'error': str(e)
            }


# ============================================================
# MULTI-API SEARCH mit FALLBACK
# ============================================================

class MultiAPISearchTool(BaseTool):
    """
    Meta-Tool: Nutzt alle Search APIs mit automatischem Fallback

    Reihenfolge:
    1. Brave Search (2.000/Monat) - Privacy-focused, beste Qualität
    2. Tavily AI (1.000/Monat) - AI-optimiert für RAG
    3. SearXNG (unlimited) - Self-hosted, immer verfügbar
    """

    def __init__(self,
                 brave_key: Optional[str] = None,
                 tavily_key: Optional[str] = None,
                 searxng_url: str = "http://localhost:8888"):
        super().__init__()
        self.name = "Multi-API Search"
        self.description = "3-Stufen Fallback Search"

        # Initialisiere alle APIs
        self.apis = []

        # Brave (Primary)
        if brave_key or os.getenv('BRAVE_API_KEY'):
            try:
                self.apis.append(BraveSearchTool(brave_key))
                logger.info("✅ Brave Search API aktiviert (Primary)")
            except:
                logger.warning("⚠️ Brave Search API konnte nicht initialisiert werden")

        # Tavily (Fallback 1)
        if tavily_key or os.getenv('TAVILY_API_KEY'):
            try:
                self.apis.append(TavilySearchTool(tavily_key))
                logger.info("✅ Tavily AI aktiviert (Fallback 1)")
            except:
                logger.warning("⚠️ Tavily AI konnte nicht initialisiert werden")

        # SearXNG (Last Resort - immer verfügbar wenn Server läuft)
        self.apis.append(SearXNGSearchTool(searxng_url))
        logger.info("✅ SearXNG aktiviert (Last Resort)")

    def execute(self, query: str, **kwargs) -> Dict:
        """
        Führt Suche mit automatischem Fallback durch

        Probiert APIs nacheinander bis eine funktioniert
        """
        last_error = None

        for api in self.apis:
            try:
                logger.info(f"🔄 Versuche: {api.name}")
                result = api.execute(query, **kwargs)

                if result.get('success') and result.get('related_urls'):
                    logger.info(f"✅ {api.name} erfolgreich!")
                    return result
                else:
                    logger.warning(f"⚠️ {api.name}: Keine URLs gefunden, probiere nächste API...")

            except (RateLimitError, APIKeyMissingError) as e:
                logger.warning(f"⚠️ {api.name}: {e}, probiere nächste API...")
                last_error = e
                continue

            except Exception as e:
                logger.error(f"❌ {api.name}: {e}, probiere nächste API...")
                last_error = e
                continue

        # Alle APIs fehlgeschlagen
        logger.error("❌ Alle Search APIs fehlgeschlagen!")
        return {
            'success': False,
            'source': 'Multi-API Search',
            'query': query,
            'related_urls': [],
            'error': f'Alle APIs fehlgeschlagen. Letzter Fehler: {last_error}'
        }


# ============================================================
# WEB SCRAPER TOOL (unverändert)
# ============================================================

class WebScraperTool(BaseTool):
    """
    Web-Scraper mit BeautifulSoup

    Extrahiert Text-Content von Webseiten
    """

    def __init__(self):
        super().__init__()
        self.name = "Web Scraper"
        self.description = "Extrahiert Text-Content von Webseiten"
        self.min_call_interval = 1.0
        self.max_content_length = 5000

    def execute(self, url: str, **kwargs) -> Dict:
        """Scraped eine Webseite"""
        self._rate_limit_check()

        max_chars = kwargs.get('max_chars', self.max_content_length)

        try:
            logger.info(f"🌐 Web Scraping: {url}")

            response = requests.get(
                url,
                timeout=10,
                headers={
                    'User-Agent': 'Mozilla/5.0 (compatible; Voice-Assistant/1.0)'
                }
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'lxml')

            # Titel
            title = soup.title.string if soup.title else ''
            title = title.strip() if title else ''

            # Entferne unnötige Tags
            for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'iframe']):
                tag.decompose()

            # Extrahiere Text
            text = soup.get_text(separator=' ', strip=True)
            text = self._clean_text(text)

            # Kürze
            was_truncated = len(text) > max_chars
            if was_truncated:
                text = text[:max_chars] + "..."

            word_count = len(text.split())

            logger.info(f"✅ Web Scraping: {word_count} Wörter extrahiert")

            return {
                'success': True,
                'source': url,
                'title': title,
                'content': text,
                'url': url,
                'word_count': word_count,
                'truncated': was_truncated
            }

        except Exception as e:
            logger.error(f"❌ Web Scraping Fehler bei {url}: {e}")
            return {
                'success': False,
                'source': url,
                'url': url,
                'error': str(e)
            }

    def _clean_text(self, text: str) -> str:
        """Säubert Text"""
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'\n+', '\n', text)
        return text.strip()


# ============================================================
# CONTEXT BUILDER (unverändert)
# ============================================================

def build_context(user_text: str, tool_results: List[Dict], max_length: int = 4000) -> str:
    """
    Baut strukturierten Kontext für AI aus Tool-Ergebnissen
    """
    context = f"# User-Frage\n{user_text}\n\n"
    context += "# Recherche-Ergebnisse\n\n"

    successful_results = [r for r in tool_results if r.get('success', False)]

    if not successful_results:
        context += "*Keine erfolgreichen Recherche-Ergebnisse gefunden.*\n\n"
    else:
        for i, result in enumerate(successful_results, 1):
            source = result.get('source', 'Unbekannt')
            content = result.get('content', result.get('abstract', ''))
            url = result.get('url', '')
            title = result.get('title', '')

            # Format: ## Quelle 1: Titel (URL)
            if title:
                context += f"## Quelle {i}: {title}\n"
            else:
                context += f"## Quelle {i}: {source}\n"

            # URL IMMER prominent anzeigen!
            if url:
                context += f"**🔗 URL:** {url}\n\n"

            if content:
                max_content_per_source = max_length // len(successful_results)
                if len(content) > max_content_per_source:
                    content = content[:max_content_per_source] + "..."

                context += f"{content}\n\n"

            context += "---\n\n"

    context += "# Aufgabe\n"
    context += "Beantworte die User-Frage basierend auf den Recherche-Ergebnissen oben. "
    context += "WICHTIG: Zitiere JEDE Quelle MIT ihrer URL! "
    context += "Format: 'Quelle 1 (https://...) schreibt...'\n\n"

    if len(context) > max_length:
        context = context[:max_length] + "\n\n*[Context gekürzt]*"

    logger.info(f"Context gebaut: {len(context)} Zeichen, {len(successful_results)} Quellen")

    return context


# ============================================================
# TOOL REGISTRY
# ============================================================

class ToolRegistry:
    """Verwaltet alle verfügbaren Tools"""

    def __init__(self):
        self.tools = {}
        self._register_default_tools()

    def _register_default_tools(self):
        """Registriert Standard-Tools"""
        # Multi-API Search als Primary
        self.register(MultiAPISearchTool())
        self.register(WebScraperTool())

    def register(self, tool: BaseTool):
        """Registriert ein Tool"""
        self.tools[tool.name] = tool
        logger.info(f"Tool registriert: {tool.name}")

    def get(self, name: str) -> Optional[BaseTool]:
        """Holt ein Tool nach Name"""
        return self.tools.get(name)

    def list_tools(self) -> List[str]:
        """Listet alle Tools"""
        return list(self.tools.keys())


# ============================================================
# GLOBALE TOOL REGISTRY
# ============================================================

_tool_registry = None

def get_tool_registry() -> ToolRegistry:
    """Holt globale Tool Registry (Singleton)"""
    global _tool_registry
    if _tool_registry is None:
        _tool_registry = ToolRegistry()
    return _tool_registry


# ============================================================
# CONVENIENCE FUNCTIONS
# ============================================================

def search_web(query: str) -> Dict:
    """
    Convenience-Funktion für Web-Suche mit Multi-API Fallback
    """
    registry = get_tool_registry()
    search_tool = registry.get("Multi-API Search")
    return search_tool.execute(query)


def scrape_webpage(url: str, max_chars: int = 5000) -> Dict:
    """
    Convenience-Funktion für Web-Scraping
    """
    registry = get_tool_registry()
    scraper_tool = registry.get("Web Scraper")
    return scraper_tool.execute(url, max_chars=max_chars)


# ============================================================
# MAIN (Testing)
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    print("=" * 60)
    print("MULTI-API SEARCH TEST")
    print("=" * 60)

    # Test Multi-API Search
    print("\n[TEST] Multi-API Search: Trump News")
    result = search_web("latest Trump news")

    print(f"\n✅ Success: {result['success']}")
    print(f"📡 Source: {result.get('source', 'N/A')}")
    print(f"🔗 URLs: {len(result.get('related_urls', []))}")

    if result.get('related_urls'):
        print(f"\nErste 3 URLs:")
        for i, url in enumerate(result['related_urls'][:3], 1):
            print(f"  {i}. {url}")

    print("\n" + "=" * 60)
