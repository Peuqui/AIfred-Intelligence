"""Tests für das Telegram-/clear-Message-Log (TD6).

Die Bot-API kann keine Chat-Historie auflisten — /clear kann nur
Message-IDs löschen, die das Plugin selbst protokolliert hat. Das Log
ist eine JSON-Datei (pro Chat eine Liste, Cap _MSGLOG_CAP), take()
liest und leert atomar unter Lock.
"""

from __future__ import annotations

from unittest.mock import patch

import aifred.plugins.channels.telegram_channel as tg


def _with_tmp_log(tmp_path):
    return patch.object(tg, "_MSGLOG_FILE", tmp_path / "msglog.json")


class TestMsglog:
    def test_add_and_take(self, tmp_path):
        with _with_tmp_log(tmp_path):
            tg._msglog_add("123", 1, 2, 3)
            tg._msglog_add(123, 4)  # int/str chat_id normalisiert auf str
            assert tg._msglog_take("123") == [1, 2, 3, 4]
            # take leert den Eintrag
            assert tg._msglog_take("123") == []

    def test_chats_are_isolated(self, tmp_path):
        with _with_tmp_log(tmp_path):
            tg._msglog_add("a", 1)
            tg._msglog_add("b", 2)
            assert tg._msglog_take("a") == [1]
            assert tg._msglog_take("b") == [2]

    def test_cap_keeps_newest(self, tmp_path):
        with _with_tmp_log(tmp_path):
            with patch.object(tg, "_MSGLOG_CAP", 5):
                tg._msglog_add("c", *range(1, 11))  # 1..10, Cap 5
                assert tg._msglog_take("c") == [6, 7, 8, 9, 10]

    def test_corrupt_file_returns_empty(self, tmp_path):
        with _with_tmp_log(tmp_path):
            (tmp_path / "msglog.json").write_text("{kaputt")
            assert tg._msglog_take("x") == []
            # und ist danach wieder beschreibbar
            tg._msglog_add("x", 7)
            assert tg._msglog_take("x") == [7]

    def test_empty_add_is_noop(self, tmp_path):
        with _with_tmp_log(tmp_path):
            tg._msglog_add("y")  # keine ids
            assert not (tmp_path / "msglog.json").exists()
