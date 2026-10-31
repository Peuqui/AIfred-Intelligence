"""Tests für ``_decode_header`` (E-Mail-Kopfzeilen nach RFC 2047).

Kontrakt:
- Zwischen kodiertem und einfachem Text steht genau ein Leerzeichen, auch wenn
  ``decode_header`` es am Zeilenumbruch entfernt.
- Benachbarte kodierte Wörter schließen ohne Leerzeichen aneinander an, auch bei
  verschiedenen Zeichensätzen.
- Ein unbekannter Zeichensatz bricht nichts ab (eine kaputte Mail darf den
  Listener nicht stoppen).
- Der Absender bleibt für die Allowlist lesbar.
"""

from __future__ import annotations

import pytest

from aifred.lib.security import extract_sender_email
from aifred.plugins.channels.email_channel.client import _decode_header


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, ""),
        ("", ""),
        ("Hans Meier <hans@x.de>", "Hans Meier <hans@x.de>"),
        ("=?utf-8?q?M=C3=BCller?= <hans@x.de>", "Müller <hans@x.de>"),
        ("Hans =?utf-8?q?M=C3=BCller?= <hans@x.de>", "Hans Müller <hans@x.de>"),
        ("AW: =?utf-8?q?Gr=C3=BC=C3=9Fe?= aus Kiel", "AW: Grüße aus Kiel"),
        ("=?utf-8?q?M=C3=BCller?=\r\n <hans@x.de>", "Müller <hans@x.de>"),
        ("=?utf-8?q?M=C3=BC?= =?iso-8859-1?q?ller?= <hans@x.de>", "Müller <hans@x.de>"),
        ("=?utf-8?q?Gr=C3=BC?= =?utf-8?q?=C3=9Fe?=", "Grüße"),
    ],
)
def test_decode_header_spacing(raw: str | None, expected: str) -> None:
    assert _decode_header(raw) == expected


def test_unknown_charset_does_not_raise() -> None:
    assert _decode_header("=?nonsense?q?Hallo?= Welt") == "Hallo Welt"


@pytest.mark.parametrize(
    "raw",
    [
        "=?utf-8?q?M=C3=BCller?= <hans@x.de>",
        "Hans =?utf-8?q?M=C3=BCller?= <hans@x.de>",
        "=?utf-8?q?M=C3=BCller?=\r\n <hans@x.de>",
    ],
)
def test_sender_stays_readable_for_allowlist(raw: str) -> None:
    assert extract_sender_email(_decode_header(raw)) == "hans@x.de"
