"""The Vue app shell: built frontend, colours and UI texts under /api/app/.

The shell loads before the login (the login form is part of it), so the cookie middleware
lets /app/ through; it holds no user data. Everything the app does afterwards goes through
the cookie-protected endpoints.
"""

from pathlib import Path

from collections.abc import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.staticfiles import StaticFiles

from ...theme import COLORS, css_variables
from ..i18n import TranslationManager
from ..user_error import Missing
from .app import api_app

FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"
# Written by the build (frontend/vite.config.ts); its content goes with every answer, so an open page notices a newer
# build and loads it (frontend/src/api/client.ts reads the header).
BUILD_ID_FILE = FRONTEND_DIST / "build-id.txt"
BUILD_ID_HEADER = "X-Build-Id"
APP_NAME = "AIfred Intelligence"
APP_SHORT_NAME = "AIfred"


@api_app.middleware("http")
async def _send_build_id(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    response = await call_next(request)
    # Read every time: a build replaces the file while the server runs. Before the first build there is none to name.
    if BUILD_ID_FILE.is_file():
        response.headers[BUILD_ID_HEADER] = BUILD_ID_FILE.read_text(encoding="utf-8")
    return response


@api_app.get("/app/theme.css", include_in_schema=False)
async def app_theme() -> Response:
    return Response(css_variables(), media_type="text/css")


@api_app.get("/app/manifest.webmanifest", include_in_schema=False)
async def app_manifest() -> JSONResponse:
    """What makes the shell installable as an app (home screen icon, own window), without a service worker. The
    colours come from theme.py; the icons are the hat (frontend/public). Paths are relative to this file."""
    return JSONResponse(
        {
            "name": APP_NAME,
            "short_name": APP_SHORT_NAME,
            "start_url": "./",
            "scope": "./",
            "display": "standalone",
            "background_color": COLORS["page_bg"],
            "theme_color": COLORS["page_bg"],
            "icons": [
                {"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"},
            ],
        },
        media_type="application/manifest+json",
    )


@api_app.get("/app/ui-settings", tags=["App"])
async def app_ui_settings() -> dict[str, str]:
    """What the shell needs before the login: the UI language of the settings."""
    from ..settings import get_default_settings, load_settings

    return {"ui_language": (load_settings() or get_default_settings())["ui_language"]}


@api_app.get("/app/i18n/{lang}", tags=["App"])
async def app_texts(lang: str) -> JSONResponse:
    texts = TranslationManager._translations.get(lang)
    if texts is None:
        raise Missing("no_ui_texts", language=lang)
    return JSONResponse(texts)


# Registered after the routes above, so they win over the mount. check_dir=False: before
# the first `npm run build` the folder does not exist and /app/ answers 404.
api_app.mount("/app", StaticFiles(directory=FRONTEND_DIST, html=True, check_dir=False), name="app_shell")
