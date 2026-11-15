"""The local JS libraries (assets/vendor, under /vendor): what a page loads as a module script is fetched with CORS,
and a page of the chat's sandbox frame has no origin of its own, so the files say that any page may read them. They
are the public libraries, nothing of the user's."""

from __future__ import annotations

import os

from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


class VendorFiles(StaticFiles):
    def file_response(self, full_path: str | os.PathLike[str], stat_result: os.stat_result, scope: Scope, status_code: int = 200) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response
