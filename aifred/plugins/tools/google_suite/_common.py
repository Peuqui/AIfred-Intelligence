"""Shared helpers for the Google-Suite tool modules (SSOT)."""


async def _get_token() -> str:
    from ....lib.oauth.broker import oauth_broker
    token = await oauth_broker.get_token("google")
    if not token:
        raise RuntimeError("Google nicht verbunden. Bitte erst in den Einstellungen autorisieren.")
    return token
