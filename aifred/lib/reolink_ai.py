"""Reolink Edge-AI-Client — liest die On-Device-Erkennung der Kamera.

Reolink-Kameras (z.B. TrackMix) erkennen Person / Fahrzeug / Tier on-device,
ohne Cloud. Dieser Client fragt diese Erkennung über die lokale CGI-API ab
(``GetAiState``) — so kann AIfred die Roh-Detektion an die Kamera auslagern
statt selbst MOG2/YOLO auf den Stream zu rechnen.

API-Fluss (alles lokal, HTTP):

1. ``Login`` → Token (mit ``leaseTime``, wird gecacht und vor Ablauf erneuert)
2. ``GetAiState`` → ``alarm_state``/``support`` je Klasse (people/vehicle/dog_cat)

Credentials laufen über denselben ``CredentialBroker`` wie die RTSP-Quelle:
Service ``rtsp_<cred>`` → ``RTSP_<CRED>_USER`` / ``RTSP_<CRED>_PASSWORD`` aus
der ``.env``. Kein Secret landet in der settings.json oder im Log; die URL
wird nur lokal beim Request gebaut.

Logische Klassennamen (für AIfred einheitlich) — gemappt aus der Reolink-API:

* ``person``  ← ``people``
* ``vehicle`` ← ``vehicle``
* ``animal``  ← ``dog_cat``

``face`` ignoriert der Client bewusst: die Kamera erkennt kein *Wer* — die
Gesichts-Identität bleibt AIfreds Aufgabe (InsightFace).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from .credential_broker import broker

logger = logging.getLogger(__name__)

# Reolink-API-Klasse → AIfred-Eventtyp. ``face`` fehlt absichtlich.
_CLASS_MAP = {
    "people": "person",
    "vehicle": "vehicle",
    "dog_cat": "animal",
}

# Sicherheitsmarge: Token einige Sekunden vor Ablauf erneuern.
_TOKEN_RENEW_MARGIN_S = 30.0
# Kurzer HTTP-Timeout — der Poll soll bei einer hängenden Kamera nicht
# die ganze Watch-Schleife blockieren.
_HTTP_TIMEOUT_S = 5.0


class ReolinkAIError(RuntimeError):
    """Edge-AI-Abfrage fehlgeschlagen (Login/Request/Antwort-Format)."""


class ReolinkAIClient:
    """Liest die On-Device-Erkennung einer Reolink-Kamera per CGI-API.

    Ein Client pro Kamera-Quelle; hält einen persistenten HTTP-Client und
    einen gecachten Token. ``aclose()`` am Ende der Watch-Session aufrufen.
    """

    def __init__(
        self,
        host: str,
        *,
        api_port: int = 443,
        cred: str = "",
        channel: int = 0,
    ) -> None:
        self._host = host
        self._api_port = api_port
        self._cred = cred
        self._channel = channel
        # Reolink leitet HTTP→HTTPS um und nutzt ein self-signed Cert →
        # HTTPS mit deaktivierter Zertifikatsprüfung (lokale IP-Kamera, kein
        # öffentlich vertrauenswürdiges Cert möglich).
        self._base = f"https://{host}:{api_port}/cgi-bin/api.cgi"
        self._client: httpx.AsyncClient | None = None
        self._token: str = ""
        self._token_expires_at: float = 0.0

    # ── Public API ────────────────────────────────────────────────

    async def get_ai_state(self) -> dict[str, bool]:
        """Aktuelle Erkennung der Kamera als ``{logische_klasse: alarm}``.

        Nur Klassen mit ``support=1`` tauchen auf (Kamera-abhängig). Wirft
        :class:`ReolinkAIError` bei Login-/Request-/Format-Fehlern — der
        Caller entscheidet, ob er das toleriert (Best-Effort-Poll)."""
        token = await self._ensure_token()
        payload = [{
            "cmd": "GetAiState",
            "action": 0,
            "param": {"channel": self._channel},
        }]
        data = await self._post("GetAiState", payload, token=token)
        value = data.get("value") or {}
        result: dict[str, bool] = {}
        for api_key, logical in _CLASS_MAP.items():
            entry = value.get(api_key)
            if not isinstance(entry, dict):
                continue
            if int(entry.get("support", 0)) != 1:
                continue
            result[logical] = int(entry.get("alarm_state", 0)) > 0
        return result

    async def snap(self, channel: int | None = None) -> bytes:
        """Ein frisches Standbild eines Kanals als JPEG-Bytes (``cmd=Snap``).

        Für die Dual-Lens-TrackMix: Kanal 0 = Weitwinkel, Kanal 1 = Zoom/Tele.
        Holt das Bild direkt von der Kamera (nicht aus dem gepufferten
        RTSP-Stream) — geringere Latenz, volle Lens-Auflösung. Wirft
        :class:`ReolinkAIError` bei Fehler/leerer Antwort."""
        token = await self._ensure_token()
        ch = self._channel if channel is None else channel
        params: dict[str, Any] = {
            "cmd": "Snap", "channel": ch, "rs": "aifred", "token": token,
        }
        try:
            resp = await self._http().get(self._base, params=params)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise ReolinkAIError(f"Snap ch{ch} to {self._host} failed: {e}") from e
        ct = resp.headers.get("content-type", "")
        if "image" not in ct or len(resp.content) < 1000:
            # Kein Bild → Token evtl. abgelaufen, beim nächsten Call erneuern.
            self._token = ""
            self._token_expires_at = 0.0
            raise ReolinkAIError(
                f"Snap ch{ch} from {self._host}: no image (ct={ct!r})"
            )
        return resp.content

    async def aclose(self) -> None:
        """HTTP-Client schließen. Idempotent."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── Internals ─────────────────────────────────────────────────

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            # verify=False: self-signed Cert der lokalen Kamera (kein
            # öffentlich vertrauenswürdiges Cert möglich, reines LAN).
            self._client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S, verify=False)
        return self._client

    async def _ensure_token(self) -> str:
        """Gecachten Token liefern oder per Login neu holen."""
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        return await self._login()

    async def _login(self) -> str:
        user = broker.get(f"rtsp_{self._cred}", "user") if self._cred else ""
        password = broker.get(f"rtsp_{self._cred}", "password") if self._cred else ""
        payload = [{
            "cmd": "Login",
            "param": {"User": {"userName": user, "password": password}},
        }]
        data = await self._post("Login", payload, token=None)
        token_obj = (data.get("value") or {}).get("Token") or {}
        name = str(token_obj.get("name") or "").strip()
        if not name:
            raise ReolinkAIError(f"login returned no token for {self._host}")
        lease = float(token_obj.get("leaseTime", 3600) or 3600)
        self._token = name
        self._token_expires_at = time.monotonic() + max(0.0, lease - _TOKEN_RENEW_MARGIN_S)
        return name

    async def _post(
        self, cmd: str, payload: list[dict[str, Any]], *, token: str | None
    ) -> dict[str, Any]:
        """Einen CGI-Befehl absetzen und den ersten Antwort-Block (mit
        ``code==0``) zurückgeben. Wirft :class:`ReolinkAIError` sonst."""
        params = {"cmd": cmd}
        if token:
            params["token"] = token
        try:
            resp = await self._http().post(self._base, params=params, json=payload)
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ReolinkAIError(f"{cmd} request to {self._host} failed: {e}") from e
        if not isinstance(body, list) or not body:
            raise ReolinkAIError(f"{cmd}: unexpected response shape from {self._host}")
        block: dict[str, Any] = body[0]
        code = int(block.get("code", -1))
        if code != 0:
            # Token könnte abgelaufen sein → beim nächsten Call erneuern.
            self._token = ""
            self._token_expires_at = 0.0
            detail = (block.get("error") or {}).get("detail", "")
            raise ReolinkAIError(f"{cmd} returned code={code} {detail}".strip())
        return block
