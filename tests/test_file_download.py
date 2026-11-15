"""A file as a download (lib/api/file_download.py): an HTML page as one standalone file, anything else as it is."""

from pathlib import Path

from fastapi.responses import FileResponse, Response

from aifred.lib.api.file_download import download_response, page_download


def test_a_page_comes_with_the_library_it_loads(tmp_path: Path) -> None:
    page = tmp_path / "Prüfstand.html"
    page.write_text('<script src="/vendor/three.min.js"></script><p>V8</p>', encoding="utf-8")
    response = download_response(page)
    assert type(response) is Response
    assert b"REVISION" in response.body and b"/vendor/" not in response.body
    assert "attachment" in response.headers["content-disposition"] and "Pr%C3%BCfstand.html" in response.headers["content-disposition"]


def test_any_other_file_is_sent_as_it_is(tmp_path: Path) -> None:
    other = tmp_path / "daten.bin"
    other.write_bytes(bytes(range(256)))
    assert isinstance(download_response(other), FileResponse)


def test_a_page_that_is_not_utf8_is_sent_as_it_is() -> None:
    latin = '<p>Prüfstand</p><script src="/vendor/three.min.js"></script>'.encode("latin-1")
    assert page_download(latin, "alt.html").body == latin
