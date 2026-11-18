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


# A page the viewer opens runs its scripts, but in a sandbox without an origin: it cannot read AIfred's API or cookies.
PAGE_SANDBOX = "sandbox allow-scripts"


def attachment(name: str, inline: bool = False) -> dict[str, str]:
    """Content-Disposition with the name in RFC 5987 form: umlauts arrive intact. ``inline``: the browser shows the
    file (PDF, audio, video) instead of saving it."""
    return {"Content-Disposition": f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(name)}"}


def page_response(page: bytes, name: str) -> Response:
    """A page to look at: inline, sandboxed. The ONE place that sets how a page opens in a tab."""
    headers = {**attachment(name, inline=True), "Content-Security-Policy": PAGE_SANDBOX}
    return Response(page, media_type="text/html", headers=headers)


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


def view_response(file: Path) -> Response:
    """The file as the browser shows it (``?inline=true`` of the download endpoints, the requester's preview): a page
    sandboxed, anything else inline with its type."""
    if is_page(file.name):
        return page_response(file.read_bytes(), file.name)
    return FileResponse(file, filename=file.name, content_disposition_type="inline")
