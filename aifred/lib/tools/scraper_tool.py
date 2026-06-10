"""
Web Scraper Tool - Content Extraction

Extracted from agent_tools.py for better modularity.
Supports HTML (trafilatura/Playwright) and PDF (PyMuPDF) content extraction.
"""

import io
import logging
import re
import time
import trafilatura
from trafilatura.settings import DEFAULT_CONFIG
from copy import deepcopy
from typing import Dict
import requests

from .base import BaseTool
from ..logging_utils import log_message
from ..config import PLAYWRIGHT_FALLBACK_THRESHOLD
from ..security import UnsafeURLError, validate_external_url

# Optional: PyMuPDF for PDF extraction (best quality)
try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
    fitz = None

# Logging Setup
logger = logging.getLogger(__name__)

# ============================================================
# WEB SCRAPER TOOL
# ============================================================

class WebScraperTool(BaseTool):
    """
    Web Scraper with trafilatura + Playwright Fallback

    Extracts clean text content from web pages.
    trafilatura automatically filters ads, navigation and cookie banners.
    """

    # Constants
    # PLAYWRIGHT_FALLBACK_THRESHOLD imported from config.py (module level)
    MAX_RETRY_ATTEMPTS = 2  # Maximum retry attempts for Cloudflare/rate-limit blocks
    RETRY_DELAY = 3.0  # Seconds to wait before retry
    SAFE_MAX_REDIRECTS = 3  # Max redirect hops, each re-validated against SSRF rules

    def __init__(self):
        super().__init__()
        self.name = "Web Scraper"
        self.description = "Extracts text content from web pages"
        self.min_call_interval = 1.0

        # trafilatura config with 10s timeout (instead of default 30s)
        self.trafilatura_config = deepcopy(DEFAULT_CONFIG)
        self.trafilatura_config.set('DEFAULT', 'DOWNLOAD_TIMEOUT', '10')
        self.trafilatura_config.set('DEFAULT', 'MAX_REDIRECTS', '2')  # Max 2 redirects (default is more)

    def _safe_request(self, method: str, url: str, **kwargs) -> requests.Response:
        """HTTP request that re-validates every redirect hop against SSRF rules.

        ``validate_external_url`` only covers the initial URL; a public host can
        302-redirect to an internal address (cloud metadata, llama-swap,
        ChromaDB). We disable automatic redirects and follow them manually,
        re-validating each ``Location`` before connecting. Raises
        :class:`UnsafeURLError` if any hop resolves to a private/reserved
        address — the caller decides how to surface it (no silent pass-through).
        """
        kwargs.setdefault("timeout", 15)
        kwargs["allow_redirects"] = False
        current = url
        for _ in range(self.SAFE_MAX_REDIRECTS + 1):
            validate_external_url(current)
            resp = requests.request(method, current, **kwargs)
            if resp.is_redirect or resp.is_permanent_redirect:
                location = resp.headers.get("Location")
                if not location:
                    return resp
                current = requests.compat.urljoin(current, location)  # type: ignore[attr-defined]
                continue
            return resp
        raise UnsafeURLError(f"Too many redirects for {url!r}")

    def execute(self, query: str, **kwargs) -> Dict:
        """
        Scrape a web page completely without length limit

        Args:
            query: URL of the web page (renamed from 'url' to 'query' for BaseTool compatibility)

        Strategy (3-tier):
        0. PDF detection: Check Content-Type → PyMuPDF
        1. trafilatura (cleanest content, automatically filters ads/navigation/cookies)
        2. If < threshold OR failed → Playwright (JavaScript rendering)

        trafilatura works for 95% of all websites (news, blogs, weather).
        Playwright only for JavaScript-heavy single-page apps (React, Vue, etc.).
        PyMuPDF for PDFs (AWMF guidelines, Orphananesthesia, etc.)

        Ollama's dynamic num_ctx handles context size control!
        """
        self._rate_limit_check()

        # Internally we use 'url' for clarity
        url = query

        # SSRF protection: reject URLs that resolve to private/loopback/reserved
        # addresses. The initial URL is validated here; redirect targets are
        # re-validated per hop in _safe_request (requests paths) and via the
        # route guard in _playwright_sync.
        try:
            validate_external_url(url)
        except UnsafeURLError as e:
            log_message(f"🛑 Scraper blocked unsafe URL: {e}", "warning")
            return {
                'success': False,
                'method': 'blocked',
                'source': url,
                'error': f"URL rejected: {e}",
            }

        # ============================================================
        # STEP 0: PDF detection (Content-Type header check)
        # ============================================================
        is_pdf = self._is_pdf_url(url)
        if is_pdf:
            logger.info(f"📄 PDF detected: {url}")
            log_message(f"📄 PDF detected: {url}")
            return self._scrape_pdf(url)

        # ============================================================
        # STEP 1: trafilatura (fast + clean for HTML)
        # ============================================================
        result = self._scrape_with_trafilatura(url)

        # Intelligent Playwright fallback strategy:
        # 1. Download failed (404, timeout, bot-protection) → NO Playwright (pointless!)
        # 2. Too little content (< threshold) → Playwright (JS-heavy site!)

        if not result['success'] or result.get('word_count', 0) < PLAYWRIGHT_FALLBACK_THRESHOLD:
            # trafilatura failed or too little content → try Playwright (JS-heavy site)
            reason = "Download failed" if not result['success'] else f"only {result.get('word_count', 0)} words"
            log_message(f"⚠️ trafilatura: {reason} → Retry with Playwright (JavaScript)")
            playwright_result = self._scrape_with_playwright(url)
            if playwright_result['success'] and playwright_result.get('word_count', 0) > result.get('word_count', 0):
                log_message(f"✅ Playwright: {playwright_result['word_count']} words")
                return playwright_result
            # Playwright also failed — return original result
            if not result['success']:
                return result

        # Both cases (failed + low content) handled above

        return result

    def _scrape_with_trafilatura(self, url: str, retry_attempt: int = 1) -> Dict:
        """
        Scrape with trafilatura (cleanest content)

        trafilatura specializes in content extraction and automatically filters:
        - Ads and tracking code
        - Navigation and menus
        - Cookie banners
        - Footer/Header content
        - Social media widgets

        Perfect for news articles, blog posts, weather pages!

        Args:
            url: URL to scrape
            retry_attempt: Current retry attempt (1 = first try, 2 = retry)
        """
        try:
            if retry_attempt == 1:
                logger.info(f"🌐 Web Scraping: {url}")
                logger.debug("   Method: trafilatura (content extraction)")
            else:
                logger.info(f"🔄 Retry {retry_attempt}/{self.MAX_RETRY_ATTEMPTS}: {url}")

            # Download HTML via the SSRF-safe fetcher (re-validates redirects),
            # then hand the raw HTML to trafilatura for extraction. We do NOT use
            # trafilatura.fetch_url because it follows redirects without
            # re-validation.
            resp = self._safe_request("GET", url, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; AIfred/1.0)'
            }, timeout=10)
            downloaded = resp.text if resp.ok else None

            if not downloaded:
                error_msg = "Download failed (no response)"
                logger.error(f"❌ trafilatura: {error_msg}")

                # Retry Logic
                if retry_attempt < self.MAX_RETRY_ATTEMPTS:
                    logger.info(f"⏳ Waiting {self.RETRY_DELAY}s before retry...")
                    time.sleep(self.RETRY_DELAY)
                    return self._scrape_with_trafilatura(url, retry_attempt=retry_attempt + 1)

                return {
                    'success': False,
                    'method': 'trafilatura',
                    'source': url,
                    'error': error_msg,
                    'retry_attempts': retry_attempt
                }

            # Extract clean content
            text = trafilatura.extract(
                downloaded,
                include_comments=False,  # No comments
                include_tables=True,     # Keep tables (important for weather!)
                no_fallback=False,       # Fallback to basic extraction if needed
                favor_precision=True,    # Less content, but more precise (filters more ads)
                output_format='txt'      # Plain text (not JSON/XML)
            )

            if not text:
                logger.warning("⚠️ trafilatura: No content extracted")
                return {
                    'success': False,
                    'method': 'trafilatura',
                    'source': url,
                    'error': 'No content extracted'
                }

            # Extract title (optional, trafilatura can do this too)
            metadata = trafilatura.extract_metadata(downloaded)
            title = metadata.title if metadata and metadata.title else ''

            # Clean text
            text = self._clean_text(text)
            word_count = len(text.split())

            logger.info(f"  ✅ {word_count} words extracted")

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

        except (OSError, ValueError) as e:
            error_msg = str(e)
            logger.error(f"❌ trafilatura error at {url}: {error_msg}")

            # Retry Logic for transient errors (timeout, connection)
            if retry_attempt < self.MAX_RETRY_ATTEMPTS and any(keyword in error_msg.lower() for keyword in ['timeout', 'connection', 'refused']):
                logger.info(f"⏳ Waiting {self.RETRY_DELAY}s before retry...")
                time.sleep(self.RETRY_DELAY)
                return self._scrape_with_trafilatura(url, retry_attempt=retry_attempt + 1)

            return {
                'success': False,
                'method': 'trafilatura',
                'source': url,
                'error': error_msg,
                'retry_attempts': retry_attempt
            }

    def _scrape_with_playwright(self, url: str) -> Dict:
        """Scrape with Playwright (slower, but JavaScript-capable)"""
        # Playwright sync API cannot run inside asyncio event loop.
        # When called from LLM tool execution (async), run in a thread.
        try:
            import asyncio
            asyncio.get_running_loop()
            # We're inside asyncio — offload to thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(self._playwright_sync, url).result(timeout=30)
        except RuntimeError:
            # No running loop — safe to call sync directly
            return self._playwright_sync(url)

    def _playwright_sync(self, url: str) -> Dict:
        """Actual Playwright scraping (must run outside asyncio loop)."""
        try:
            from playwright.sync_api import sync_playwright

            logger.info(f"🌐 Web Scraping: {url}")
            logger.debug("   Method: Playwright (JavaScript rendering)")

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    page = browser.new_page()

                    # SSRF guard: re-validate every request the page issues
                    # (initial navigation, redirects, subresources). Abort any
                    # hop that resolves to a private/reserved address.
                    def _ssrf_guard(route, request):
                        try:
                            validate_external_url(request.url)
                        except UnsafeURLError as exc:
                            log_message(f"🛑 Playwright blocked unsafe request: {exc}", "warning")
                            route.abort()
                            return
                        route.continue_()

                    page.route("**/*", _ssrf_guard)

                    # Navigate to page and wait for DOM content
                    page.goto(url, wait_until='domcontentloaded', timeout=15000)

                    # Wait 2s more for lazy-loaded content
                    page.wait_for_timeout(2000)

                    # Title
                    title = page.title()

                    # Extract text: prefer main content area over full body
                    text = ""
                    for selector in ['article', 'main', '[role="main"]', '.chapter', '.content', '#content']:
                        try:
                            el = page.locator(selector).first
                            if el.is_visible(timeout=500):
                                text = el.inner_text(timeout=3000)
                                if len(text.split()) > 50:
                                    break
                        except Exception:
                            continue
                    if not text or len(text.split()) < 50:
                        text = page.inner_text('body')
                    text = self._clean_text(text)

                    word_count = len(text.split())

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
                finally:
                    browser.close()  # ALWAYS executed, even on exception

        except Exception as e:
            error_msg = str(e)

            # The Chromium browser is installed as a deliberate deploy step
            # (scripts/setup-python.sh + install-all.sh). We do NOT auto-install
            # at runtime — that would run an unattended network/disk action
            # triggered by an LLM scrape request. Surface a clear, actionable
            # error instead.
            if "Executable doesn't exist" in error_msg:
                hint = ("Playwright Chromium not installed — run "
                        "'source venv/bin/activate && playwright install --with-deps chromium'")
                logger.error(f"❌ {hint}")
                log_message(f"❌ {hint}", "error")
                return {
                    'success': False,
                    'method': 'playwright',
                    'source': url,
                    'url': url,
                    'error': hint,
                }

            logger.error(f"❌ Playwright error at {url}: {error_msg}")
            log_message(f"❌ Playwright error: {error_msg}")
            return {
                'success': False,
                'method': 'playwright',
                'source': url,
                'url': url,
                'error': error_msg
            }

    def _clean_text(self, text: str) -> str:
        """Clean text"""
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'\n+', '\n', text)
        return text.strip()

    # ============================================================
    # PDF SUPPORT (PyMuPDF)
    # ============================================================

    def _is_pdf_url(self, url: str) -> bool:
        """
        Detect if a URL points to a PDF.

        Checks:
        1. URL ending (.pdf)
        2. HEAD request Content-Type header

        Args:
            url: URL to check

        Returns:
            True if PDF, False otherwise
        """
        # Fast check: URL ends with .pdf
        if url.lower().endswith('.pdf'):
            return True

        # Slow check: HEAD request for Content-Type (redirects re-validated)
        try:
            response = self._safe_request("HEAD", url, timeout=5, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; AIfred/1.0)'
            })
            content_type = response.headers.get('Content-Type', '').lower()
            return 'application/pdf' in content_type
        except UnsafeURLError as e:
            log_message(f"🛑 PDF detection blocked unsafe redirect: {e}", "warning")
            return False
        except OSError:
            # On error: Assume not PDF (trafilatura will try)
            return False

    def _scrape_pdf(self, url: str) -> Dict:
        """
        Extract text from PDF documents with PyMuPDF.

        PyMuPDF (fitz) offers:
        - Fastest text extraction
        - Best quality
        - Good table recognition
        - Metadata extraction (title, author)

        Args:
            url: URL of the PDF document

        Returns:
            Dict with extracted content or error
        """
        if not PYMUPDF_AVAILABLE:
            logger.warning("⚠️ PyMuPDF not installed → PDF support disabled")
            return {
                'success': False,
                'method': 'pdf',
                'source': url,
                'error': 'PyMuPDF not installed (pip install pymupdf)'
            }

        try:
            logger.info(f"📄 PDF-Download: {url}")

            # Download PDF with timeout (use real browser User-Agent to avoid hotlink protection)
            # Extract domain for Referer header (required by some servers)
            from urllib.parse import urlparse
            parsed = urlparse(url)
            referer = f"{parsed.scheme}://{parsed.netloc}/"

            response = self._safe_request("GET", url, timeout=15, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': referer,
                'Accept': 'application/pdf,*/*'
            })
            response.raise_for_status()

            # Verify it's actually a PDF
            content_type = response.headers.get('Content-Type', '').lower()
            if 'application/pdf' not in content_type and not url.lower().endswith('.pdf'):
                logger.warning(f"⚠️ Not a PDF: Content-Type={content_type}")
                # Fallback to trafilatura
                return self._scrape_with_trafilatura(url)

            # Open PDF from memory
            pdf_data = io.BytesIO(response.content)
            doc = fitz.open(stream=pdf_data, filetype="pdf")

            # Extract metadata
            metadata = doc.metadata
            title = metadata.get('title', '') if metadata else ''
            if not title:
                # Fallback: Use filename from URL
                title = url.split('/')[-1].replace('.pdf', '')

            # Extract text from all pages
            text_parts = []
            page_count = len(doc)  # Save before closing!
            for page_num, page in enumerate(doc):
                page_text = page.get_text("text")
                if page_text.strip():
                    text_parts.append(page_text)

            doc.close()

            # Combine and clean text
            full_text = '\n\n'.join(text_parts)
            full_text = self._clean_text(full_text)
            word_count = len(full_text.split())

            if not full_text:
                logger.warning("⚠️ PDF: No text extracted (possibly scanned/image PDF)")
                return {
                    'success': False,
                    'method': 'pdf',
                    'source': url,
                    'error': 'No text extracted (possibly scanned PDF)'
                }

            logger.info(f"  ✅ PDF: {word_count} words, {page_count} pages")
            log_message(f"  ✅ PDF: {word_count} words extracted")

            return {
                'success': True,
                'source': url,
                'title': title,
                'content': full_text,
                'url': url,
                'word_count': word_count,
                'truncated': False,
                'method': 'pdf',
                'pages': page_count
            }

        except requests.exceptions.Timeout:
            error_msg = "PDF Download Timeout"
            logger.error(f"❌ {error_msg}: {url}")
            return {
                'success': False,
                'method': 'pdf',
                'source': url,
                'error': error_msg
            }

        except requests.exceptions.HTTPError as e:
            error_msg = f"HTTP Error {e.response.status_code}"
            logger.error(f"❌ {error_msg}: {url}")
            return {
                'success': False,
                'method': 'pdf',
                'source': url,
                'error': error_msg
            }

        except (OSError, ValueError) as e:
            error_msg = str(e)[:100]
            logger.error(f"❌ PDF error at {url}: {error_msg}")
            return {
                'success': False,
                'method': 'pdf',
                'source': url,
                'error': f"PDF extraction failed: {error_msg}"
            }

