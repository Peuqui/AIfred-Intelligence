"""All libxml2 work runs in the one lxml thread (crash of 26.11.2026: two threads in libxml2 at once).

The per-site tests wrap the library function that parses and record the thread it runs on;
they deliberately do not import lxml_thread, so they test behaviour, not wiring.
"""

import ast
import threading
from pathlib import Path
from collections.abc import Callable
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

AIFRED_DIR = Path(__file__).resolve().parent.parent / "aifred"
LXML_USERS = ("lxml", "trafilatura", "justext", "htmldate", "lxml_html_clean", "docx", "openpyxl", "pptx")
CALLER_COUNT = 3


def _record_threads(monkeypatch: pytest.MonkeyPatch, owner: Any, attribute: str) -> list[str]:
    """Wrap ``owner.attribute`` so that every call notes the name of its thread."""
    names: list[str] = []
    real = getattr(owner, attribute)

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        names.append(threading.current_thread().name)
        return real(*args, **kwargs)

    monkeypatch.setattr(owner, attribute, wrapper)
    return names


def _call_from_plain_threads(func: Any) -> list[Any]:
    results: list[Any] = [None] * CALLER_COUNT
    errors: list[BaseException] = []

    def work(index: int) -> None:
        try:
            results[index] = func()
        except BaseException as error:  # noqa: BLE001 — reported to the test thread
            errors.append(error)

    threads = [threading.Thread(target=work, args=(i,), name=f"caller-{i}") for i in range(CALLER_COUNT)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors
    return results


def _assert_all_in_lxml_thread(names: list[str]) -> None:
    assert len(names) >= CALLER_COUNT
    assert all(name.startswith("lxml") for name in names), names


def test_scraper_extracts_in_lxml_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    import trafilatura

    from aifred.lib.tools.scraper_tool import WebScraperTool

    page = "<html><head><title>Titel</title></head><body><article><p>" + "Ein Satz im Artikel. " * 40 + "</p></article></body></html>"
    scraper = WebScraperTool()
    monkeypatch.setattr(scraper, "_safe_request", lambda *a, **k: SimpleNamespace(ok=True, text=page))
    names = _record_threads(monkeypatch, trafilatura, "extract")
    results = _call_from_plain_threads(lambda: scraper._scrape_with_trafilatura("https://example.org/"))
    assert all(result["success"] for result in results), results
    _assert_all_in_lxml_thread(names)


_MULTISTATUS = b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response><d:href>/remote.php/dav/files/u/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
  <d:response><d:href>/remote.php/dav/files/u/a.txt</d:href>
    <d:propstat><d:prop><d:resourcetype/><d:getcontentlength>5</d:getcontentlength><d:getetag>"e1"</d:getetag></d:prop>
    <d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
</d:multistatus>"""


def test_dav_multistatus_parses_in_lxml_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    from lxml import etree

    from aifred.plugins.tools.nextcloud.dav import DavClient

    client = DavClient("https://cloud.example.org", "u", "pw", 5.0)
    answer = SimpleNamespace(status_code=207, content=_MULTISTATUS)
    monkeypatch.setattr(client, "_client", SimpleNamespace(request=lambda *a, **k: answer))
    names = _record_threads(monkeypatch, etree, "fromstring")
    results = _call_from_plain_threads(lambda: client.list_files(""))
    assert all([entry.path for entry in result] == ["a.txt"] for result in results), results
    _assert_all_in_lxml_thread(names)


def test_mail_html_text_parses_in_lxml_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    from lxml import html

    from aifred.plugins.channels.email_channel.client import _html_text

    names = _record_threads(monkeypatch, html, "fromstring")
    results = _call_from_plain_threads(lambda: _html_text("<html><body><p>Hallo</p><p>Welt</p></body></html>"))
    assert all("Hallo" in result and "Welt" in result for result in results), results
    _assert_all_in_lxml_thread(names)


def test_docx_read_in_lxml_thread(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import docx

    from aifred.lib.document_store import _read_docx

    path = tmp_path / "a.docx"
    document = docx.Document()
    document.add_paragraph("Absatz eins")
    document.save(str(path))
    names = _record_threads(monkeypatch, docx, "Document")
    results = _call_from_plain_threads(lambda: _read_docx(path))
    assert all(result == "Absatz eins" for result in results), results
    _assert_all_in_lxml_thread(names)


def test_xlsx_read_in_lxml_thread(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import openpyxl

    from aifred.lib.document_store import _read_xlsx

    path = tmp_path / "a.xlsx"
    workbook = openpyxl.Workbook()
    workbook.active.append(["Spalte", "Wert"])
    workbook.active.append(["a", "1"])
    workbook.save(str(path))
    names = _record_threads(monkeypatch, openpyxl, "load_workbook")
    results = _call_from_plain_threads(lambda: _read_xlsx(path))
    assert all("Spalte" in result for result in results), results
    _assert_all_in_lxml_thread(names)


def test_pptx_read_in_lxml_thread(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import pptx

    from aifred.lib.document_store import _read_pptx

    path = tmp_path / "a.pptx"
    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    slide.shapes.title.text = "Folientitel"
    presentation.save(str(path))
    names = _record_threads(monkeypatch, pptx, "Presentation")
    results = _call_from_plain_threads(lambda: _read_pptx(path))
    assert all("Folientitel" in result for result in results), results
    _assert_all_in_lxml_thread(names)


@pytest.mark.parametrize("kind", ["docx", "pptx"])
def test_office_documents_are_freed_in_lxml_thread(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str) -> None:
    """python-docx and python-pptx hold their lxml trees in reference cycles: returning plain data alone leaves
    them to the cyclic collector of whichever thread runs it next. They must be gone, freed in the lxml thread,
    by the time the reader returns."""
    import gc
    import weakref

    import docx
    import pptx

    from aifred.lib.document_store import _read_docx, _read_pptx

    path = tmp_path / f"a.{kind}"
    owner: ModuleType
    read: Callable[[Path], str]
    if kind == "docx":
        written = docx.Document()
        written.add_paragraph("Absatz")
        written.save(str(path))
        owner, attribute, read = docx, "Document", _read_docx
    else:
        pptx.Presentation().save(str(path))
        owner, attribute, read = pptx, "Presentation", _read_pptx
    freed_in: list[str] = []
    real = getattr(owner, attribute)

    def opened(*args: Any, **kwargs: Any) -> Any:
        document = real(*args, **kwargs)
        weakref.finalize(document, lambda: freed_in.append(threading.current_thread().name))
        return document

    monkeypatch.setattr(owner, attribute, opened)
    gc.collect()
    gc.disable()  # no automatic collection may free the cycles in this thread meanwhile
    try:
        read(path)
        assert len(freed_in) == 1 and freed_in[0].startswith("lxml"), freed_in
    finally:
        gc.enable()


def test_run_in_lxml_thread_returns_result_and_runs_in_lxml_thread() -> None:
    from aifred.lib.lxml_thread import run_in_lxml_thread

    def add(a: int, b: int = 0) -> int:
        return a + b

    assert run_in_lxml_thread(add, 1, b=2) == 3
    assert run_in_lxml_thread(lambda: threading.current_thread().name).startswith("lxml")


def test_run_in_lxml_thread_refuses_the_lxml_thread_itself() -> None:
    from aifred.lib.lxml_thread import run_in_lxml_thread

    def nothing() -> None:
        return None

    def call_again() -> None:
        run_in_lxml_thread(nothing)

    with pytest.raises(RuntimeError, match="lxml thread itself"):
        run_in_lxml_thread(call_again)


def test_run_in_lxml_thread_refuses_a_running_event_loop() -> None:
    import asyncio

    from aifred.lib.lxml_thread import run_in_lxml_thread

    async def call() -> None:
        run_in_lxml_thread(lambda: None)

    with pytest.raises(RuntimeError, match="event loop"):
        asyncio.run(call())


def _imports_lxml_user(tree: ast.AST) -> int | None:
    """Line of the first import of a libxml2-using package (also inside functions), else None."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots = [node.module.split(".")[0]]
        else:
            continue
        if any(root in LXML_USERS for root in roots):
            return node.lineno
    return None


def _uses_lxml_thread(tree: ast.AST) -> bool:
    names = ("run_in_lxml_thread", "run_in_lxml_thread_freeing_cycles")
    return any(isinstance(node, ast.Name) and node.id in names for node in ast.walk(tree))


def test_every_module_using_libxml2_goes_through_the_lxml_thread() -> None:
    offenders = []
    for path in sorted(AIFRED_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        line = _imports_lxml_user(tree)
        if line is not None and not _uses_lxml_thread(tree):
            offenders.append(f"{path.relative_to(AIFRED_DIR.parent)}:{line}")
    assert not offenders, "imports lxml, trafilatura, docx ... but never uses run_in_lxml_thread: " + ", ".join(offenders)
