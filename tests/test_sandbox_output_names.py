"""Sandbox output files are named "<name>-<content hash>": readable, a common
name does not overwrite a different page, the same page under two names is
kept once; the bubble shows the page collapsed with its source below it."""
from __future__ import annotations

import uuid

import pytest

import aifred.lib.config as config
import aifred.lib.sandbox as sandbox
from aifred.lib.bubble import KIND_SANDBOX_HTML, BubbleArtifact, render_bubble

SESSION = uuid.uuid4().hex
PAGE = "<!DOCTYPE html><html><body><button id='b'>Nächste</button></body></html>"


@pytest.fixture
def output_root(tmp_path, monkeypatch):
    root = tmp_path / "sandbox_output"
    root.mkdir()
    monkeypatch.setattr(config, "SANDBOX_OUTPUT_DIR", root)
    monkeypatch.setattr(config, "BACKEND_URL", "")
    return root


def test_same_page_under_two_names_and_two_runs_is_one_output(tmp_path, output_root):
    urls = []
    for run in range(2):
        work = tmp_path / f"run{run}"
        work.mkdir()
        (work / "fibonacci.html").write_text(PAGE, encoding="utf-8")
        (work / "zweitname.html").write_text(PAGE, encoding="utf-8")
        urls.extend(sandbox._collect_html(work, SESSION))
    assert len(set(urls)) == 1
    (name,) = [p.name for p in (output_root / SESSION).iterdir()]
    assert name == f"fibonacci-{sandbox.content_hash(PAGE.encode())}.html"
    assert len(sandbox.content_hash(PAGE.encode())) == 6


def test_common_name_with_different_content_keeps_both(tmp_path, output_root):
    urls = []
    for run, page in enumerate((PAGE, PAGE.replace("Nächste", "Weiter"))):
        work = tmp_path / f"run{run}"
        work.mkdir()
        (work / "index.html").write_text(page, encoding="utf-8")
        urls.extend(sandbox._collect_html(work, SESSION))
    assert len(set(urls)) == 2
    assert all(u.rsplit("/", 1)[-1].startswith("index-") for u in urls)


def test_bubble_key_treats_the_same_content_under_two_names_as_one(tmp_path, output_root):
    from aifred.lib.bubble import BubbleArtifact
    digest = sandbox.content_hash(PAGE.encode())
    a = BubbleArtifact(KIND_SANDBOX_HTML, {"url": f"/_upload/sandbox_output/{SESSION}/fibonacci-{digest}.html"})
    b = BubbleArtifact(KIND_SANDBOX_HTML, {"url": f"/_upload/sandbox_output/{SESSION}/output-{digest}.html"})
    assert a.key() == b.key()


def test_different_pages_get_different_names(tmp_path, output_root):
    work = tmp_path / "run"
    work.mkdir()
    (work / "a.html").write_text(PAGE, encoding="utf-8")
    (work / "b.html").write_text(PAGE.replace("Nächste", "Weiter"), encoding="utf-8")
    assert len(sandbox._collect_html(work, SESSION)) == 2


def test_output_path_resolves_only_real_sandbox_files(tmp_path, output_root):
    work = tmp_path / "run"
    work.mkdir()
    (work / "fibonacci.html").write_text(PAGE, encoding="utf-8")
    (url,) = sandbox._collect_html(work, SESSION)
    path = sandbox.sandbox_output_path(url)
    assert path is not None and path.read_text(encoding="utf-8") == PAGE
    assert sandbox.sandbox_output_path(f"/_upload/sandbox_output/{SESSION}/../x.html") is None
    assert sandbox.sandbox_output_path("/_upload/sandbox_output/not-a-session/seite-abcdef.html") is None
    assert sandbox.sandbox_output_path("/_upload/documents/seite.html") is None


def test_bubble_shows_page_collapsed_with_its_source(tmp_path, output_root):
    work = tmp_path / "run"
    work.mkdir()
    (work / "fibonacci.html").write_text(PAGE, encoding="utf-8")
    (url,) = sandbox._collect_html(work, SESSION)
    html = render_bubble("Fertig.", [BubbleArtifact(KIND_SANDBOX_HTML, {"url": url}, offset=0)])
    assert "<details data-sandbox" in html and "<details open" not in html
    assert f'src="{url}"' in html
    # Source below the page, escaped as text.
    assert html.index("<iframe") < html.index("&lt;button id=&#x27;b&#x27;&gt;")


def test_documents_page_shows_its_source_too(tmp_path, monkeypatch) -> None:
    """A page saved to documents/ got no source block under its embed."""
    import aifred.lib.config as config
    from aifred.lib.bubble import _source_block

    monkeypatch.setattr(config, "DOCUMENTS_DIR", tmp_path)
    (tmp_path / "fibonacci.html").write_text(PAGE, encoding="utf-8")
    path = sandbox.documents_html_path("/_upload/documents/fibonacci.html")
    assert path is not None and path.read_text(encoding="utf-8") == PAGE
    assert sandbox.documents_html_path("/_upload/documents/../escape.html") is None
    assert "fibonacci.html" in _source_block("/_upload/documents/fibonacci.html")
