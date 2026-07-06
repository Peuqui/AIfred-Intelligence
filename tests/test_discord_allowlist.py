"""Tests für die Discord-Sender-Allowlist (TD8-Angleichung an Telegram).

Fail-closed: leer = niemand; explizite numerische IDs; die
"*"-Wildcard wird nicht mehr unterstützt (weltoffener Bot).
"""

from __future__ import annotations

import os
from unittest.mock import patch

from aifred.plugins.channels.discord_channel import _is_discord_user_allowed


class TestDiscordAllowlist:
    def test_empty_blocks_all(self):
        with patch.dict(os.environ, {"DISCORD_ALLOWED_USERS": ""}):
            assert _is_discord_user_allowed(123456) is False

    def test_star_wildcard_blocks_everyone(self):
        # TD8: gleiche Entscheidung wie Telegram — kein '*' mehr.
        with patch.dict(os.environ, {"DISCORD_ALLOWED_USERS": "*"}):
            assert _is_discord_user_allowed(123456) is False

    def test_specific_id_allowed(self):
        with patch.dict(os.environ, {"DISCORD_ALLOWED_USERS": "111, 222, 333"}):
            assert _is_discord_user_allowed(222) is True
            assert _is_discord_user_allowed(444) is False
