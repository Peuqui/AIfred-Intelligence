"""ONVIF-Ereignisquelle der Kamera-Erkennungen (PullPoint-Abonnement).

Statt die Kamera alle 0,5 s nach ihrem Zustand zu fragen, abonniert AIfred
ihre Ereignisse einmal und wartet auf Meldungen: ``PeopleDetect``,
``VehicleDetect`` und ``DogCatDetect`` kommen mit Zustand an/aus, ohne
Dauerlast auf dem Kamera-Webserver. Die reine Pixel-Bewegung der Kamera
(``CellMotionDetector``) wird bewusst ignoriert — sie kam in der Messung nicht
vor der Person und löst auch bei Laub und Schatten aus.

Das Abonnement hat eine Laufzeit und wird erneuert; bricht die Verbindung ab,
meldet die Quelle eine Warnung und baut es mit Backoff neu auf (kein stiller
Wechsel auf ein anderes Verfahren).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import xml.etree.ElementTree as ET

import httpx

from .config import (
    VISION_EDGE_AI_BACKOFF_MAX_DOUBLINGS,
    VISION_EDGE_AI_BACKOFF_MAX_SEC,
    VISION_ONVIF_LEASE_SEC,
    VISION_ONVIF_PULL_TIMEOUT_SEC,
    VISION_ONVIF_RENEW_SEC,
)
from .onvif_soap import OnvifError, content_type, envelope, local_name, parse_response

logger = logging.getLogger(__name__)

_NS_EVENTS = "http://www.onvif.org/ver10/events/wsdl"
_NS_WSNT = "http://docs.oasis-open.org/wsn/b-2"
_ACTION_CREATE = f"{_NS_EVENTS}/EventPortType/CreatePullPointSubscriptionRequest"
_ACTION_PULL = f"{_NS_EVENTS}/PullPointSubscription/PullMessagesRequest"
_ACTION_RENEW = "http://docs.oasis-open.org/wsn/bw-2/SubscriptionManager/RenewRequest"
_ACTION_UNSUBSCRIBE = (
    "http://docs.oasis-open.org/wsn/bw-2/SubscriptionManager/UnsubscribeRequest"
)
_EVENT_SERVICE_PATH = "/onvif/event_service"
_PULL_MESSAGE_LIMIT = 50

# Reolink-Thema (Ende des ONVIF-Topics) → AIfred-Klasse.
TOPIC_CLASS = {
    "RuleEngine/MyRuleDetector/PeopleDetect": "person",
    "RuleEngine/MyRuleDetector/VehicleDetect": "vehicle",
    "RuleEngine/MyRuleDetector/DogCatDetect": "animal",
}


class OnvifPullPointSource:
    """``EdgeAiEventSource`` über ein ONVIF-PullPoint-Abonnement."""

    def __init__(
        self, source_id: str, host: str, onvif_port: int, cred: str,
    ) -> None:
        self._source_id = source_id
        self._host = host
        self._cred = cred
        self._endpoint = f"http://{host}:{onvif_port}{_EVENT_SERVICE_PATH}"
        self._http = httpx.AsyncClient(
            timeout=VISION_ONVIF_PULL_TIMEOUT_SEC + 5,
        )
        self._active: dict[str, bool] = {}
        self._rising: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._address = ""

    # ── EdgeAiEventSource ─────────────────────────────────────────

    async def wait_rising(self) -> list[str]:
        if self._task is None:
            self._task = asyncio.create_task(
                self._run(), name=f"vision-onvif:{self._source_id}",
            )
        elif self._task.done():
            # Die Pull-Schleife darf nur durch Abbruch enden; ein anderer Grund
            # ist ein Programmfehler und wird sichtbar.
            self._task.result()
            raise RuntimeError(f"ONVIF-Pull-Schleife von {self._source_id} beendet")
        rising = [await self._rising.get()]
        while not self._rising.empty():
            cls = self._rising.get_nowait()
            if cls not in rising:
                rising.append(cls)
        return rising

    async def is_active(self, cls: str) -> bool:
        return self._active.get(cls, False)

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._address:
            try:
                await self._post(
                    self._address, f'<Unsubscribe xmlns="{_NS_WSNT}"/>',
                    _ACTION_UNSUBSCRIBE, subscription=True,
                )
            except OnvifError as e:
                logger.warning(
                    "edge-ai onvif unsubscribe failed for %s: %s", self._source_id, e,
                )
        await self._http.aclose()

    # ── Abonnement ────────────────────────────────────────────────

    async def _post(
        self, url: str, body: str, action: str, *, subscription: bool,
    ) -> ET.Element:
        envelope_bytes = envelope(
            body, cred=self._cred,
            action=action if subscription else None,
            to=url if subscription else None,
        )
        try:
            resp = await self._http.post(
                url, content=envelope_bytes,
                headers={"Content-Type": content_type(action)},
            )
        except httpx.HTTPError as e:
            raise OnvifError(f"ONVIF request to {self._host} failed: {e}") from e
        return parse_response(resp.content, resp.status_code, self._host)

    async def _subscribe(self) -> str:
        """PullPoint-Abonnement anlegen; Returnt die Adresse des Abonnements."""
        body = (
            f'<CreatePullPointSubscription xmlns="{_NS_EVENTS}">'
            f"<InitialTerminationTime>PT{VISION_ONVIF_LEASE_SEC}S</InitialTerminationTime>"
            f"</CreatePullPointSubscription>"
        )
        root = await self._post(self._endpoint, body, _ACTION_CREATE, subscription=False)
        address = next(
            (
                (el.text or "").strip()
                for el in root.iter()
                if local_name(el.tag) == "Address" and (el.text or "").strip()
            ),
            "",
        )
        if not address:
            raise OnvifError(f"ONVIF subscription of {self._host} has no address")
        return address

    async def _renew(self, address: str) -> None:
        body = (
            f'<Renew xmlns="{_NS_WSNT}">'
            f"<TerminationTime>PT{VISION_ONVIF_LEASE_SEC}S</TerminationTime></Renew>"
        )
        await self._post(address, body, _ACTION_RENEW, subscription=True)

    async def _pull(self, address: str) -> None:
        body = (
            f'<PullMessages xmlns="{_NS_EVENTS}">'
            f"<Timeout>PT{VISION_ONVIF_PULL_TIMEOUT_SEC}S</Timeout>"
            f"<MessageLimit>{_PULL_MESSAGE_LIMIT}</MessageLimit></PullMessages>"
        )
        root = await self._post(address, body, _ACTION_PULL, subscription=True)
        for message in root.iter():
            if local_name(message.tag) == "NotificationMessage":
                self._apply(message)

    def _apply(self, message: ET.Element) -> None:
        """Eine Benachrichtigung auswerten: Klasse und Zustand merken, eine
        steigende Flanke in die Warteschlange legen."""
        topic = ""
        state: bool | None = None
        for el in message.iter():
            name = local_name(el.tag)
            if name == "Topic":
                topic = (el.text or "").strip()
            elif name == "SimpleItem" and el.get("Name") == "State":
                state = el.get("Value") == "true"
        cls = next(
            (c for suffix, c in TOPIC_CLASS.items() if topic.endswith(suffix)), None,
        )
        if cls is None or state is None:
            return
        was_active = self._active.get(cls, False)
        self._active[cls] = state
        if state and not was_active:
            self._rising.put_nowait(cls)

    async def _run(self) -> None:
        fails = 0
        while True:
            try:
                address = await self._subscribe()
                self._address = address
                renewed_at = time.monotonic()
                # Neues Abonnement: der Kameraseite ist nur ihr Anfangszustand
                # bekannt (kommt als erste Meldung) — den eigenen verwerfen.
                self._active.clear()
                while True:
                    await self._pull(address)
                    # Erst ein gelungener Pull beweist, dass das Abonnement
                    # trägt; ein Neuaufbau, der sofort wieder scheitert,
                    # lässt das Backoff weiter wachsen.
                    fails = 0
                    if time.monotonic() - renewed_at >= VISION_ONVIF_RENEW_SEC:
                        await self._renew(address)
                        renewed_at = time.monotonic()
            except OnvifError as e:
                fails += 1
                backoff = min(
                    2 ** min(fails, VISION_EDGE_AI_BACKOFF_MAX_DOUBLINGS),
                    VISION_EDGE_AI_BACKOFF_MAX_SEC,
                )
                logger.warning(
                    "edge-ai onvif failed for %s (#%d, resubscribe in %.0fs): %s",
                    self._source_id, fails, backoff, e,
                )
                await asyncio.sleep(backoff)

