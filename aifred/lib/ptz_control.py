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

Bedient wird der Kopf aus der Live-Ansicht über den ``PtzDriver`` (gedrückt
halten fährt, Loslassen hält an, Sicherheitsabschaltung gegen weiterfahren).
Velocity-Konventionen: Pan/Tilt/Zoom je in [-1.0, 1.0].
"""

from __future__ import annotations

import asyncio
import logging
import time
import xml.etree.ElementTree as ET

import requests

from . import vision_snap
from .config import (
    VISION_PTZ_DEADMAN_SEC,
    VISION_PTZ_DEFAULT_SPEED,
    VISION_PTZ_MIN_MOVE_SEC,
    VISION_PTZ_SPEEDS,
)
from .frame_sources.rtsp_source import find_camera_config
from .logging_utils import log_message
from .reolink_ai import ReolinkAIError
from .onvif_soap import (
    OnvifError,
    content_type,
    envelope,
    local_name,
    parse_response,
    xml_escape,
)

logger = logging.getLogger(__name__)

_DEFAULT_ONVIF_PORT = 8000
_SERVICE_PATH = "/onvif/device_service"
_HTTP_TIMEOUT_S = 5.0

# ONVIF-Namespaces
_NS_MEDIA = "http://www.onvif.org/ver10/media/wsdl"
_NS_PTZ = "http://www.onvif.org/ver20/ptz/wsdl"
_NS_SCHEMA = "http://www.onvif.org/ver10/schema"


class PTZError(OnvifError):
    """ONVIF-Anfrage fehlgeschlagen (HTTP-Fehler oder SOAP-Fault)."""


def _clamp(v: float) -> float:
    return max(-1.0, min(1.0, v))


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

    def _post(self, action: str, body: str) -> ET.Element:
        """Sende einen SOAP-1.2-Request und parse die Antwort.

        Wirft ``PTZError`` bei HTTP-Fehler oder SOAP-Fault. Credentials
        tauchen in keiner Fehlermeldung auf."""
        try:
            resp = requests.post(
                self._endpoint,
                data=envelope(body, cred=self._cred),
                headers={"Content-Type": content_type(action)},
                timeout=_HTTP_TIMEOUT_S,
            )
        except requests.RequestException as e:
            raise PTZError(f"ONVIF request to {self.host} failed: {e}") from e
        try:
            return parse_response(resp.content, resp.status_code, self.host)
        except OnvifError as e:
            raise PTZError(str(e)) from e

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
                if local_name(el.tag) == "Profiles" and el.get("token")
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
            f"<PresetToken>{xml_escape(preset_token)}</PresetToken></GotoPreset>"
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


# Richtung → (Pan, Tilt, Zoom) als Vorzeichen; ONVIF: Pan + = rechts,
# Tilt + = hoch, Zoom + = hinein.
DIRECTIONS: dict[str, tuple[float, float, float]] = {
    "left": (-1.0, 0.0, 0.0),
    "right": (1.0, 0.0, 0.0),
    "up": (0.0, 1.0, 0.0),
    "down": (0.0, -1.0, 0.0),
    "zoom_in": (0.0, 0.0, 1.0),
    "zoom_out": (0.0, 0.0, -1.0),
}

# Der Zoommotor der Kamera fährt pro ONVIF-Befehl bei kleiner Geschwindigkeit nur
# EINEN Schritt (gemessen: 100 Einheiten) und bleibt dann stehen, egal wie lange
# der Befehl dauert. Für „gehalten = zoomt weiter" wird der Befehl im Herzschlag
# der gehaltenen Taste wiederholt.
REPEATING_DIRECTIONS = frozenset({"zoom_in", "zoom_out"})

_controllers: dict[str, tuple[tuple[str, int, str], PTZController]] = {}


def get_controller(source_id: str) -> PTZController | None:
    """PTZ-Controller der Quelle (``rtsp_cameras``-Eintrag mit ``ptz: true``),
    sonst ``None``. Der Controller wird pro Quelle gehalten (das Profil-Token
    wird nur einmal geholt) und neu gebaut, wenn sich Host/Port/Zugang ändern.
    Eine unlesbare Einstellungsdatei wirft ``ValueError``."""
    cam = find_camera_config(source_id)
    if not cam or not cam.get("ptz"):
        _controllers.pop(source_id, None)
        return None
    key = (
        str(cam.get("host", "")).strip(),
        int(cam.get("onvif_port", _DEFAULT_ONVIF_PORT)),
        str(cam.get("cred", "")).strip(),
    )
    cached = _controllers.get(source_id)
    if cached is None or cached[0] != key:
        cached = (key, PTZController(host=key[0], onvif_port=key[1], cred=key[2]))
        _controllers[source_id] = cached
        logger.info("Registered PTZ controller: %s", source_id)
    return cached[1]


class PtzDriver:
    """Asynchrone Bedienung des Kamerakopfs für die Live-Ansicht: gedrückt
    halten fährt, Loslassen hält an.

    Zwei Absicherungen gegen einen weiterfahrenden Kopf: eine Bewegung dauert
    mindestens ``VISION_PTZ_MIN_MOVE_SEC`` (ein Tipp fährt ein Stück statt
    nichts) und endet spätestens ``VISION_PTZ_DEADMAN_SEC`` nach dem letzten
    Lebenszeichen (Tastendruck bzw. Herzschlag der gehaltenen Taste), auch wenn
    das Loslassen nie ankommt."""

    def __init__(self) -> None:
        self._pressed_at: dict[str, float] = {}
        self._deadman: dict[str, asyncio.TimerHandle] = {}
        self._moving: dict[str, tuple[str, str]] = {}  # Quelle → (Richtung, Stufe)

    async def press(
        self, source_id: str, direction: str, speed: str = VISION_PTZ_DEFAULT_SPEED,
    ) -> None:
        """Fahren in ``direction`` mit der Geschwindigkeitsstufe ``speed``
        (``VISION_PTZ_SPEEDS``)."""
        vector = DIRECTIONS[direction]
        factor = VISION_PTZ_SPEEDS[speed]
        controller = get_controller(source_id)
        if controller is None:
            raise PTZError(f"{source_id} hat keine PTZ-Steuerung (rtsp_cameras: ptz)")
        await asyncio.to_thread(
            controller.continuous_move,
            *(v * factor for v in vector),
        )
        self._pressed_at[source_id] = time.monotonic()
        self._moving[source_id] = (direction, speed)
        log_message(f"PTZ press {source_id} {direction} speed={speed}")
        self._arm_deadman(source_id)

    async def keep_alive(self, source_id: str) -> None:
        """Herzschlag der gehaltenen Taste: verlängert die Frist der
        Totmann-Abschaltung und wiederholt bei Zoom den Fahrbefehl (siehe
        ``REPEATING_DIRECTIONS``). Ohne laufende Bewegung ohne Wirkung."""
        if source_id not in self._pressed_at:
            return
        self._arm_deadman(source_id)
        direction, speed = self._moving[source_id]
        if direction in REPEATING_DIRECTIONS:
            controller = get_controller(source_id)
            if controller is not None:
                factor = VISION_PTZ_SPEEDS[speed]
                await asyncio.to_thread(
                    controller.continuous_move,
                    *(v * factor for v in DIRECTIONS[direction]),
                )

    def _arm_deadman(self, source_id: str) -> None:
        previous = self._deadman.pop(source_id, None)
        if previous is not None:
            previous.cancel()
        loop = asyncio.get_running_loop()
        self._deadman[source_id] = loop.call_later(
            VISION_PTZ_DEADMAN_SEC,
            lambda: asyncio.ensure_future(self._deadman_stop(source_id)),
        )

    async def release(self, source_id: str) -> None:
        """Loslassen: anhalten, aber nur, wenn gerade eine Bewegung läuft (das
        Verlassen einer Taste ohne Drücken ist kein Stopp-Befehl)."""
        pressed_at = self._pressed_at.get(source_id)
        if pressed_at is None:
            return
        remaining = VISION_PTZ_MIN_MOVE_SEC - (time.monotonic() - pressed_at)
        if remaining > 0:
            await asyncio.sleep(remaining)
        await self.stop(source_id)

    async def home(self, source_id: str) -> None:
        """Ausgangsposition anfahren: die Wächterposition der Kamera (eine laufende
        Bewegung endet damit)."""
        self._pressed_at.pop(source_id, None)
        self._moving.pop(source_id, None)
        timer = self._deadman.pop(source_id, None)
        if timer is not None:
            timer.cancel()
        cam = find_camera_config(source_id)
        if not cam or not cam.get("ptz"):
            raise PTZError(f"{source_id} hat keine PTZ-Steuerung (rtsp_cameras: ptz)")
        client = vision_snap.get_client(source_id)
        if client is None:
            raise PTZError(f"{source_id}: keine Kamera-Zugangsdaten (rtsp_cameras: cred)")
        try:
            await client.ptz_goto_guard(int(cam.get("channel", 0)))
        except ReolinkAIError as e:
            raise PTZError(str(e)) from e
        log_message(f"PTZ home (Wächterposition) {source_id}")

    async def stop(self, source_id: str) -> None:
        """Sofort anhalten (auch ohne laufende Bewegung)."""
        self._pressed_at.pop(source_id, None)
        self._moving.pop(source_id, None)
        timer = self._deadman.pop(source_id, None)
        if timer is not None:
            timer.cancel()
        controller = get_controller(source_id)
        if controller is not None:
            await asyncio.to_thread(controller.stop)
            log_message(f"PTZ stop {source_id}")

    async def _deadman_stop(self, source_id: str) -> None:
        self._deadman.pop(source_id, None)
        try:
            await self.stop(source_id)
        except PTZError:
            logger.exception("PTZ-Sicherheitsstopp für %s fehlgeschlagen", source_id)


ptz_driver = PtzDriver()
