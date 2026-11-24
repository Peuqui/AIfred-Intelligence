"""normalize_url: the identity of an address for deduplication and for comparing read pages."""

import pytest

from aifred.lib.tools.url_utils import deduplicate_urls, deduplicate_urls_with_metadata, normalize_url

SAME = "shop.test/produkt/a?id=1"


@pytest.mark.parametrize("url", [
    "https://shop.test/produkt/a?id=1",
    "http://shop.test/produkt/a?id=1",
    "https://www.shop.test/produkt/a?id=1",
    "https://SHOP.test/produkt/a?id=1",
    "https://WWW.Shop.Test/produkt/a?id=1",
    "https://shop.test/produkt/a/?id=1",
    "https://shop.test/produkt/a?id=1#preise",
    "  https://shop.test/produkt/a?id=1  ",
    "https://r.jina.ai/https://shop.test/produkt/a?id=1",
    "http://r.jina.ai/https://www.shop.test/produkt/a/?id=1#x",
    "HTTPS://R.JINA.AI/http://shop.test/produkt/a?id=1",
])
def test_these_spellings_are_one_address(url: str) -> None:
    assert normalize_url(url) == SAME


@pytest.mark.parametrize("a, b", [
    ("https://shop.test/a?id=1", "https://shop.test/a?id=2"),
    ("https://shop.test/a", "https://shop.test/b"),
    ("https://shop.test/a", "https://other.test/a"),
    ("https://shop.test/A", "https://shop.test/a"),
    ("https://shop.test/a", "https://shop.test/a?x=1"),
    ("https://shop.test/a", "https://sub.shop.test/a"),
])
def test_these_are_different_addresses(a: str, b: str) -> None:
    assert normalize_url(a) != normalize_url(b)


def test_only_a_leading_www_goes() -> None:
    assert normalize_url("https://awww.shop.test/") == "awww.shop.test"
    assert normalize_url("https://shop.www.test/") == "shop.www.test"


def test_root_and_bare_host() -> None:
    assert normalize_url("https://shop.test/") == normalize_url("shop.test") == "shop.test"


def test_deduplicate_uses_it() -> None:
    assert deduplicate_urls(["https://www.shop.test/a/", "http://shop.test/a#x", "https://shop.test/b"]) == [
        "https://www.shop.test/a/", "https://shop.test/b",
    ]


def test_an_address_that_cannot_be_split_is_an_error_not_passed_through() -> None:
    with pytest.raises(ValueError):
        normalize_url("http://[not-an-ip/a")


def test_a_search_result_whose_address_cannot_be_split_is_skipped_and_the_others_stay() -> None:
    urls, titles, snippets = deduplicate_urls_with_metadata(
        ["https://a.test/x", "http://[abc", "https://b.test/y"], ["A", "kaputt", "B"], ["sa", "sk", "sb"])
    assert (urls, titles, snippets) == (["https://a.test/x", "https://b.test/y"], ["A", "B"], ["sa", "sb"])
