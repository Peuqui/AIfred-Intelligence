"""extract_xml_tags: code blocks are no tags, but they stay part of a tag's content."""

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
    from aifred.lib.message_builder import with_html_preview_note

    monkeypatch.setattr(fmt, "_HTML_PREVIEW_DIR", tmp_path)
    monkeypatch.setattr(fmt, "BACKEND_URL", "")
    text = "Hier ist es:\n```html\n<p>Fibonacci</p>\n```\nFertig."
    (url,) = fmt.html_preview_urls(text)
    assert fmt._save_html_to_assets("<p>Fibonacci</p>") == url
    note = with_html_preview_note(text)
    assert note.startswith(text) and url in note
    assert with_html_preview_note("Ohne Code.") == "Ohne Code."
