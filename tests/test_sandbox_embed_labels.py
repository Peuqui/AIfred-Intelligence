"""The labels of a sandbox output in the bubble (formatting.build_sandbox_iframe / build_sandbox_image) follow the UI
language; until 08.11.2026 they stood in German in the code."""

from collections.abc import Iterator

import pytest

from aifred.lib import formatting


@pytest.fixture
def language() -> Iterator[None]:
    before = formatting.get_ui_locale()
    yield
    formatting.set_ui_locale(before)


@pytest.mark.parametrize("lang, iframe_labels, image_label", [
    ("de", ("📊 Interaktive Visualisierung", "Im Browser öffnen"), "Vollbild"),
    ("en", ("📊 Interactive visualization", "Open in browser"), "Full screen"),
])
def test_the_labels_follow_the_ui_language(
        language: None, lang: str, iframe_labels: tuple[str, str], image_label: str) -> None:
    formatting.set_ui_locale(lang)
    iframe = formatting.build_sandbox_iframe("/_upload/sandbox_output/a/b.html")
    image = formatting.build_sandbox_image("/_upload/sandbox_output/a/b.png")
    assert all(label in iframe for label in iframe_labels)
    assert f">{image_label}</a>" in image
