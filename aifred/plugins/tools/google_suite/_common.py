"""Shared helpers for the Google-Suite tool modules (SSOT)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import httpx

# Tool descriptions live in the plugin ROOT (google_suite/prompts/tools/),
# not per subpackage — the plugin stays atomic as a whole.
PLUGIN_DIR = str(Path(__file__).resolve().parent)


async def _get_token() -> str:
    from ....lib.oauth.broker import oauth_broker
    token = await oauth_broker.get_token("google")
    if not token:
        raise RuntimeError("Google is not connected. Authorize it in the settings first.")
    return token


async def _google_request(
    method: str,
    url: str,
    *,
    params: Optional[dict[str, Any]] = None,
    json: Optional[dict[str, Any]] = None,
    timeout: float = 15.0,
) -> httpx.Response:
    """One HTTP path for every Google API call (SSOT).

    Bündelt Token-Beschaffung, Bearer-Header, Timeout und — entscheidend —
    ``raise_for_status``: die beiden Bugs mit still verschluckten Fehlern
    (members:modify) waren genau die handgeschriebenen Abweichler vom
    24× kopierten Boilerplate, das dieser Helper ersetzt.
    """
    token = await _get_token()
    async with httpx.AsyncClient() as client:
        r = await client.request(
            method,
            url,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            json=json,
            timeout=timeout,
        )
    r.raise_for_status()
    return r
