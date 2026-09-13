"""Sandbox output files are named after their content: the same page written
twice is one file and one URL; the bubble shows its source below it."""
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
        (work / "output.html").write_text(PAGE, encoding="utf-8")
        (work / "fibonacci.html").write_text(PAGE, encoding="utf-8")
        urls.extend(sandbox._collect_html(work, SESSION))
    assert len(set(urls)) == 1
    assert len(list((output_root / SESSION).iterdir())) == 1


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
    assert sandbox.sandbox_output_path(url).read_text(encoding="utf-8") == PAGE
    assert sandbox.sandbox_output_path(f"/_upload/sandbox_output/{SESSION}/../x.html") is None
    assert sandbox.sandbox_output_path("/_upload/sandbox_output/not-a-session/abcdef12.html") is None
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
