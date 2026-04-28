"""Generic OAuth 2.0 broker: provider registry, token storage, auto-refresh.

Token storage: data/oauth_tokens.json
  Each provider's token is Fernet-encrypted individually so the file is
  safe to inspect (structure visible) but values are unreadable without
  the key.

Encryption key: data/oauth_encryption_key.bin
  Auto-generated on first use. File permissions set to 0o600.
  Never committed to git (data/ is in .gitignore).

State parameter: in-memory dict with 10-minute TTL per pending flow.
  Prevents CSRF on the callback endpoint.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Optional

from cryptography.fernet import Fernet

from ..config import DATA_DIR

logger = logging.getLogger(__name__)

_KEY_FILE = DATA_DIR / "oauth_encryption_key.bin"
_TOKENS_FILE = DATA_DIR / "oauth_tokens.json"

# Pending OAuth states: state_token → (provider_name, expiry_ts)
_STATE_TTL_SECONDS = 600


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str
    expiry: float        # Unix timestamp when access_token expires
    scopes: list[str]
    token_type: str = "Bearer"


class OAuthProvider(ABC):
    """Protocol every OAuth provider must implement."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def get_auth_url(self, scopes: list[str], redirect_uri: str, state: str) -> str: ...

    @abstractmethod
    async def exchange_code(self, code: str, redirect_uri: str) -> TokenSet: ...

    @abstractmethod
    async def refresh(self, token_set: TokenSet) -> TokenSet: ...


class OAuthBroker:
    """Central OAuth broker.  Plugins call get_token(); the broker handles
    storage and transparent token refresh.
    """

    def __init__(self) -> None:
        self._providers: dict[str, OAuthProvider] = {}
        # state_token → (provider_name, expiry_ts, redirect_uri)
        # redirect_uri MUST be identical at auth-url generation and token exchange,
        # otherwise Google rejects with 400. Storing it here removes the need to
        # reconstruct it in the callback (which is unreliable behind a reverse proxy).
        self._pending: dict[str, tuple[str, float, str]] = {}

    def register(self, provider: OAuthProvider) -> None:
        self._providers[provider.name] = provider
        logger.info("OAuth provider registered: %s", provider.name)

    # ------------------------------------------------------------------
    # Called by Settings UI / API to initiate a new connection
    # ------------------------------------------------------------------

    def get_auth_url(self, provider_name: str, scopes: list[str], redirect_uri: str) -> str:
        """Generate an authorization URL.  State token is stored for CSRF check."""
        if provider_name not in self._providers:
            raise KeyError(f"Unknown OAuth provider: {provider_name}")
        state = secrets.token_urlsafe(32)
        self._pending[state] = (provider_name, time.time() + _STATE_TTL_SECONDS, redirect_uri)
        self._purge_expired_states()
        return self._providers[provider_name].get_auth_url(scopes, redirect_uri, state)

    async def handle_callback(self, state: str, code: str) -> str:
        """Exchange authorization code for tokens.  Returns provider name.

        The redirect_uri used in the token exchange is the one we recorded
        when the auth URL was created — Google requires byte-for-byte match.
        """
        entry = self._pending.pop(state, None)
        if entry is None:
            raise ValueError("Unknown or expired OAuth state — restart the login flow")
        provider_name, expiry, redirect_uri = entry
        if time.time() > expiry:
            raise ValueError("OAuth state expired — restart the login flow")
        provider = self._providers[provider_name]
        token_set = await provider.exchange_code(code, redirect_uri)
        _save_token(provider_name, token_set)
        logger.info("OAuth tokens stored for provider: %s", provider_name)
        return provider_name

    # ------------------------------------------------------------------
    # Called by plugins at runtime
    # ------------------------------------------------------------------

    async def get_token(self, provider_name: str) -> str:
        """Return a valid access token, refreshing transparently if expired."""
        token_set = _load_token(provider_name)
        if token_set is None:
            raise RuntimeError(
                f"Not connected to {provider_name} — "
                "complete the OAuth flow in AIfred settings first"
            )
        # Refresh 60 s before actual expiry to avoid mid-request failures
        if time.time() >= token_set.expiry - 60:
            if provider_name not in self._providers:
                raise RuntimeError(f"Provider {provider_name} not registered")
            token_set = await self._providers[provider_name].refresh(token_set)
            _save_token(provider_name, token_set)
            logger.debug("OAuth token refreshed for provider: %s", provider_name)
        return token_set.access_token

    def is_connected(self, provider_name: str) -> bool:
        return _load_token(provider_name) is not None

    def disconnect(self, provider_name: str) -> None:
        tokens = _load_all_tokens()
        if provider_name in tokens:
            tokens.pop(provider_name)
            _save_all_tokens(tokens)
            logger.info("OAuth tokens removed for provider: %s", provider_name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _purge_expired_states(self) -> None:
        now = time.time()
        self._pending = {s: v for s, v in self._pending.items() if v[1] > now}


# ───────────────────────────────────────────────────���──────────────────
# Encrypted token storage
# ─────────────────────────────────────────��────────────────────────────

def _get_fernet() -> Fernet:
    if _KEY_FILE.exists():
        return Fernet(_KEY_FILE.read_bytes())
    key = Fernet.generate_key()
    _KEY_FILE.write_bytes(key)
    _KEY_FILE.chmod(0o600)
    logger.info("New OAuth encryption key generated: %s", _KEY_FILE)
    return Fernet(key)


def _load_all_tokens() -> dict[str, TokenSet]:
    if not _TOKENS_FILE.exists():
        return {}
    fernet = _get_fernet()
    try:
        raw: dict[str, str] = json.loads(_TOKENS_FILE.read_text())
        result: dict[str, TokenSet] = {}
        for name, encrypted in raw.items():
            data = json.loads(fernet.decrypt(encrypted.encode()).decode())
            result[name] = TokenSet(**data)
        return result
    except Exception:
        logger.exception("Failed to load OAuth tokens — file corrupt or key changed")
        return {}


def _save_all_tokens(tokens: dict[str, TokenSet]) -> None:
    fernet = _get_fernet()
    raw = {
        name: fernet.encrypt(json.dumps(asdict(ts)).encode()).decode()
        for name, ts in tokens.items()
    }
    _TOKENS_FILE.write_text(json.dumps(raw, indent=2))
    _TOKENS_FILE.chmod(0o600)


def _load_token(provider_name: str) -> Optional[TokenSet]:
    return _load_all_tokens().get(provider_name)


def _save_token(provider_name: str, token_set: TokenSet) -> None:
    tokens = _load_all_tokens()
    tokens[provider_name] = token_set
    _save_all_tokens(tokens)


# Singleton
oauth_broker = OAuthBroker()
