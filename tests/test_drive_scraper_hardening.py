"""google_drive-Härtung (Query-Escape) und Scraper-DNS-Rebind-Pin."""

from types import SimpleNamespace

from aifred.lib.tools.scraper_tool import WebScraperTool
from aifred.plugins.tools.google_suite.drive._api import escape_drive_term
from aifred.plugins.tools.google_suite.drive.tools import _DRIVE_QUERY_OPERATOR

# ── Drive: Query-Escape ───────────────────────────────────────

class TestDriveQueryEscape:
    def test_quote_and_backslash_escaped(self):
        assert escape_drive_term("L'atelier") == "L\\'atelier"
        assert escape_drive_term("a\\b") == "a\\\\b"
        # Injection: Term kann das '...'-Literal nicht mehr verlassen
        evil = "x' or name contains 'geheim"
        assert "' " not in escape_drive_term(evil).replace("\\'", "")

    def test_operator_detection_word_boundaries(self):
        # Frueher: `"in" in query` — jede Suche mit "in" im Wort wurde als
        # rohe Drive-Query durchgereicht ("Einladung", "Termin", ...)
        for plain in ("Einladung", "Termine 2026", "Wein kaufen", "involviert"):
            assert not _DRIVE_QUERY_OPERATOR.search(plain), plain
        for raw in ("name = 'x'", "fullText contains 'x'", "'abc' in parents"):
            assert _DRIVE_QUERY_OPERATOR.search(raw), raw


# ── Scraper: DNS-Rebind-Pin ───────────────────────────────────

class TestScraperPin:
    def test_pin_url_swaps_host_keeps_port_and_host_header(self):
        pinned, host = WebScraperTool._pin_url("https://example.com:8443/a?b=1", "93.184.216.34")
        assert pinned == "https://93.184.216.34:8443/a?b=1"
        assert host == "example.com:8443"

    def test_pin_url_default_port(self):
        pinned, host = WebScraperTool._pin_url("http://example.com/x", "1.2.3.4")
        assert pinned == "http://1.2.3.4/x"
        assert host == "example.com"

    def test_pin_url_ipv6_brackets(self):
        pinned, _ = WebScraperTool._pin_url("https://example.com/", "2606:2800:220:1::1")
        assert pinned == "https://[2606:2800:220:1::1]/"

    def test_safe_request_connects_to_validated_ip(self, monkeypatch):
        """Der Request MUSS an die IP gehen, die die Validierung geliefert
        hat — eine zweite DNS-Auflösung wäre das Rebind-Loch."""
        import aifred.lib.tools.scraper_tool as st

        seen = {}
        monkeypatch.setattr(st, "validate_external_url", lambda url: "9.9.9.9")

        def fake_pinned(self, method, url, ip, **kwargs):
            seen["url"], seen["ip"] = url, ip
            return SimpleNamespace(
                is_redirect=False, is_permanent_redirect=False,
                headers={}, close=lambda: None,
            )

        monkeypatch.setattr(WebScraperTool, "_pinned_request", fake_pinned)
        monkeypatch.setattr(WebScraperTool, "_read_capped", staticmethod(lambda resp: None))

        tool = WebScraperTool()
        tool._safe_request("GET", "https://example.com/")
        assert seen == {"url": "https://example.com/", "ip": "9.9.9.9"}
