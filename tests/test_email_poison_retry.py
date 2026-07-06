"""Tests für die E3-Bounded-Retry-/Quarantäne-Logik im E-Mail-Listener.

Kontrakt (SECURITY_FINDINGS.md E3):
- Wirft die Verarbeitung einer UID, wird sie erneut versucht (re-raise →
  äußere Schleife reconnected mit ~30 s Backoff), Fehlerzähler +1.
- Nach EMAIL_MAX_PROCESS_ATTEMPTS Fehlversuchen: Mail wird server-seitig
  geflaggt (\\Flagged) UND der Checkpoint vorgerückt (skip), damit die
  Queue nicht ewig blockiert.
- Ein erfolgreicher Durchlauf setzt den Zähler zurück.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

import aifred.plugins.channels.email_channel as ec
from aifred.plugins.channels.email_channel import EmailChannel


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clear_failures():
    ec._uid_failures.clear()
    yield
    ec._uid_failures.clear()


def _channel():
    ch = EmailChannel()
    ch.channel_log = MagicMock()
    ch._update_checkpoint = MagicMock()
    ch._quarantine_uid = MagicMock()
    return ch


def _patch_once(side_effect):
    """Patch _process_uid_once mit gegebenem side_effect."""
    return patch.object(EmailChannel, "_process_uid_once", side_effect=side_effect)


class TestBoundedRetry:
    def test_success_clears_counter(self):
        ch = _channel()
        ec._uid_failures["7"] = 2  # vorherige Fehlversuche
        async def ok(self, imap, uid):
            return None
        with patch.object(EmailChannel, "_process_uid_once", ok):
            run(ch._process_uid(MagicMock(), b"7"))
        assert "7" not in ec._uid_failures
        ch._quarantine_uid.assert_not_called()

    def test_transient_failure_reraises_and_counts(self):
        ch = _channel()
        async def boom(self, imap, uid):
            raise RuntimeError("LLM backend down")
        with patch.object(EmailChannel, "_process_uid_once", boom):
            with pytest.raises(RuntimeError):
                run(ch._process_uid(MagicMock(), b"7"))
        # Erster Fehlversuch: zählt, KEIN skip/quarantine
        assert ec._uid_failures["7"] == 1
        ch._quarantine_uid.assert_not_called()
        ch._update_checkpoint.assert_not_called()

    def test_quarantine_after_max_attempts(self):
        ch = _channel()
        imap = MagicMock()
        async def boom(self, imap, uid):
            raise ValueError("poison")
        with patch(
            "aifred.plugins.channels.email_channel.config.EMAIL_MAX_PROCESS_ATTEMPTS", 5
        ), patch.object(EmailChannel, "_process_uid_once", boom):
            # Versuche 1–4: re-raise
            for expected in range(1, 5):
                with pytest.raises(ValueError):
                    run(ch._process_uid(imap, b"7"))
                assert ec._uid_failures["7"] == expected
            # Versuch 5: quarantäne + skip, KEIN re-raise
            run(ch._process_uid(imap, b"7"))
        ch._quarantine_uid.assert_called_once_with(imap, b"7")
        ch._update_checkpoint.assert_called_once_with(b"7")
        assert "7" not in ec._uid_failures  # Zähler nach Skip zurückgesetzt

    def test_quarantine_uses_flagged_flag(self):
        # _quarantine_uid setzt \\Flagged via UID STORE (best effort).
        ch = EmailChannel()
        ch.channel_log = MagicMock()
        imap = MagicMock()
        ch._quarantine_uid(imap, b"42")
        imap.uid.assert_called_once()
        args = imap.uid.call_args.args
        assert args[0] == "STORE" and args[1] == b"42"
        assert args[2] == "+FLAGS" and "Flagged" in args[3]

    def test_quarantine_store_error_is_swallowed(self):
        # Ein fehlschlagender STORE darf die Schleife nicht crashen.
        ch = EmailChannel()
        ch.channel_log = MagicMock()
        imap = MagicMock()
        imap.uid.side_effect = OSError("STORE failed")
        ch._quarantine_uid(imap, b"42")  # darf NICHT werfen
        ch.channel_log.assert_called()
