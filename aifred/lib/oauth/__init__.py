"""OAuth Broker — generic OAuth 2.0 token management.

Providers register themselves here; plugins call oauth_broker.get_token()
to obtain a valid (auto-refreshed) access token.

Usage::

    from aifred.lib.oauth import oauth_broker

    # In a plugin:
    token = await oauth_broker.get_token("google")

    # Check connection status:
    if oauth_broker.is_connected("google"):
        ...
"""

from .broker import OAuthBroker, OAuthProvider, TokenSet, oauth_broker
from .google import GoogleProvider

# Register built-in providers
oauth_broker.register(GoogleProvider())

__all__ = ["oauth_broker", "OAuthBroker", "OAuthProvider", "TokenSet", "GoogleProvider"]
