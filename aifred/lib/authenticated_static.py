"""Cookie-authenticated static file serving.

The sensitive surveillance directories (Vigilantia frames, face crops, session
audio) are mounted just like the public ones, but every request must carry a
valid login cookie. This moves the access check from the reverse proxy down
into the app, so the files are also protected on localhost/LAN — not only
behind the reverse proxy's own login (which stays as the outer layer).

Auth is the same integrity-signed ``aifred_username`` cookie the web login
sets; verification goes through :mod:`aifred.lib.auth` (single source of
truth). Same-origin ``<img src="/_upload/...">`` requests carry the cookie
automatically (``path=/``, ``SameSite=Lax``), so embedded images keep working.
"""

from __future__ import annotations

import os

from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.staticfiles import PathLike, StaticFiles
from starlette.types import Receive, Scope, Send

# Reuse the framework-agnostic verifier — keeps the HMAC/secret logic in one
# place (lib/auth) instead of re-implementing the check here.
from .auth import USERNAME_COOKIE_NAME, verify_signed_username
from .config import PAGE_SANDBOX

PAGE_SUFFIXES = (".html", ".htm")


class AuthenticatedStaticFiles(StaticFiles):
    """:class:`StaticFiles` that rejects requests without a valid login cookie.

    A missing/forged cookie yields ``403`` instead of the file. Everything else
    (range requests, caching headers, 404s) is handled by the base class.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            cookie = Request(scope).cookies.get(USERNAME_COOKIE_NAME, "")
            if verify_signed_username(cookie) is None:
                response = PlainTextResponse("Forbidden", status_code=403)
                await response(scope, receive, send)
                return
        await super().__call__(scope, receive, send)

    def file_response(
        self, full_path: PathLike, stat_result: os.stat_result, scope: Scope, status_code: int = 200
    ) -> Response:
        """A page opens sandboxed, however it is reached (a link in a chat answer, the tab of an embedded page):
        written by an agent, it must not use AIfred's API with the login cookie (18.11.2026, before it could)."""
        response = super().file_response(full_path, stat_result, scope, status_code)
        if str(full_path).lower().endswith(PAGE_SUFFIXES):
            response.headers["Content-Security-Policy"] = PAGE_SANDBOX
        return response
