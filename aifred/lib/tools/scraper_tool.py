"""
Web Scraper Tool - Content Extraction

Extracted from agent_tools.py for better modularity.
"""

import logging
import re
import trafilatura
from trafilatura.settings import DEFAULT_CONFIG
from copy import deepcopy
from typing import Dict

from .base import BaseTool
from ..logging_utils import log_message

# Logging Setup
logger = logging.getLogger(__name__)

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

        # trafilatura Config mit 10s Timeout (statt default 30s)
        self.trafilatura_config = deepcopy(DEFAULT_CONFIG)
        self.trafilatura_config.set('DEFAULT', 'DOWNLOAD_TIMEOUT', '10')
        self.trafilatura_config.set('DEFAULT', 'MAX_REDIRECTS', '2')  # Max 2 Redirects (default ist mehr)
    def execute(self, query: str, **kwargs) -> Dict:
        """
        Scraped eine Webseite komplett ohne Längenlimit

        Args:
            query: URL der Webseite (umbenennung von 'url' zu 'query' für BaseTool-Kompatibilität)

        Strategie (2-Stufen Fallback):
        1. trafilatura (sauberster Content, filtert Werbung/Navigation/Cookies automatisch)
        2. Falls < threshold ODER fehlgeschlagen → Playwright (JavaScript-Rendering)

        trafilatura funktioniert für 95% aller Websites (News, Blogs, Wetter).
        Playwright nur für JavaScript-heavy Single-Page-Apps (React, Vue, etc.).

        Ollama's dynamisches num_ctx übernimmt die Context-Größen-Kontrolle!
        """
        self._rate_limit_check()

        # Intern verwenden wir 'url' für Klarheit
        url = query

        # Versuch 1: trafilatura (schnell + sauber)
        result = self._scrape_with_trafilatura(url)

        # Intelligente Playwright-Fallback-Strategie:
        # 1. Download failed (404, timeout, bot-protection) → KEIN Playwright (sinnlos!)
        # 2. Zu wenig Content (< threshold) → Playwright (JS-heavy Site!)

        if not result['success']:
            # Download failed → Site blockiert/down → Playwright bringt nichts!
            log_message("⚠️ trafilatura Download failed → SKIP Playwright (Site blockiert/down)")
            return result

        # Trafilatura erfolgreich, aber zu wenig Content? → JS-heavy Site!
        if result.get('word_count', 0) < self.PLAYWRIGHT_FALLBACK_THRESHOLD:
            log_message(f"⚠️ trafilatura nur {result['word_count']} Wörter → Retry mit Playwright (JavaScript)")
            playwright_result = self._scrape_with_playwright(url)
            if playwright_result['success']:
                log_message(f"✅ Playwright: {playwright_result['word_count']} Wörter (trafilatura: {result.get('word_count', 0)})")
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
            logger.debug("   Methode: trafilatura (Content-Extraktion)")

            # Download HTML mit 15s Timeout (via config)
            downloaded = trafilatura.fetch_url(url, config=self.trafilatura_config)

            if not downloaded:
                logger.error("❌ trafilatura: Download fehlgeschlagen")
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
                logger.warning("⚠️ trafilatura: Kein Content extrahiert")
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
            logger.debug("   Methode: Playwright (JavaScript-Rendering)")

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()

                # Navigiere zur Seite und warte auf Netzwerk-Idle
                page.goto(url, wait_until='networkidle', timeout=10000)

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

