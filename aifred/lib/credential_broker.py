"""Credential Broker — single source of truth for all secrets.

Credentials are read exclusively through this broker. No plugin or
library should access os.environ or config.py for passwords, tokens,
or API keys directly.

The broker:
- Reads credentials from os.environ (populated from .env at startup)
- Logs every credential access in the audit system
- Never exposes credential values in str(), repr(), logs, or errors
- Provides a clean API: broker.get("email", "password")
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


class CredentialBroker:
    """Central credential manager. Singleton via module-level `broker` instance."""

    def get(self, service: str, key: str) -> str:
        """Get a credential value. Returns empty string if not set.

        Args:
            service: Service name (e.g. "email", "discord", "cloud_claude")
            key: Credential key (e.g. "password", "bot_token", "api_key")

        Returns:
            The credential value, or empty string if not configured.
        """
        env_key = self._resolve_env_key(service, key)
        value = os.environ.get(env_key, "")
        if value:
            logger.debug("Credential accessed: %s/%s", service, key)
        return value

    def is_set(self, service: str, key: str) -> bool:
        """Check if a credential is configured (non-empty)."""
        return bool(self.get(service, key))

    def set_runtime(self, service: str, key: str, value: str) -> None:
        """Set a credential at runtime (e.g. from Settings UI).

        Updates os.environ so the credential is available to the broker.
        Does NOT write to .env (that's the Settings UI's job).
        """
        env_key = self._resolve_env_key(service, key)
        os.environ[env_key] = value
        logger.debug("Credential updated: %s/%s", service, key)

    def get_env_key(self, service: str, key: str) -> str:
        """Get the environment variable name for a credential. Public API."""
        return self._resolve_env_key(service, key)

    def _resolve_env_key(self, service: str, key: str) -> str:
        """Map (service, key) to environment variable name."""
        return _CREDENTIAL_MAP.get((service, key), f"{service.upper()}_{key.upper()}")


# Mapping from (service, key) to environment variable names.
# This is the ONLY place that knows which env vars hold which credentials.
_CREDENTIAL_MAP: dict[tuple[str, str], str] = {
    # Email
    ("email", "imap_host"): "EMAIL_IMAP_HOST",
    ("email", "imap_port"): "EMAIL_IMAP_PORT",
    ("email", "smtp_host"): "EMAIL_SMTP_HOST",
    ("email", "smtp_port"): "EMAIL_SMTP_PORT",
    ("email", "user"): "EMAIL_USER",
    ("email", "password"): "EMAIL_PASSWORD",
    ("email", "from"): "EMAIL_FROM",
    ("email", "enabled"): "EMAIL_ENABLED",
    ("email", "allowed_senders"): "EMAIL_ALLOWED_SENDERS",
    # Discord
    ("discord", "bot_token"): "DISCORD_BOT_TOKEN",
    ("discord", "channel_ids"): "DISCORD_CHANNEL_IDS",
    ("discord", "enabled"): "DISCORD_ENABLED",
    # Cloud API providers
    ("cloud_claude", "api_key"): "ANTHROPIC_API_KEY",
    ("cloud_qwen", "api_key"): "DASHSCOPE_API_KEY",
    ("cloud_deepseek", "api_key"): "DEEPSEEK_API_KEY",
    ("cloud_kimi", "api_key"): "MOONSHOT_API_KEY",
    # Telegram
    ("telegram", "bot_token"): "TELEGRAM_BOT_TOKEN",
    ("telegram", "allowed_users"): "TELEGRAM_ALLOWED_USERS",
    ("telegram", "enabled"): "TELEGRAM_ENABLED",
    # Webhook API
    ("webhook", "api_token"): "WEBHOOK_API_TOKEN",
    # DeepL Translation
    ("deepl", "api_key"): "DEEPL_API_KEY",
    # Google OAuth
    ("google", "client_id"): "GOOGLE_CLIENT_ID",
    ("google", "client_secret"): "GOOGLE_CLIENT_SECRET",
}


# Singleton
broker = CredentialBroker()
