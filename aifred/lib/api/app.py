"""FastAPI app core: app instance, login-cookie middleware, global state access."""


from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..user_error import UserError

API_VERSION = "3.1.0"


# ============================================================
# FastAPI App
# ============================================================

api_app = FastAPI(
    title="AIfred Intelligence API",
    description="REST API for remote control of AIfred Intelligence",
    version=API_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

# ============================================================
# Refusals in the UI language
# ============================================================
# Every text the API answers with is one the user may read: a UserError carries its i18n key, the handler words it in
# the language the UI is set to; a request FastAPI cannot read names its fields the same way.


def _ui_language() -> str:
    from ..prompt_loader import get_language

    return get_language()


@api_app.exception_handler(UserError)
async def _user_error(_request: Request, error: UserError) -> JSONResponse:
    return JSONResponse({"detail": error.message(_ui_language())}, status_code=error.status)


@api_app.exception_handler(RequestValidationError)
async def _unreadable_request(_request: Request, error: RequestValidationError) -> JSONResponse:
    from ..i18n import t

    fields = sorted({".".join(str(part) for part in problem["loc"] if part not in ("body", "query", "path")) or "?"
                     for problem in error.errors()})
    return JSONResponse({"detail": t("error_unreadable_request", lang=_ui_language(), fields=", ".join(fields))},
                        status_code=422)


# NOTE: CORS middleware is NOT added here.
# Reflex handles CORS via its own middleware when using api_transformer.
# Adding CORSMiddleware here causes "ASGI flow error: Connection already upgraded"
# because it conflicts with Reflex's WebSocket upgrade handling.


# ============================================================
# App-Level Auth (login cookie required)
# ============================================================
# Defense in depth below the reverse proxy's login: the server listens on all
# interfaces (LAN clients reach port 8002 directly, AIFRED_SERVER_HOST), so
# without this gate anyone on the network (or an SSRF from a scraped page)
# could hit /system/restart-*, /vision/snapshot, /chat/history etc. Same
# cookie the web login sets (see AuthenticatedStaticFiles for the static
# twin) — browser calls are same-origin and carry it automatically.
#
# Exempt: inject + webhook + announce + tts (enforce their own service token
# via require_service_token), the OAuth callback (arrives as a redirect
# from the provider, cookie not guaranteed), the Vue login and registration (they set the cookie; /auth/me
# answers null without one) and the
# Vue app shell under /app/ (code, colours, UI texts; no user data, the login form is part of it).

_COOKIE_EXEMPT_PATHS = (
    "/chat/inject", "/agent/trigger", "/audio/announce", "/audio/announce/rooms",
    "/tts/entries", "/auth/login", "/auth/register", "/auth/me",
)


@api_app.middleware("http")
async def _require_login_cookie(request: Request, call_next):
    from ..auth import USERNAME_COOKIE_NAME, verify_signed_username

    # Mounted under /api (aifred/server.py) — scope path is mount-relative, but be
    # tolerant if the app ever runs unmounted (tests, standalone).
    path = request.scope.get("path", "")
    if path.startswith("/api/"):
        path = path[4:]

    exempt = (
        path in _COOKIE_EXEMPT_PATHS
        or (path.startswith("/oauth/") and path.endswith("/callback"))
        or path.startswith("/app/")  # Vue app shell, loads before the login (app_shell.py)
    )
    if not exempt:
        cookie = request.cookies.get(USERNAME_COOKIE_NAME, "")
        if verify_signed_username(cookie) is None:
            from ..i18n import t

            return JSONResponse({"detail": t("error_login_required", lang=_ui_language())}, status_code=403)
    return await call_next(request)

