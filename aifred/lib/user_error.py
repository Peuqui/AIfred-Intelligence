"""A refusal the user reads, in the language the UI is set to: an i18n key with its values (``error_<key>`` in
``i18n/de.json`` and ``en.json``). It is raised where the rule lives (lib, or an endpoint); the API's handler answers
with its status and the text in the UI language (``api/app.py``), the Reflex copy words it with ``message(lang)``. In
logs and ``str()`` it reads in English (debug output is English).

Each kind also is the built-in exception the callers caught so far (``Missing`` a ``KeyError``, ``Invalid`` a
``ValueError`` …), so their ``except`` still holds.
"""

from __future__ import annotations

from typing import Any


class UserError(Exception):
    """Base: the status the API answers with and the text's key."""

    status = 422

    def __init__(self, key: str, **values: Any) -> None:
        super().__init__(key)
        self.key = key
        self.values = values

    def message(self, lang: str | None = None) -> str:
        from .i18n import t

        return t(f"error_{self.key}", lang=lang, **self.values)

    def __str__(self) -> str:
        return self.message("en")


class Invalid(UserError, ValueError):
    """A value the rule does not take (422)."""

    status = 422


class BadRequest(UserError, ValueError):
    """A request that cannot be read as meant (400)."""

    status = 400


class Missing(UserError, KeyError):
    """Something named that does not exist (404)."""

    status = 404


class Conflict(UserError, RuntimeError):
    """Not now: something else runs, the name is taken, the state does not allow it (409)."""

    status = 409


class Refused(UserError, PermissionError):
    """Not allowed (403)."""

    status = 403


class Unavailable(UserError, ConnectionError):
    """A service it needs does not answer (503)."""

    status = 503


class UpstreamFailed(UserError, ConnectionError):
    """A service it asked answered with an error (502)."""

    status = 502


class NotDone(UserError, OSError):
    """It failed on this side: a file not written, a store that broke (500)."""

    status = 500


def said(error: BaseException, lang: str) -> str:
    """An error as an interface shows it: a refusal in the UI language, anything else as it reads."""
    return error.message(lang) if isinstance(error, UserError) else str(error)
