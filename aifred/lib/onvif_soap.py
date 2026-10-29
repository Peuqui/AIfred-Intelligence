"""ONVIF-SOAP-Grundbausteine (SOAP 1.2 + WS-Security UsernameToken).

Gemeinsam für den PTZ-Controller (synchron, ``requests``) und die ONVIF-
Ereignisquelle des Vigilantia-Watchers (asynchron, ``httpx``): Umschlag
bauen und Antwort prüfen. Der Transport bleibt bei den Aufrufern.

Credentials kommen aus dem ``CredentialBroker`` (``rtsp_<cred>``) — ONVIF und
RTSP teilen sich auf den üblichen Kameras dieselben Zugangsdaten. Sie tauchen
in keiner Fehlermeldung auf.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from xml.sax.saxutils import escape as xml_escape

from .credential_broker import broker

_WSSE = (
    "http://docs.oasis-open.org/wss/2004/01/"
    "oasis-200401-wss-wssecurity-secext-1.0.xsd"
)
_WSU = (
    "http://docs.oasis-open.org/wss/2004/01/"
    "oasis-200401-wss-wssecurity-utility-1.0.xsd"
)
_PW_DIGEST = (
    "http://docs.oasis-open.org/wss/2004/01/"
    "oasis-200401-wss-username-token-profile-1.0#PasswordDigest"
)
_B64_ENC = (
    "http://docs.oasis-open.org/wss/2004/01/"
    "oasis-200401-wss-soap-message-security-1.0#Base64Binary"
)
_WSA = "http://www.w3.org/2005/08/addressing"


class OnvifError(RuntimeError):
    """ONVIF-Anfrage fehlgeschlagen (HTTP-Fehler, SOAP-Fault oder unlesbare Antwort)."""


def local_name(tag: str) -> str:
    """Local name eines (ggf. namespaced) ElementTree-Tags."""
    return tag.rsplit("}", 1)[-1]


def security_header(cred: str) -> str:
    """WS-Security UsernameToken mit PasswordDigest (SHA1).

    Leerer String, wenn kein Credential hinterlegt ist — dann wird
    unauthentifiziert verbunden (offene Kamera)."""
    if not cred:
        return ""
    user = broker.get(f"rtsp_{cred}", "user")
    password = broker.get(f"rtsp_{cred}", "password")
    if not user and not password:
        return ""
    nonce = secrets.token_bytes(16)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + password.encode()).digest()
    ).decode()
    nonce_b64 = base64.b64encode(nonce).decode()
    return (
        f'<wsse:Security xmlns:wsse="{_WSSE}" xmlns:wsu="{_WSU}" '
        f's:mustUnderstand="1"><wsse:UsernameToken>'
        f"<wsse:Username>{xml_escape(user)}</wsse:Username>"
        f'<wsse:Password Type="{_PW_DIGEST}">{digest}</wsse:Password>'
        f'<wsse:Nonce EncodingType="{_B64_ENC}">{nonce_b64}</wsse:Nonce>'
        f"<wsu:Created>{created}</wsu:Created>"
        f"</wsse:UsernameToken></wsse:Security>"
    )


def envelope(
    body: str, *, cred: str, action: str | None = None, to: str | None = None,
) -> bytes:
    """SOAP-1.2-Umschlag mit Security-Header. ``action`` + ``to`` ergänzen die
    WS-Addressing-Header, die Operationen an einer Subscription (PullMessages,
    Renew) verlangen."""
    addressing = ""
    if action and to:
        addressing = (
            f'<wsa:Action xmlns:wsa="{_WSA}" s:mustUnderstand="1">'
            f"{xml_escape(action)}</wsa:Action>"
            f'<wsa:To xmlns:wsa="{_WSA}" s:mustUnderstand="1">'
            f"{xml_escape(to)}</wsa:To>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
        f"<s:Header>{addressing}{security_header(cred)}</s:Header>"
        f"<s:Body>{body}</s:Body></s:Envelope>"
    ).encode()


def content_type(action: str) -> str:
    return f'application/soap+xml; charset=utf-8; action="{action}"'


def parse_response(content: bytes, status_code: int, host: str) -> ET.Element:
    """Antwort parsen. Wirft ``OnvifError`` bei SOAP-Fault, HTTP-Fehler oder
    unlesbarem XML."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        raise OnvifError(f"ONVIF response from {host} not parseable: {e}") from e
    fault = next((el for el in root.iter() if local_name(el.tag) == "Fault"), None)
    if fault is not None:
        reason = next(
            (el for el in fault.iter() if local_name(el.tag) == "Reason"), fault,
        )
        raise OnvifError(f"ONVIF fault from {host}: {''.join(reason.itertext()).strip()}")
    if status_code >= 400:
        raise OnvifError(f"ONVIF HTTP {status_code} from {host}")
    return root
