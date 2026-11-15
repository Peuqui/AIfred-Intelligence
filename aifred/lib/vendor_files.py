"""The local JS libraries (assets/vendor, under /vendor): what a page loads as a module script is fetched with CORS,
and a page of the chat's sandbox frame has no origin of its own, so the files say that any page may read them. They
are the public libraries, nothing of the user's."""

from __future__ import annotations

import os
from pathlib import Path

from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


class VendorFiles(StaticFiles):
    def file_response(self, full_path: str | os.PathLike[str], stat_result: os.stat_result, scope: Scope, status_code: int = 200) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response


def vendor_file(url_path: str) -> Path | None:
    """The file of the local libraries (``VENDOR_DIR``, with its sub folders: ``/vendor/three/addons/…``) that a page
    asks for under ``VENDOR_URL``; None for a path that leads out of the folder or to nothing."""
    from urllib.parse import unquote

    from .config import VENDOR_DIR, VENDOR_URL

    prefix = f"{VENDOR_URL}/"
    if not url_path.startswith(prefix):
        return None
    root = VENDOR_DIR.resolve()
    file = (root / unquote(url_path[len(prefix):])).resolve()
    return file if file.is_relative_to(root) and file.is_file() else None
