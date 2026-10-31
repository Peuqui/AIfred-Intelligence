"""Tests für die Kopfzeilen eingehender Mails (RFC 2047).

Kontrakt von ``_decode_header``:
- Zwischen kodiertem und einfachem Text steht genau ein Leerzeichen, auch wenn
  ``decode_header`` es am Zeilenumbruch entfernt.
- Benachbarte kodierte Wörter schließen ohne Leerzeichen aneinander an, auch bei
  verschiedenen Zeichensätzen.
- Ein unbekannter Zeichensatz bricht nichts ab (eine kaputte Mail darf den
  Listener nicht stoppen).

Kontrakt von ``_decode_sender``:
- Die Adresse stammt aus der Rohzeile, entschlüsselt wird nur der Name. Was erst
  durchs Entschlüsseln wie eine erlaubte Adresse aussieht, kommt nicht durch.
- Eine Zeile ohne genau eine Adresse besteht weder Allowlist noch Eigentümer-Prüfung,
  auch nicht über einen Domain-Eintrag.
"""

from __future__ import annotations

import pytest

from aifred.lib.security import _is_owner, extract_sender_email
from aifred.plugins.channels.email_channel import _fetch_email_as_inbound, _is_sender_allowed
from aifred.plugins.channels.email_channel.client import _decode_header, _decode_sender


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


class TestDecodeSender:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, ""),
            ("", ""),
            ("hans@x.de", "hans@x.de"),
            ("Hans Meier <hans@x.de>", '"Hans Meier" <hans@x.de>'),
            ("=?utf-8?q?M=C3=BCller?= <hans@x.de>", '"Müller" <hans@x.de>'),
            ("=?utf-8?q?M=C3=BCller?=\r\n <hans@x.de>", '"Müller" <hans@x.de>'),
            ("=?utf-8?q?M=C3=BC?= =?iso-8859-1?q?ller?= <hans@x.de>", '"Müller" <hans@x.de>'),
            ("=?utf-8?q?a=22b?= <hans@x.de>", '"a\\"b" <hans@x.de>'),
        ],
    )
    def test_shape(self, raw: str | None, expected: str) -> None:
        assert _decode_sender(raw) == expected

    def test_header_without_single_address_stays_visible(self) -> None:
        raw = "a@x.de, b@x.de"
        assert _decode_sender(raw) == raw
        assert extract_sender_email(_decode_sender(raw)) == ""

    @pytest.mark.parametrize(
        "raw",
        [
            "=?utf-8?q?M=C3=BCller?= <hans@x.de>",
            "Hans =?utf-8?q?M=C3=BCller?= <hans@x.de>",
            "=?utf-8?q?M=C3=BCller?=\r\n <hans@x.de>",
            "=?utf-8?q?M=C3=BCller=2C_Hans?= <hans@x.de>",
            "=?utf-8?q?a=0D=0Ab?= <hans@x.de>",
        ],
    )
    def test_address_is_read_from_real_senders(self, raw: str) -> None:
        assert extract_sender_email(_decode_sender(raw)) == "hans@x.de"


SPOOFED_SENDERS = [
    "=?utf-8?q?owner=40x.de?=",
    "=?utf-8?q?owner=40x.de?= <attacker@evil.com>",
    "=?utf-8?q?owner=40x.de=2C?= <attacker@evil.com>",
    "=?utf-8?q?=3Cowner=40x.de=3E?= <attacker@evil.com>",
    "=?utf-8?q?=3Cowner=40x.de=3E?=<attacker@evil.com>",
    "=?utf-8?q?owner=40x.de_=28?= <attacker@evil.com>",
    "=?utf-8?q?owner=40x.de=0D=0Ax?= <attacker@evil.com>",
    "=?utf-8?q?owner=40x.de=0A?= <attacker@evil.com>",
    '"owner@x.de" <attacker@evil.com>',
    "<attacker@evil.com>, owner@x.de",
    "owner@x.de, <attacker@evil.com>",
]


class TestAllowlist:
    @pytest.mark.parametrize("entry", ["owner@x.de", "@x.de"])
    @pytest.mark.parametrize("raw", SPOOFED_SENDERS)
    def test_spoofed_sender_gets_nothing(
        self, raw: str, entry: str, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("EMAIL_ALLOWED_SENDERS", entry)
        sender = _decode_sender(raw)
        assert not _is_sender_allowed(sender)
        assert not _is_owner("email", sender, {"auth_results": "pass"})

    @pytest.mark.parametrize(
        "raw",
        [
            "owner@x.de",
            "OWNER@X.DE",
            "=?utf-8?q?M=C3=BCller?= <owner@x.de>",
            "=?utf-8?q?M=C3=BCller=2C_Hans?= <owner@x.de>",
            '"Meier, Hans" <owner@x.de>',
        ],
    )
    def test_real_owner_passes(self, raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EMAIL_ALLOWED_SENDERS", "owner@x.de")
        sender = _decode_sender(raw)
        assert _is_sender_allowed(sender)
        assert _is_owner("email", sender, {"auth_results": "pass"})

    def test_empty_first_entry_makes_nobody_owner(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EMAIL_ALLOWED_SENDERS", ", owner@x.de")
        assert not _is_owner("email", "a@x.de, b@x.de", {"auth_results": "pass"})


def test_listener_reads_sender_from_raw_header(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeImap:
        def uid(self, *_args: object) -> tuple[str, list[tuple[bytes, bytes]]]:
            mail = b"From: =?utf-8?q?owner=40x.de?=\r\nSubject: t\r\n\r\nbody\r\n"
            return "OK", [(b"1 (RFC822 {1}", mail)]

    monkeypatch.setenv("EMAIL_ALLOWED_SENDERS", "owner@x.de")
    inbound = _fetch_email_as_inbound(FakeImap(), b"1")  # type: ignore[arg-type]
    assert inbound is not None
    assert not _is_sender_allowed(inbound.sender)
