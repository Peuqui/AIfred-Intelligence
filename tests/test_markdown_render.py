"""Markdown rendering for outbound channels."""

from aifred.lib.markdown_render import md_to_html


def test_single_newline_stays_a_line_break():
    html = md_to_html("**Lobpreis**\nHerr, wir danken dir.")
    assert html == "<p><strong>Lobpreis</strong><br />\nHerr, wir danken dir.</p>\n"


def test_paragraphs_and_lists_unchanged():
    html = md_to_html("Absatz eins.\n\n- a\n- b")
    assert "<p>Absatz eins.</p>" in html
    assert "<li>a</li>" in html and "<br" not in html


def test_inline_html_is_escaped():
    assert "<script>" not in md_to_html("<script>alert(1)</script>")
