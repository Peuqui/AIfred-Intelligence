"""ONVIF-PTZ-Steuerung für IP-Kameras (Pan/Tilt/Zoom).

Minimaler ONVIF-Client über rohes SOAP (``requests``) — bewusst ohne
externe ONVIF-Bibliothek, damit keine zusätzliche Abhängigkeit nötig ist.
Deckt die für eine „AIfred steuert den Kamerakopf"-Logik nötigen
Operationen ab: ContinuousMove, Stop, AbsoluteMove, GotoPreset.

Konfiguriert wird PTZ über dieselben Kamera-Einträge wie die RTSP-Quellen
(``rtsp_cameras`` in ``plugins/tools/vision/settings.json``); zusätzlich::

    {"name": "TrackMix", "host": "192.168.1.50", ...,
     "cred": "trackmix", "ptz": true, "onvif_port": 8000}

* ``ptz``         schaltet PTZ-Steuerung für die Kamera frei (Default aus)
* ``onvif_port``  ONVIF-Service-Port (Default 8000 — Reolink-Standard)

Credentials kommen wie bei der RTSP-Quelle aus dem ``CredentialBroker``
(``rtsp_<cred>`` → ``RTSP_<CRED>_USER`` / ``RTSP_<CRED>_PASSWORD``). ONVIF
und RTSP teilen sich auf den üblichen Kameras dieselben Zugangsdaten.

Status: erste Implementierung, real noch ungetestet (keine PTZ-Kamera zur
Hand). Velocity-Konventionen: Pan/Tilt/Zoom je in [-1.0, 1.0].
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests

from .credential_broker import broker

logger = logging.getLogger(__name__)

_VISION_SETTINGS = (
    Path(__file__).resolve().parents[1] / "plugins/tools/vision/settings.json"
)

_DEFAULT_ONVIF_PORT = 8000
_SERVICE_PATH = "/onvif/device_service"
_HTTP_TIMEOUT_S = 5.0

# ONVIF-Namespaces
_NS_MEDIA = "http://www.onvif.org/ver10/media/wsdl"
_NS_PTZ = "http://www.onvif.org/ver20/ptz/wsdl"
_NS_SCHEMA = "http://www.onvif.org/ver10/schema"
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


class PTZError(RuntimeError):
    """ONVIF-Anfrage fehlgeschlagen (HTTP-Fehler oder SOAP-Fault)."""


def _clamp(v: float) -> float:
    return max(-1.0, min(1.0, v))


def _local(tag: str) -> str:
    """Local name eines (ggf. namespaced) ElementTree-Tags."""
    return tag.rsplit("}", 1)[-1]


class PTZController:
    """Steuert eine ONVIF-fähige PTZ-Kamera über rohes SOAP.

    Profile-Token wird beim ersten Zug lazy geladen und gecacht. Alle
    Methoden sind synchron (blockierend) — aus async-Kontext via
    ``asyncio.to_thread()`` aufrufen, wie bei der RTSP-Quelle.
    """

    def __init__(self, host: str, onvif_port: int, cred: str) -> None:
        self.host = host
        self.onvif_port = onvif_port
        self._cred = cred
        self._endpoint = f"http://{host}:{onvif_port}{_SERVICE_PATH}"
        self._token: str | None = None

    # ── SOAP-Infrastruktur ──────────────────────────────────────────

    def _security_header(self) -> str:
        """WS-Security UsernameToken mit PasswordDigest (SHA1).

        Liefert leeren String, wenn kein Credential hinterlegt ist — dann
        wird unauthentifiziert verbunden (offene Kamera)."""
        if not self._cred:
            return ""
        user = broker.get(f"rtsp_{self._cred}", "user")
        password = broker.get(f"rtsp_{self._cred}", "password")
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
            f"<wsse:Username>{user}</wsse:Username>"
            f'<wsse:Password Type="{_PW_DIGEST}">{digest}</wsse:Password>'
            f'<wsse:Nonce EncodingType="{_B64_ENC}">{nonce_b64}</wsse:Nonce>'
            f"<wsu:Created>{created}</wsu:Created>"
            f"</wsse:UsernameToken></wsse:Security>"
        )

    def _post(self, action: str, body: str) -> ET.Element:
        """Sende einen SOAP-1.2-Request und parse die Antwort.

        Wirft ``PTZError`` bei HTTP-Fehler oder SOAP-Fault. Credentials
        tauchen in keiner Fehlermeldung auf."""
        envelope = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
            f"<s:Header>{self._security_header()}</s:Header>"
            f"<s:Body>{body}</s:Body></s:Envelope>"
        )
        headers = {
            "Content-Type": f'application/soap+xml; charset=utf-8; action="{action}"'
        }
        try:
            resp = requests.post(
                self._endpoint,
                data=envelope.encode("utf-8"),
                headers=headers,
                timeout=_HTTP_TIMEOUT_S,
            )
        except requests.RequestException as e:
            raise PTZError(f"ONVIF request to {self.host} failed: {e}") from e
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as e:
            raise PTZError(f"ONVIF response from {self.host} not parseable: {e}") from e
        fault = next(
            (el for el in root.iter() if _local(el.tag) == "Reason"), None
        )
        if fault is not None:
            raise PTZError(f"ONVIF fault from {self.host}: {''.join(fault.itertext()).strip()}")
        if resp.status_code >= 400:
            raise PTZError(f"ONVIF HTTP {resp.status_code} from {self.host}")
        return root

    def profile_token(self) -> str:
        """Erstes Media-Profile-Token holen (gecacht)."""
        if self._token is not None:
            return self._token
        body = f'<GetProfiles xmlns="{_NS_MEDIA}"/>'
        root = self._post(f"{_NS_MEDIA}/GetProfiles", body)
        token = next(
            (
                el.get("token")
                for el in root.iter()
                if _local(el.tag) == "Profiles" and el.get("token")
            ),
            None,
        )
        if not token:
            raise PTZError(f"No media profile found on {self.host}")
        self._token = token
        return token

    # ── PTZ-Operationen ─────────────────────────────────────────────

    def continuous_move(self, pan: float, tilt: float, zoom: float = 0.0) -> None:
        """Bewegung mit gegebener Geschwindigkeit starten (läuft bis ``stop``).

        ``pan``/``tilt``/``zoom`` je in [-1.0, 1.0]."""
        token = self.profile_token()
        pan, tilt, zoom = _clamp(pan), _clamp(tilt), _clamp(zoom)
        body = (
            f'<ContinuousMove xmlns="{_NS_PTZ}" xmlns:tt="{_NS_SCHEMA}">'
            f"<ProfileToken>{token}</ProfileToken><Velocity>"
            f'<tt:PanTilt x="{pan}" y="{tilt}"/><tt:Zoom x="{zoom}"/>'
            f"</Velocity></ContinuousMove>"
        )
        self._post(f"{_NS_PTZ}/ContinuousMove", body)

    def stop(self, pan_tilt: bool = True, zoom: bool = True) -> None:
        """Laufende Bewegung anhalten."""
        token = self.profile_token()
        body = (
            f'<Stop xmlns="{_NS_PTZ}"><ProfileToken>{token}</ProfileToken>'
            f"<PanTilt>{str(pan_tilt).lower()}</PanTilt>"
            f"<Zoom>{str(zoom).lower()}</Zoom></Stop>"
        )
        self._post(f"{_NS_PTZ}/Stop", body)

    def absolute_move(self, pan: float, tilt: float, zoom: float = 0.0) -> None:
        """Auf absolute Pan/Tilt/Zoom-Position fahren (je in [-1.0, 1.0])."""
        token = self.profile_token()
        pan, tilt, zoom = _clamp(pan), _clamp(tilt), _clamp(zoom)
        body = (
            f'<AbsoluteMove xmlns="{_NS_PTZ}" xmlns:tt="{_NS_SCHEMA}">'
            f"<ProfileToken>{token}</ProfileToken><Position>"
            f'<tt:PanTilt x="{pan}" y="{tilt}"/><tt:Zoom x="{zoom}"/>'
            f"</Position></AbsoluteMove>"
        )
        self._post(f"{_NS_PTZ}/AbsoluteMove", body)

    def goto_preset(self, preset_token: str) -> None:
        """Gespeicherte Preset-Position anfahren."""
        token = self.profile_token()
        body = (
            f'<GotoPreset xmlns="{_NS_PTZ}"><ProfileToken>{token}</ProfileToken>'
            f"<PresetToken>{preset_token}</PresetToken></GotoPreset>"
        )
        self._post(f"{_NS_PTZ}/GotoPreset", body)

    def nudge(
        self, pan: float, tilt: float, zoom: float = 0.0, duration: float = 0.4
    ) -> None:
        """Kurzer Bewegungs-Stoß: ContinuousMove → warten → Stop.

        Praktisch für „ein Stück nach links/oben" ohne separate Stop-Logik
        im Aufrufer."""
        self.continuous_move(pan, tilt, zoom)
        time.sleep(max(0.0, duration))
        self.stop()

    def aim_at_offset(
        self,
        dx: float,
        dy: float,
        *,
        deadzone: float = 0.1,
        speed: float = 0.5,
        duration: float = 0.4,
    ) -> None:
        """Kopf in Richtung eines Ziel-Offsets vom Bildzentrum schwenken.

        ``dx``/``dy`` sind normierte Offsets in [-1, 1] (0 = Zentrum). Das
        ist der grobe „folge der Bounding-Box"-Primitiv für AIfred: liegt
        das Ziel innerhalb der Deadzone, passiert nichts; sonst ein
        proportionaler Stoß. Bild-Y zeigt nach unten, Tilt nach oben →
        Vorzeichen von ``dy`` wird invertiert. Bewusst simpel/ungenau —
        echte Pixel→Winkel-Kalibrierung ist eine spätere Ausbaustufe."""
        pan = 0.0 if abs(dx) < deadzone else _clamp(dx) * speed
        tilt = 0.0 if abs(dy) < deadzone else -_clamp(dy) * speed
        if pan == 0.0 and tilt == 0.0:
            return
        self.nudge(pan, tilt, duration=duration)


def _slug(name: str) -> str:
    keep = "".join(c if c.isalnum() else "_" for c in name.strip().lower())
    return keep.strip("_") or "cam"


def controllers_from_settings() -> dict[str, PTZController]:
    """Baue PTZ-Controller für alle Kameras mit ``ptz: true``.

    Schlüssel ist die ``source_id`` (``cam/rtsp_<slug>``), gleich wie bei
    der RTSP-Quelle — so findet ein Aufrufer zur Video-Quelle den
    passenden Controller. Leeres Dict, wenn Datei/Key fehlt oder keine
    Kamera PTZ aktiviert hat.
    """
    try:
        data = json.loads(_VISION_SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.debug("ptz: vision settings not readable (%s)", e)
        return {}
    cams = data.get("rtsp_cameras")
    if not isinstance(cams, list):
        return {}
    result: dict[str, PTZController] = {}
    for c in cams:
        if not isinstance(c, dict) or not c.get("ptz"):
            continue
        name = str(c.get("name", "")).strip()
        host = str(c.get("host", "")).strip()
        if not name or not host:
            continue
        try:
            port = int(c.get("onvif_port", _DEFAULT_ONVIF_PORT))
        except (TypeError, ValueError):
            port = _DEFAULT_ONVIF_PORT
        source_id = f"cam/rtsp_{_slug(name)}"
        result[source_id] = PTZController(
            host=host, onvif_port=port, cred=str(c.get("cred", "")).strip()
        )
        logger.info("Registered PTZ controller: %s (%s)", source_id, name)
    return result
