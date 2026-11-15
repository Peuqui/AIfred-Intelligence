"""A file as a download. An HTML page comes with everything it loads from /vendor/ written into it (lib/standalone_page):
saved and opened somewhere else, it is one file that works. Every other file is sent as it is."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi.responses import FileResponse, Response

from ..standalone_page import standalone_page

PAGE_SUFFIXES = (".html", ".htm")


def is_page(name: str) -> bool:
    return name.lower().endswith(PAGE_SUFFIXES)


def attachment(name: str) -> dict[str, str]:
    """Content-Disposition with the name in RFC 5987 form: umlauts arrive intact."""
    return {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"}


def page_download(page: bytes, name: str) -> Response:
    """The page as a standalone file. A page that is not UTF-8 is sent as it is: the libraries can only be written
    into text we can read."""
    try:
        body = standalone_page(page.decode("utf-8")).encode("utf-8")
    except UnicodeDecodeError:
        body = page
    return Response(body, media_type="text/html; charset=utf-8", headers=attachment(name))


def download_response(file: Path) -> Response:
    if is_page(file.name):
        return page_download(file.read_bytes(), file.name)
    return FileResponse(file, filename=file.name)
