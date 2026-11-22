"""Tests für die A9-Auswertung von Authentication-Results (E-Mail).

Kontrakt (SECURITY_FINDINGS.md A9):
- ``_parse_auth_results`` liefert "pass"/"fail"/"none" aus dem OBERSTEN
  Authentication-Results-Header (RFC 8601: der empfangende MX schreibt
  ihn zuoberst; tiefere Header können vom Absender gefälscht sein).
- Das Urteil gilt der Domain des From-Headers: dmarc=pass ist "pass", dmarc=fail
  ist "fail", egal was SPF oder DKIM sagen (der Server eines Angreifers besteht
  SPF für seine eigene Absender-Domain, während er den From-Header fälscht).
- Ohne DMARC-Urteil zählt dkim=pass oder spf=pass nur für die From-Domain oder
  eine Subdomain davon (header.d / smtp.mailfrom), sonst "fail".
- Fehlender Header → "none" (Provider ohne AR-Stempel; kein Fehler).
"""

from __future__ import annotations

import email as email_lib

import pytest

from aifred.plugins.channels.email_channel import _parse_auth_results


def _msg(*ar_headers: str, sender: str = "x@y.de"):
    raw = "".join(f"Authentication-Results: {h}\n" for h in ar_headers)
    raw += f"From: {sender}\nSubject: t\n\nbody\n"
    return email_lib.message_from_string(raw)


# Ein echter GMX-Stempel (2026-07-06), Absender und IP anonymisiert:
GMX_REAL = (
    "gmx.net; dkim=pass header.i=@mail.de header.s=mailde202009; "
    "spf=pass smtp.mailfrom=user@mail.de; "
    "dmarc=pass header.from=mail.de policy.dmarc=quarantine; "
    "iprev=pass policy.iprev=192.0.2.24"
)


class TestParseAuthResults:
    def test_real_gmx_header_passes(self):
        assert _parse_auth_results(_msg(GMX_REAL, sender="user@mail.de")) == "pass"

    def test_dmarc_pass(self):
        assert _parse_auth_results(_msg("mx.de; dmarc=pass header.from=mail.de")) == "pass"

    def test_dkim_pass_for_the_from_domain_without_dmarc(self):
        assert _parse_auth_results(_msg("mx.de; dkim=pass header.d=y.de; spf=none")) == "pass"

    def test_spf_pass_for_the_from_domain_without_dmarc(self):
        assert _parse_auth_results(_msg("mx.de; spf=pass smtp.mailfrom=bounce@y.de")) == "pass"

    def test_all_fail(self):
        assert _parse_auth_results(_msg("mx.de; dkim=fail; spf=fail; dmarc=fail")) == "fail"

    def test_softfail_is_not_pass(self):
        # spf=softfail darf NICHT als pass zählen (Wortgrenze).
        assert _parse_auth_results(_msg("mx.de; spf=softfail; dkim=none")) == "fail"

    def test_no_header_is_none(self):
        assert _parse_auth_results(_msg()) == "none"

    def test_only_top_header_counts(self):
        # Angreifer hängt einen gefälschten pass-Header UNTEN an; der
        # echte (oberste) MX-Header sagt fail → Ergebnis fail.
        m = _msg("mx.de; dmarc=fail", "evil.example; dmarc=pass")
        assert _parse_auth_results(m) == "fail"

    def test_case_insensitive(self):
        assert _parse_auth_results(_msg("MX.DE; DKIM=PASS header.d=Y.DE")) == "pass"


@pytest.mark.parametrize(("header", "verdict"), [
    # The forged owner: the attacker's own server passes SPF/DKIM for its own domain, DMARC fails for From.
    ("mx.de; spf=pass smtp.mailfrom=attacker.example; dmarc=fail header.from=y.de", "fail"),
    ("mx.de; dkim=pass header.d=attacker.example; dmarc=fail header.from=y.de", "fail"),
    # Without a DMARC verdict a pass for another domain says nothing about From.
    ("mx.de; spf=pass smtp.mailfrom=attacker.example", "fail"),
    ("mx.de; dkim=pass header.d=attacker.example; spf=softfail smtp.mailfrom=y.de", "fail"),
    ("mx.de; dkim=pass", "fail"),
    ("mx.de; spf=pass smtp.mailfrom=noty.de", "fail"),
    ("mx.de; dkim=pass header.d=mail.y.de", "pass"),
    ("mx.de; dmarc=none header.from=y.de; spf=pass smtp.mailfrom=y.de", "pass"),
])
def test_the_verdict_is_about_the_from_domain(header: str, verdict: str) -> None:
    assert _parse_auth_results(_msg(header)) == verdict
