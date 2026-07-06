"""Tests für die A9-Auswertung von Authentication-Results (E-Mail).

Kontrakt (SECURITY_FINDINGS.md A9):
- ``_parse_auth_results`` liefert "pass"/"fail"/"none" aus dem OBERSTEN
  Authentication-Results-Header (RFC 8601: der empfangende MX schreibt
  ihn zuoberst; tiefere Header koennen vom Absender gefaelscht sein).
- "pass" nur bei dmarc=pass ODER dkim=pass ODER spf=pass.
- Fehlender Header → "none" (Provider ohne AR-Stempel; kein Fehler).
"""

from __future__ import annotations

import email as email_lib

from aifred.plugins.channels.email_channel import _parse_auth_results


def _msg(*ar_headers: str):
    raw = "".join(f"Authentication-Results: {h}\n" for h in ar_headers)
    raw += "From: x@y.de\nSubject: t\n\nbody\n"
    return email_lib.message_from_string(raw)


# Der echte GMX-Stempel der Testmail von markus.peuckert@mail.de (2026-07-06):
GMX_REAL = (
    "gmx.net; dkim=pass header.i=@mail.de header.s=mailde202009; "
    "spf=pass smtp.mailfrom=markus.peuckert@mail.de; "
    "dmarc=pass header.from=mail.de policy.dmarc=quarantine; "
    "iprev=pass policy.iprev=62.201.172.24"
)


class TestParseAuthResults:
    def test_real_gmx_header_passes(self):
        assert _parse_auth_results(_msg(GMX_REAL)) == "pass"

    def test_dmarc_pass(self):
        assert _parse_auth_results(_msg("mx.de; dmarc=pass header.from=mail.de")) == "pass"

    def test_dkim_pass_without_dmarc(self):
        assert _parse_auth_results(_msg("mx.de; dkim=pass; spf=none")) == "pass"

    def test_spf_pass_without_dmarc(self):
        assert _parse_auth_results(_msg("mx.de; spf=pass smtp.mailfrom=a@b.de")) == "pass"

    def test_all_fail(self):
        assert _parse_auth_results(_msg("mx.de; dkim=fail; spf=fail; dmarc=fail")) == "fail"

    def test_softfail_is_not_pass(self):
        # spf=softfail darf NICHT als pass zaehlen (Wortgrenze).
        assert _parse_auth_results(_msg("mx.de; spf=softfail; dkim=none")) == "fail"

    def test_no_header_is_none(self):
        assert _parse_auth_results(_msg()) == "none"

    def test_only_top_header_counts(self):
        # Angreifer haengt einen gefaelschten pass-Header UNTEN an; der
        # echte (oberste) MX-Header sagt fail → Ergebnis fail.
        m = _msg("mx.de; dmarc=fail", "evil.example; dmarc=pass")
        assert _parse_auth_results(m) == "fail"

    def test_case_insensitive(self):
        assert _parse_auth_results(_msg("MX.DE; DKIM=PASS")) == "pass"
