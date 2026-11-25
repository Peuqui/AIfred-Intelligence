"""The text of a file as the search index's parsers read it: ONE place for the preview of every file location (the
local documents folder, a document source, a file area of the requester): which types have a reader (the index's
parsers first, else the raw text of a text type) and what a damaged file answers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from ..document_sources import SourceDownload
from ..logging_utils import log_exception
from ..user_error import Invalid


class DocumentPreview(BaseModel):
    name: str
    text: str


def _suffix(name: str) -> str:
    return Path(name).suffix.lower()


def require_reader(name: str) -> None:
    """The preview is the text of the index's parser, or the raw text of a text type; any other type has none."""
    from ..config import TEXT_PREVIEW_EXTENSIONS
    from ..document_store import PARSERS

    if _suffix(name) not in PARSERS and _suffix(name) not in TEXT_PREVIEW_EXTENSIONS:
        raise Invalid("no_parser", ext=_suffix(name))


async def _preview(name: str, parse: Callable[[], str]) -> DocumentPreview:
    try:
        text = await asyncio.to_thread(parse)
    except Exception as error:  # a damaged file of any kind: the page shows why it has no preview
        log_exception(f"Preview of {name} failed", error)
        raise Invalid("preview_failed", reason=str(error)) from error
    return DocumentPreview(name=name, text=text)


def _raw(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


async def preview_of_file(file: Path) -> DocumentPreview:
    """A file on this machine."""
    from ..document_store import PARSERS

    require_reader(file.name)
    parser = PARSERS.get(_suffix(file.name))
    return await _preview(file.name, (lambda: parser(file)) if parser else (lambda: _raw(file.read_bytes())))


async def preview_of_bytes(name: str, data: bytes) -> DocumentPreview:
    """A file that exists only in memory (a source's download)."""
    from ..document_store import PARSERS, document_text

    require_reader(name)
    return await _preview(name, (lambda: document_text(data, name)) if _suffix(name) in PARSERS else (lambda: _raw(data)))


async def preview_of_download(download: SourceDownload) -> DocumentPreview:
    """A document source's file. The reader check comes first (an id need not hold the name, Drive's: the type is known
    only now) and before the content is read."""
    require_reader(download.name)
    return await preview_of_bytes(download.name, b"".join([chunk async for chunk in download.chunks]))
