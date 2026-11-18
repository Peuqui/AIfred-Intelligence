"""extract_xml_tags: code blocks are no tags, but they stay part of a tag's content."""

import re

from aifred.lib.formatting import extract_xml_tags


def test_code_draft_inside_think_is_kept():
    text = "<think>Plan:\n```python\nx = 1\n```\nfertig</think>Antwort"
    assert extract_xml_tags(text) == [("think", "Plan:\n```python\nx = 1\n```\nfertig")]


def test_tags_inside_code_blocks_are_ignored():
    text = "Beispiel:\n```html\n<think>kein Denken</think>\n```\nEnde"
    assert extract_xml_tags(text) == []


def test_two_think_blocks_each_with_code():
    text = "<think>a\n```\n1\n```</think>x<think>b\n```\n2\n```</think>y"
    assert extract_xml_tags(text) == [("think", "a\n```\n1\n```"), ("think", "b\n```\n2\n```")]


def test_html_preview_url_is_known_from_the_answer_text(tmp_path, monkeypatch) -> None:
    """The history note needs the preview URL before the bubble writes the
    file: both come from the code block's content hash."""
    import aifred.lib.formatting as fmt
    from aifred.lib.message_builder import with_history_notes

    monkeypatch.setattr(fmt, "_HTML_PREVIEW_DIR", tmp_path)
    text = "Hier ist es:\n```html\n<p>Fibonacci</p>\n```\nFertig."
    (url,) = fmt.html_preview_urls(text)
    assert fmt._save_html_to_assets("<p>Fibonacci</p>") == url
    note = with_history_notes(text)
    assert note.startswith(text) and url in note
    assert with_history_notes("Ohne Code.") == "Ohne Code."


def test_exports_with_the_same_title_do_not_overwrite_each_other(tmp_path, monkeypatch) -> None:
    import aifred.lib.formatting as fmt

    monkeypatch.setattr(fmt, "_HTML_PREVIEW_DIR", tmp_path)
    first = fmt._save_html_to_assets("<p>eins</p>", "Mein Chat")
    second = fmt._save_html_to_assets("<p>zwei</p>", "Mein Chat")
    assert first != second
    assert (tmp_path / first.rsplit("/", 1)[1]).read_text() == "<p>eins</p>"
    assert (tmp_path / second.rsplit("/", 1)[1]).read_text() == "<p>zwei</p>"
    assert all(re.fullmatch(r"/_upload/html_preview/[A-Za-z0-9_-]+\.html", url) for url in (first, second))


def test_html_preview_limit_holds_without_memory_state(tmp_path, monkeypatch) -> None:
    """The limit counts the files on disk by age: a fresh process knows nothing of earlier files."""
    import os

    import aifred.lib.formatting as fmt

    monkeypatch.setattr(fmt, "_HTML_PREVIEW_DIR", tmp_path)
    monkeypatch.setattr(fmt, "HTML_PREVIEW_MAX_FILES", 3)
    for age, name in enumerate(["old-a.html", "old-b.html", "old-c.html", "old-d.html"]):
        (tmp_path / name).write_text("x")
        os.utime(tmp_path / name, (1000 + age, 1000 + age))
    (tmp_path / "notes.txt").write_text("keep")
    url = fmt._save_html_to_assets("<p>neu</p>", "Neu")
    assert sorted(path.name for path in tmp_path.glob("*.html")) == sorted(
        ["old-d.html", "old-c.html", url.rsplit("/", 1)[1]]
    )
    assert (tmp_path / "notes.txt").read_text() == "keep"


def test_the_storage_tab_shows_an_export_by_its_title() -> None:
    from aifred.lib.formatting import _export_filename, export_display_name

    assert export_display_name(_export_filename("Mein Plot: Solar").removesuffix(".html")) == "Mein-Plot-Solar"
    assert export_display_name("preview-abc123") == "preview-abc123"
