"""Login for the Vue app: the same signed cookie the Reflex login sets (lib/auth, browser_storage).

While Reflex and Vue run side by side both read the cookie, and Reflex reads it from JavaScript:
the cookie therefore keeps Reflex's attributes (no HttpOnly) until Reflex is gone.
"""

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .app import api_app
from .sessions import current_user


class Credentials(BaseModel):
    username: str
    password: str


class LoggedIn(BaseModel):
    user: str


def _with_cookie(response: JSONResponse, user: str) -> JSONResponse:
    from ..auth import sign_username
    from ..browser_storage import USERNAME_COOKIE_NAME
    from ..config import AUTH_COOKIE_MAX_AGE_DAYS

    response.set_cookie(
        USERNAME_COOKIE_NAME, sign_username(user), path="/",
        max_age=AUTH_COOKIE_MAX_AGE_DAYS * 24 * 60 * 60, samesite="lax",
    )
    return response


@api_app.post("/auth/login", response_model=LoggedIn, tags=["Auth"])
async def auth_login(credentials: Credentials) -> JSONResponse:
    from ..session_storage import account_exists, verify_account

    user = credentials.username.strip().lower()
    # One answer for an unknown account and a wrong password: the login does not tell which exist.
    if not user or not account_exists(user) or not verify_account(user, credentials.password):
        raise HTTPException(status_code=401, detail="Wrong user name or password")
    return _with_cookie(JSONResponse({"user": user}), user)


@api_app.get("/auth/me", response_model=LoggedIn, tags=["Auth"])
async def auth_me(request: Request) -> LoggedIn:
    return LoggedIn(user=current_user(request))


@api_app.post("/auth/logout", status_code=204, tags=["Auth"])
async def auth_logout() -> JSONResponse:
    from ..browser_storage import USERNAME_COOKIE_NAME

    response = JSONResponse(None, status_code=204)
    response.delete_cookie(USERNAME_COOKIE_NAME, path="/")
    return response
