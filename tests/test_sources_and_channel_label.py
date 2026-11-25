"""The sources block of a bubble and the name a channel without a plugin shows (25.11.2026).

The sources block listed every fetched page with "(0 Wörter)" (the count was never known) under "Sortiert nach
Relevanz" (they are in the order of the calls); a scheduled job's bubbles were headed "Scheduler", the code name,
though everything else calls it "Horarium".
"""

from __future__ import annotations

from aifred.lib import message_processor
from aifred.lib.bubble import KIND_SOURCES, BubbleArtifact, render_artifact
from aifred.lib.formatting import build_sources_collapsible

FETCHES = [
    {"url": "https://b.example/zweite", "success": True},
    {"url": "https://a.example/erste", "success": False},
    {"url": "https://c.example/dritte", "success": True},
]


class TestSourcesBlock:
    def test_lists_pages_in_call_order_without_word_count(self):
        html = build_sources_collapsible(FETCHES, lang="de")
        positions = [html.index(fetch["url"]) for fetch in FETCHES]
        assert positions == sorted(positions)
        assert "Wörter" not in html
        assert "Relevanz" not in html
        assert "In der Reihenfolge der Abrufe" in html

    def test_failed_fetch_is_marked_in_the_ui_language(self):
        html = build_sources_collapsible(FETCHES, lang="de")
        assert "(Abruf fehlgeschlagen)" in html
        assert "fetch failed" not in html
        assert "🔗 3 Web-Quellen (1 fehlgeschlagen)" in html
        assert "(fetch failed)" in build_sources_collapsible(FETCHES, lang="en")

    def test_no_fetches_no_block(self):
        assert build_sources_collapsible([], lang="de") == ""

    def test_bubble_artifact_renders_the_block(self):
        html = render_artifact(BubbleArtifact(KIND_SOURCES, {"urls": FETCHES}), None)
        assert "https://c.example/dritte" in html
        assert "0 Wörter" not in html and "0 words" not in html


class TestChannelLabel:
    def test_scheduler_channel_is_named_horarium(self, monkeypatch):
        monkeypatch.setattr("aifred.lib.plugin_registry.get_channel", lambda channel: None)
        monkeypatch.setattr("aifred.lib.settings.load_settings", lambda: {"ui_language": "de"})
        assert message_processor.channel_display_label("scheduler") == "Horarium"

    def test_other_channels_without_plugin_keep_their_names(self, monkeypatch):
        monkeypatch.setattr("aifred.lib.plugin_registry.get_channel", lambda channel: None)
        monkeypatch.setattr("aifred.lib.settings.load_settings", lambda: {"ui_language": "en"})
        labels = [message_processor.channel_display_label(channel) for channel in ("webhook", "vision", "inject")]
        assert labels == ["Webhook", "Vision", "Inject"]
