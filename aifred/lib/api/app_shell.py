"""The Vue app shell: built frontend, colours and UI texts under /api/app/.

The shell loads before the login (the login form is part of it), so the cookie middleware
lets /app/ through; it holds no user data. Everything the app does afterwards goes through
the cookie-protected endpoints.
"""

from pathlib import Path

from fastapi.responses import JSONResponse, Response
from starlette.staticfiles import StaticFiles

from ...theme import css_variables
from ..i18n import TranslationManager
from ..user_error import Missing
from .app import api_app

FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"


@api_app.get("/app/theme.css", include_in_schema=False)
async def app_theme() -> Response:
    return Response(css_variables(), media_type="text/css")


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


# Registered after the two routes above, so they win over the mount. check_dir=False: before
# the first `npm run build` the folder does not exist and /app/ answers 404.
api_app.mount("/app", StaticFiles(directory=FRONTEND_DIST, html=True, check_dir=False), name="app_shell")
