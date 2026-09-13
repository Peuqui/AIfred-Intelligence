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
