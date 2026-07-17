"""FastAPI app core: app instance, login-cookie middleware, global state access."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from typing import Dict, Any

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

# NOTE: CORS middleware is NOT added here.
# Reflex handles CORS via its own middleware when using api_transformer.
# Adding CORSMiddleware here causes "ASGI flow error: Connection already upgraded"
# because it conflicts with Reflex's WebSocket upgrade handling.


# ============================================================
# App-Level Auth (login cookie required)
# ============================================================
# Defense in depth below nginx basic-auth: the backend binds 127.0.0.1, but
# without this gate every local process (or an SSRF from a scraped page)
# could hit /system/restart-*, /vision/snapshot, /chat/history etc. Same
# cookie the web login sets (see AuthenticatedStaticFiles for the static
# twin) — browser calls are same-origin and carry it automatically.
#
# Exempt: inject + webhook (enforce their own service token via
# require_service_token) and the OAuth callback (arrives as a redirect
# from the provider, cookie not guaranteed).

_COOKIE_EXEMPT_PATHS = ("/chat/inject", "/agent/trigger")


@api_app.middleware("http")
async def _require_login_cookie(request: Request, call_next):
    from ..browser_storage import USERNAME_COOKIE_NAME
    from ..auth import verify_signed_username

    # Mounted under /api (aifred.py) — scope path is mount-relative, but be
    # tolerant if the app ever runs unmounted (tests, standalone).
    path = request.scope.get("path", "")
    if path.startswith("/api/"):
        path = path[4:]

    exempt = (
        path in _COOKIE_EXEMPT_PATHS
        or (path.startswith("/oauth/") and path.endswith("/callback"))
    )
    if not exempt:
        cookie = request.cookies.get(USERNAME_COOKIE_NAME, "")
        if verify_signed_username(cookie) is None:
            return JSONResponse(
                {"detail": "Login cookie required"}, status_code=403
            )
    return await call_next(request)


# ============================================================
# Global State Access
# ============================================================

def get_global_backend_state() -> Dict[str, Any]:
    """Get reference to global backend state from state.py"""
    try:
        from ...state import _global_backend_state
        return _global_backend_state
    except ImportError:
        return {}


# ============================================================
# Export for api_transformer
# ============================================================

def get_api_app() -> FastAPI:
    """
    Get the FastAPI app for use with Reflex api_transformer.

    Usage in rxconfig.py or aifred.py:
        from aifred.lib.api import get_api_app
        app = rx.App(api_transformer=get_api_app())
    """
    return api_app
