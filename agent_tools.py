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
import time
import logging
import os
from typing import Dict, List, Optional
import re
import trafilatura
from trafilatura.settings import DEFAULT_CONFIG
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed

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
        # name, description und min_call_interval werden von Subclasses gesetzt
        self.last_call_time = 0

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
    1. Tavily AI (1.000/Monat) - AI-optimiert für RAG, aktuellste Artikel
    2. Brave Search (2.000/Monat) - Privacy-focused, gute Qualität
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

        # Tavily (Primary) - AI-optimiert, bessere Aktualität
        if tavily_key or os.getenv('TAVILY_API_KEY'):
            try:
                self.apis.append(TavilySearchTool(tavily_key))
                logger.info("✅ Tavily AI aktiviert (Primary)")
            except:
                logger.warning("⚠️ Tavily AI konnte nicht initialisiert werden")

        # Brave (Fallback 1)
        if brave_key or os.getenv('BRAVE_API_KEY'):
            try:
                self.apis.append(BraveSearchTool(brave_key))
                logger.info("✅ Brave Search API aktiviert (Fallback 1)")
            except:
                logger.warning("⚠️ Brave Search API konnte nicht initialisiert werden")

        # SearXNG (Last Resort - immer verfügbar wenn Server läuft)
        self.apis.append(SearXNGSearchTool(searxng_url))
        logger.info("✅ SearXNG aktiviert (Last Resort)")

    def execute(self, query: str, **kwargs) -> Dict:
        """
        Führt Suche PARALLEL durch - erste API die antwortet gewinnt!

        Race Condition: Alle APIs starten gleichzeitig, schnellste gewinnt.
        Fallback: Wenn Primary fehlschlägt, warte auf nächste.
        """
        if not self.apis:
            logger.error("❌ Keine Search APIs konfiguriert!")
            return {
                'success': False,
                'source': 'Multi-API Search',
                'query': query,
                'related_urls': [],
                'error': 'Keine Search APIs verfügbar'
            }

        logger.info(f"🚀 Parallel Search: {len(self.apis)} APIs gleichzeitig")

        # Parallel Execution mit Race Condition
        with ThreadPoolExecutor(max_workers=len(self.apis)) as executor:
            # Starte alle APIs parallel
            future_to_api = {
                executor.submit(api.execute, query, **kwargs): api
                for api in self.apis
            }

            # Erste erfolgreiche Antwort gewinnt
            results = []
            for future in as_completed(future_to_api):
                api = future_to_api[future]
                try:
                    result = future.result(timeout=15)  # Max 15s pro API

                    # Erfolgreiche Antwort mit URLs?
                    if result.get('success') and result.get('related_urls'):
                        logger.info(f"🏆 {api.name} gewonnen! ({len(result['related_urls'])} URLs)")
                        # Cancel alle anderen Requests (Best Effort)
                        for f in future_to_api:
                            f.cancel()
                        return result
                    else:
                        logger.warning(f"⚠️ {api.name}: Keine URLs gefunden")
                        results.append((api.name, result))

                except (RateLimitError, APIKeyMissingError) as e:
                    logger.warning(f"⚠️ {api.name}: {e}")
                    results.append((api.name, str(e)))

                except Exception as e:
                    logger.error(f"❌ {api.name}: {e}")
                    results.append((api.name, str(e)))

        # Alle APIs fehlgeschlagen
        logger.error("❌ Alle Search APIs fehlgeschlagen!")
        error_summary = ", ".join([f"{name}: {err}" for name, err in results])
        return {
            'success': False,
            'source': 'Multi-API Search',
            'query': query,
            'related_urls': [],
            'error': f'Alle APIs fehlgeschlagen. Details: {error_summary}'
        }


# ============================================================
# WEB SCRAPER TOOL (unverändert)
# ============================================================

class WebScraperTool(BaseTool):
    """
    Web-Scraper mit trafilatura + Playwright Fallback

    Extrahiert sauberen Text-Content von Webseiten.
    trafilatura filtert automatisch Werbung, Navigation und Cookie-Banner.
    """

    # Konstanten
    PLAYWRIGHT_FALLBACK_THRESHOLD = 800  # Wörter - unter diesem Wert wird Playwright versucht

    def __init__(self):
        super().__init__()
        self.name = "Web Scraper"
        self.description = "Extrahiert Text-Content von Webseiten"
        self.min_call_interval = 1.0

        # trafilatura Config mit 15s Timeout (statt default 30s)
        self.trafilatura_config = deepcopy(DEFAULT_CONFIG)
        self.trafilatura_config.set('DEFAULT', 'DOWNLOAD_TIMEOUT', '15')
    def execute(self, url: str, **kwargs) -> Dict:
        """
        Scraped eine Webseite komplett ohne Längenlimit

        Strategie (2-Stufen Fallback):
        1. trafilatura (sauberster Content, filtert Werbung/Navigation/Cookies automatisch)
        2. Falls < threshold ODER fehlgeschlagen → Playwright (JavaScript-Rendering)

        trafilatura funktioniert für 95% aller Websites (News, Blogs, Wetter).
        Playwright nur für JavaScript-heavy Single-Page-Apps (React, Vue, etc.).

        Ollama's dynamisches num_ctx übernimmt die Context-Größen-Kontrolle!
        """
        self._rate_limit_check()

        # Versuch 1: trafilatura (schnell + sauber)
        result = self._scrape_with_trafilatura(url)

        # Versuch 2: Playwright Fallback (nur wenn nötig)
        should_retry_with_playwright = (
            not result['success'] or  # trafilatura fehlgeschlagen
            result.get('word_count', 0) < self.PLAYWRIGHT_FALLBACK_THRESHOLD  # Zu wenig Content (wahrscheinlich JavaScript)
        )

        if should_retry_with_playwright:
            if not result['success']:
                logger.warning(f"⚠️ trafilatura fehlgeschlagen → Retry mit Playwright (JavaScript)")
            else:
                logger.warning(f"⚠️ trafilatura nur {result['word_count']} Wörter → Retry mit Playwright (JavaScript)")

            playwright_result = self._scrape_with_playwright(url)
            if playwright_result['success']:
                logger.info(f"✅ Playwright: {playwright_result['word_count']} Wörter (trafilatura: {result.get('word_count', 0)})")
                return playwright_result

        return result

    def _scrape_with_trafilatura(self, url: str) -> Dict:
        """
        Scraped mit trafilatura (sauberster Content)

        trafilatura ist spezialisiert auf Content-Extraktion und filtert automatisch:
        - Werbung und Tracking-Code
        - Navigation und Menüs
        - Cookie-Banner
        - Footer/Header Content
        - Social Media Widgets

        Perfekt für News-Artikel, Blog-Posts, Wetter-Seiten!
        """
        try:
            logger.info(f"🌐 Web Scraping: {url}")
            logger.debug(f"   Methode: trafilatura (Content-Extraktion)")

            # Download HTML mit 15s Timeout (via config)
            downloaded = trafilatura.fetch_url(url, config=self.trafilatura_config)

            if not downloaded:
                logger.error(f"❌ trafilatura: Download fehlgeschlagen")
                return {
                    'success': False,
                    'method': 'trafilatura',
                    'source': url,
                    'error': 'Download failed'
                }

            # Extract sauberen Content
            text = trafilatura.extract(
                downloaded,
                include_comments=False,  # Keine Kommentare
                include_tables=True,     # Tabellen behalten (wichtig für Wetter!)
                no_fallback=False,       # Fallback auf basic extraction wenn nötig
                favor_precision=True,    # Weniger Content, aber präziser (filtert mehr Werbung)
                output_format='txt'      # Plain text (nicht JSON/XML)
            )

            if not text:
                logger.warning(f"⚠️ trafilatura: Kein Content extrahiert")
                return {
                    'success': False,
                    'method': 'trafilatura',
                    'source': url,
                    'error': 'No content extracted'
                }

            # Titel extrahieren (optional, trafilatura kann das auch)
            metadata = trafilatura.extract_metadata(downloaded)
            title = metadata.title if metadata and metadata.title else ''

            # Text bereinigen
            text = self._clean_text(text)
            word_count = len(text.split())

            logger.info(f"  ✅ {word_count} Wörter extrahiert")

            return {
                'success': True,
                'source': url,
                'title': title,
                'content': text,
                'url': url,
                'word_count': word_count,
                'truncated': False,
                'method': 'trafilatura'
            }

        except Exception as e:
            logger.error(f"❌ trafilatura Fehler bei {url}: {e}")
            return {
                'success': False,
                'method': 'trafilatura',
                'source': url,
                'error': str(e)
            }

    def _scrape_with_playwright(self, url: str) -> Dict:
        """Scraped mit Playwright (langsamer, aber JavaScript-fähig)"""
        try:
            from playwright.sync_api import sync_playwright

            logger.info(f"🌐 Web Scraping: {url}")
            logger.debug(f"   Methode: Playwright (JavaScript-Rendering)")

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()

                # Navigiere zur Seite und warte auf Netzwerk-Idle
                page.goto(url, wait_until='networkidle', timeout=15000)

                # Warte noch 2s für lazy-loaded Content
                page.wait_for_timeout(2000)

                # Titel
                title = page.title()

                # Extrahiere Text (nur sichtbarer Content)
                text = page.inner_text('body')
                text = self._clean_text(text)

                word_count = len(text.split())

                browser.close()

                return {
                    'success': True,
                    'source': url,
                    'title': title,
                    'content': text,
                    'url': url,
                    'word_count': word_count,
                    'truncated': False,
                    'method': 'playwright'
                }

        except Exception as e:
            logger.error(f"❌ Playwright Fehler bei {url}: {e}")
            return {
                'success': False,
                'method': 'playwright',
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

def build_context(user_text: str, tool_results: List[Dict]) -> str:
    """
    Baut strukturierten Kontext für AI aus Tool-Ergebnissen

    Keine Längenbeschränkung - dynamisches num_ctx übernimmt die Größenkontrolle!
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
                # KEINE Kürzung mehr - verwende kompletten gescrapten Content!
                context += f"{content}\n\n"

            context += "---\n\n"

    context += "# Aufgabe\n"
    context += "Beantworte die User-Frage basierend auf den Recherche-Ergebnissen oben. "
    context += "WICHTIG: Zitiere JEDE Quelle MIT ihrer URL! "
    context += "Format: 'Quelle 1 (https://...) schreibt...'\n\n"

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


def scrape_webpage(url: str) -> Dict:
    """
    Convenience-Funktion für Web-Scraping

    Scraped komplette Artikel ohne Längenlimit.
    Ollama's dynamisches num_ctx übernimmt die Context-Größen-Kontrolle!
    """
    registry = get_tool_registry()
    scraper_tool = registry.get("Web Scraper")
    return scraper_tool.execute(url)


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
