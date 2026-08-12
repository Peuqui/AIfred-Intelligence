"""Tests for aifred.lib.credential_broker — centralized secret management."""

import os
from unittest.mock import patch


from aifred.lib.credential_broker import CredentialBroker, broker, _CREDENTIAL_MAP


class TestCredentialBroker:
    def setup_method(self):
        self.broker = CredentialBroker()

    def test_get_returns_env_value(self):
        with patch.dict(os.environ, {"EMAIL_PASSWORD": "secret123"}):
            assert self.broker.get("email", "password") == "secret123"

    def test_get_returns_empty_when_not_set(self):
        with patch.dict(os.environ, {}, clear=True):
            # Ensure the key is not set
            os.environ.pop("EMAIL_PASSWORD", None)
            assert self.broker.get("email", "password") == ""

    def test_is_set_true(self):
        with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "xoxb-123"}):
            assert self.broker.is_set("discord", "bot_token") is True

    def test_is_set_false(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DISCORD_BOT_TOKEN", None)
            assert self.broker.is_set("discord", "bot_token") is False

    def test_set_runtime(self):
        self.broker.set_runtime("email", "password", "new_secret")
        assert os.environ.get("EMAIL_PASSWORD") == "new_secret"
        # Cleanup
        os.environ.pop("EMAIL_PASSWORD", None)

    def test_get_env_key(self):
        assert self.broker.get_env_key("email", "password") == "EMAIL_PASSWORD"
        assert self.broker.get_env_key("discord", "bot_token") == "DISCORD_BOT_TOKEN"
        assert self.broker.get_env_key("cloud_claude", "api_key") == "ANTHROPIC_API_KEY"

    def test_unknown_service_falls_back_to_convention(self):
        """Unknown (service, key) pairs use SERVICE_KEY as env var name."""
        key = self.broker.get_env_key("telegram", "bot_token")
        assert key == "TELEGRAM_BOT_TOKEN"

    def test_singleton_is_broker_instance(self):
        assert isinstance(broker, CredentialBroker)


class TestCredentialMap:
    def test_all_email_fields_mapped(self):
        email_keys = [k for k in _CREDENTIAL_MAP if k[0] == "email"]
        expected = {"imap_host", "imap_port", "smtp_host", "smtp_port", "user", "password", "from", "enabled", "allowed_senders"}
        actual = {k[1] for k in email_keys}
        assert actual == expected

    def test_all_discord_fields_mapped(self):
        discord_keys = [k for k in _CREDENTIAL_MAP if k[0] == "discord"]
        expected = {"bot_token", "channel_ids", "allowed_users", "enabled"}
        actual = {k[1] for k in discord_keys}
        assert actual == expected

    def test_cloud_providers_mapped(self):
        assert ("cloud_claude", "api_key") in _CREDENTIAL_MAP
        assert ("cloud_qwen", "api_key") in _CREDENTIAL_MAP
        assert ("cloud_deepseek", "api_key") in _CREDENTIAL_MAP
        assert ("cloud_kimi", "api_key") in _CREDENTIAL_MAP

    def test_map_values_are_env_var_names(self):
        """All mapped values should look like environment variable names."""
        for (service, key), env_var in _CREDENTIAL_MAP.items():
            assert env_var == env_var.upper(), f"Env var {env_var} should be uppercase"
            assert " " not in env_var, f"Env var {env_var} should not contain spaces"
