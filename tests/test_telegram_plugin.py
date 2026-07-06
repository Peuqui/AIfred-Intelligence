"""Tests for Telegram channel plugin — whitelist, message splitting, helpers."""

import os
from unittest.mock import patch


from aifred.plugins.channels.telegram_channel import (
    TelegramChannel,
    _is_user_allowed,
    _split_message,
)


# ── User Whitelist ────────────────────────────────────────────

class TestIsUserAllowed:
    def test_empty_whitelist_blocks_all(self):
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": ""}):
            assert _is_user_allowed(123456) is False

    def test_star_wildcard_blocks_everyone(self):
        # TD8: die "*"-Wildcard (weltoffener Bot) wird nicht mehr
        # unterstützt — fail-closed, explizite IDs sind Pflicht.
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}):
            assert _is_user_allowed(123456) is False

    def test_specific_id_allowed(self):
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "111, 222, 333"}):
            assert _is_user_allowed(222) is True
            assert _is_user_allowed(444) is False

    def test_single_id(self):
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "999"}):
            assert _is_user_allowed(999) is True
            assert _is_user_allowed(111) is False

    def test_whitespace_handling(self):
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "  111 , 222 , 333  "}):
            assert _is_user_allowed(222) is True

    def test_not_set_blocks_all(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("TELEGRAM_ALLOWED_USERS", None)
            assert _is_user_allowed(123) is False


# ── Message Splitting ─────────────────────────────────────────

class TestSplitMessage:
    def test_short_message(self):
        assert _split_message("Hello", 4096) == ["Hello"]

    def test_exact_limit(self):
        text = "x" * 4096
        assert _split_message(text, 4096) == [text]

    def test_splits_at_newline(self):
        text = "Line 1\nLine 2\nLine 3"
        chunks = _split_message(text, 14)
        assert len(chunks) == 2
        assert chunks[0] == "Line 1\nLine 2"

    def test_splits_without_newline(self):
        text = "a" * 100
        chunks = _split_message(text, 30)
        assert len(chunks) == 4
        assert all(len(c) <= 30 for c in chunks)

    def test_empty_message(self):
        assert _split_message("", 4096) == [""]


# ── Channel Plugin ────────────────────────────────────────────

class TestTelegramChannel:
    def setup_method(self):
        self.channel = TelegramChannel()

    def test_name(self):
        assert self.channel.name == "telegram"

    def test_display_name(self):
        assert self.channel.display_name == "Telegram"

    def test_icon(self):
        assert self.channel.icon == "send"

    def test_always_reply(self):
        assert self.channel.always_reply is True

    def test_credential_fields(self):
        fields = self.channel.credential_fields
        env_keys = [f.env_key for f in fields]
        assert "TELEGRAM_BOT_TOKEN" in env_keys
        assert "TELEGRAM_ALLOWED_USERS" in env_keys

    def test_is_configured_false_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("TELEGRAM_ENABLED", None)
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            assert self.channel.is_configured() is False

    def test_is_configured_true(self):
        with patch.dict(os.environ, {"TELEGRAM_ENABLED": "true", "TELEGRAM_BOT_TOKEN": "123:ABC"}):
            assert self.channel.is_configured() is True

    def test_apply_credentials(self):
        self.channel.apply_credentials({
            "TELEGRAM_BOT_TOKEN": "123:ABC",
            "TELEGRAM_ALLOWED_USERS": "111,222",
        })
        assert os.environ.get("TELEGRAM_ENABLED") == "true"
        assert os.environ.get("TELEGRAM_BOT_TOKEN") == "123:ABC"
        assert os.environ.get("TELEGRAM_ALLOWED_USERS") == "111,222"
        # Cleanup
        os.environ.pop("TELEGRAM_ENABLED", None)
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_ALLOWED_USERS", None)
