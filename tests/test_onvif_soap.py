"""Tests für aifred.lib.onvif_soap — SOAP-Umschlag, Security-Header, Antwortprüfung.

Reine Unit-Tests ohne Netz; die Zugangsdaten kommen über einen gefakten
CredentialBroker.
"""

from __future__ import annotations

import base64
import hashlib
import xml.etree.ElementTree as ET

import pytest

from aifred.lib import onvif_soap
from aifred.lib.onvif_soap import (
    OnvifError,
    content_type,
    envelope,
    local_name,
    parse_response,
    security_header,
)

PASSWORD = "geheimes-passwort-123"


class _FakeBroker:
    def __init__(self, user: str = "admin", password: str = PASSWORD) -> None:
        self._values = {"user": user, "password": password}
        self.requested: list[tuple[str, str]] = []

    def get(self, service: str, key: str) -> str:
        self.requested.append((service, key))
        return self._values[key]


@pytest.fixture()
def fake_broker(monkeypatch) -> _FakeBroker:
    fake = _FakeBroker()
    monkeypatch.setattr(onvif_soap.broker, "get", fake.get)
    return fake


def _soap(body: str) -> bytes:
    return (
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
        f"<s:Body>{body}</s:Body></s:Envelope>"
    ).encode()


FAULT = (
    "<s:Fault><s:Code><s:Value>s:Sender</s:Value></s:Code>"
    "<s:Reason><s:Text xml:lang=\"en\">Sender not Authorized</s:Text></s:Reason></s:Fault>"
)


class TestLocalName:
    def test_strips_namespace(self):
        assert local_name("{http://x/y}Address") == "Address"

    def test_without_namespace(self):
        assert local_name("Address") == "Address"


class TestSecurityHeader:
    def test_empty_without_credential_name(self, fake_broker):
        assert security_header("") == ""
        assert fake_broker.requested == []

    def test_empty_when_broker_has_no_values(self, monkeypatch):
        monkeypatch.setattr(onvif_soap.broker, "get", lambda service, key: "")
        assert security_header("kamera") == ""

    def test_contains_token_fields(self, fake_broker):
        header = security_header("kamera")
        assert ("rtsp_kamera", "user") in fake_broker.requested
        assert ("rtsp_kamera", "password") in fake_broker.requested
        assert "<wsse:Username>admin</wsse:Username>" in header
        assert "PasswordDigest" in header
        assert "<wsse:Nonce" in header
        assert "<wsu:Created>" in header

    def test_password_not_in_clear(self, fake_broker):
        assert PASSWORD not in security_header("kamera")

    def test_digest_matches_nonce_created_password(self, fake_broker):
        root = ET.fromstring(security_header("kamera").replace(
            "<wsse:Security ", "<wsse:Security xmlns:s=\"urn:s\" ", 1,
        ))
        by_name = {local_name(el.tag): (el.text or "") for el in root.iter()}
        nonce = base64.b64decode(by_name["Nonce"])
        expected = base64.b64encode(
            hashlib.sha1(nonce + by_name["Created"].encode() + PASSWORD.encode()).digest()
        ).decode()
        assert by_name["Password"] == expected

    def test_nonce_differs_between_calls(self, fake_broker):
        assert security_header("kamera") != security_header("kamera")

    def test_username_is_escaped(self, monkeypatch):
        monkeypatch.setattr(
            onvif_soap.broker, "get",
            lambda service, key: "a<b&c" if key == "user" else "pw",
        )
        header = security_header("kamera")
        assert "a&lt;b&amp;c" in header
        assert "a<b&c" not in header


class TestEnvelope:
    def test_well_formed_with_body_and_no_credential(self):
        root = ET.fromstring(envelope("<Ping/>", cred=""))
        names = [local_name(el.tag) for el in root.iter()]
        assert names[:3] == ["Envelope", "Header", "Body"]
        assert "Ping" in names
        assert "Security" not in names
        assert "Action" not in names

    def test_addressing_only_when_action_and_to_set(self):
        for kwargs in ({"action": "a"}, {"to": "http://x"}, {}):
            root = ET.fromstring(envelope("<Ping/>", cred="", **kwargs))
            assert "Action" not in [local_name(el.tag) for el in root.iter()]

    def test_addressing_headers(self):
        root = ET.fromstring(
            envelope("<Ping/>", cred="", action="urn:act", to="http://h:8000/sub")
        )
        values = {local_name(el.tag): el.text for el in root.iter()}
        assert values["Action"] == "urn:act"
        assert values["To"] == "http://h:8000/sub"

    def test_special_characters_are_escaped(self):
        data = envelope(
            "<Ping/>", cred="", action="urn:a&b<c>", to="http://h/?x=1&y=2",
        )
        root = ET.fromstring(data)  # wäre bei unescaptem & bzw. < nicht parsebar
        values = {local_name(el.tag): el.text for el in root.iter()}
        assert values["Action"] == "urn:a&b<c>"
        assert values["To"] == "http://h/?x=1&y=2"

    def test_security_header_included_with_credential(self, fake_broker):
        root = ET.fromstring(envelope("<Ping/>", cred="kamera"))
        assert "UsernameToken" in [local_name(el.tag) for el in root.iter()]

    def test_returns_utf8_bytes(self):
        data = envelope("<Ping>Grüße</Ping>", cred="")
        assert isinstance(data, bytes)
        assert "Grüße" in data.decode("utf-8")


def test_content_type():
    assert content_type("urn:x") == 'application/soap+xml; charset=utf-8; action="urn:x"'


class TestParseResponse:
    def test_valid_response_returns_root(self):
        root = parse_response(_soap("<Ok/>"), 200, "cam")
        assert "Ok" in [local_name(el.tag) for el in root.iter()]

    def test_soap_fault_raises_with_reason(self):
        with pytest.raises(OnvifError) as exc:
            parse_response(_soap(FAULT), 400, "cam")
        assert "Sender not Authorized" in str(exc.value)
        assert "cam" in str(exc.value)

    def test_fault_with_http_200_still_raises(self):
        with pytest.raises(OnvifError):
            parse_response(_soap(FAULT), 200, "cam")

    def test_http_error_raises(self):
        with pytest.raises(OnvifError, match="HTTP 503"):
            parse_response(_soap("<Ok/>"), 503, "cam")

    def test_broken_xml_raises(self):
        with pytest.raises(OnvifError, match="not parseable"):
            parse_response(b"<s:Envelope><unclosed>", 200, "cam")

    def test_empty_body_raises(self):
        with pytest.raises(OnvifError):
            parse_response(b"", 500, "cam")

    def test_error_messages_never_contain_password(self, fake_broker):
        # Die Antwort der Kamera kann Anfrage-Inhalte nie enthalten; die
        # Fehlermeldung wird nur aus Host, Status und Reason gebaut.
        for content, status in (
            (_soap(FAULT), 401), (b"<kaputt", 200), (_soap("<Ok/>"), 500),
        ):
            with pytest.raises(OnvifError) as exc:
                parse_response(content, status, "cam")
            assert PASSWORD not in str(exc.value)
