"""Tests für aifred.lib.vision_edge_onvif.OnvifPullPointSource.

Die Kamera ist ein ``httpx.MockTransport``-Handler, der je nach SOAP-Operation
(CreatePullPointSubscription, PullMessages, Renew, Unsubscribe) antwortet.
Kein Netz, keine Hardware.
"""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from collections import deque
from typing import Any, Awaitable, Callable

import httpx
import pytest

import aifred.lib.vision_edge_onvif as onvif_module
from aifred.lib import onvif_soap
from aifred.lib.onvif_soap import local_name
from aifred.lib.vision_edge_onvif import OnvifPullPointSource

SUB_ADDRESS = "http://host:8000/onvif/PullSubManager?Idx=1"
_REAL_SLEEP = asyncio.sleep

_ENV = (
    '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
    'xmlns:wsa5="http://www.w3.org/2005/08/addressing" '
    'xmlns:tev="http://www.onvif.org/ver10/events/wsdl" '
    'xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2" '
    'xmlns:tt="http://www.onvif.org/ver10/schema">'
    "<s:Body>{}</s:Body></s:Envelope>"
)


def _notification(topic: str, item: str, value: str) -> str:
    return (
        "<wsnt:NotificationMessage>"
        f"<wsnt:Topic>tns1:{topic}</wsnt:Topic>"
        "<wsnt:Message><tt:Message><tt:Source/>"
        f'<tt:Data><tt:SimpleItem Name="{item}" Value="{value}"/></tt:Data>'
        "</tt:Message></wsnt:Message></wsnt:NotificationMessage>"
    )


def people(state: bool) -> str:
    return _notification(
        "RuleEngine/MyRuleDetector/PeopleDetect", "State", str(state).lower(),
    )


def vehicle(state: bool) -> str:
    return _notification(
        "RuleEngine/MyRuleDetector/VehicleDetect", "State", str(state).lower(),
    )


def dogcat(state: bool) -> str:
    return _notification(
        "RuleEngine/MyRuleDetector/DogCatDetect", "State", str(state).lower(),
    )


def motion(state: bool) -> str:
    return _notification(
        "RuleEngine/CellMotionDetector/Motion", "IsMotion", str(state).lower(),
    )


def _pull_response(*notifications: str) -> str:
    return _ENV.format(
        f"<tev:PullMessagesResponse>{''.join(notifications)}</tev:PullMessagesResponse>"
    )


class FakeCamera:
    """Antwortet auf ONVIF-Operationen; PullMessages liefert die Skript-Einträge
    der Reihe nach (str = Antwort, int = HTTP-Fehlerstatus), danach leere Antworten."""

    def __init__(self, pulls: list[str | int] | None = None) -> None:
        self.pulls: deque[str | int] = deque(pulls or [])
        self.requests: list[tuple[str, str, str]] = []  # (Operation, URL, Body)

    def count(self, operation: str) -> int:
        return sum(1 for op, _, _ in self.requests if op == operation)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        root = ET.fromstring(body)
        soap_body = next(el for el in root if local_name(el.tag) == "Body")
        operation = local_name(next(iter(soap_body)).tag)
        self.requests.append((operation, str(request.url), body))
        if operation == "CreatePullPointSubscription":
            return httpx.Response(200, text=_ENV.format(
                "<tev:CreatePullPointSubscriptionResponse><tev:SubscriptionReference>"
                f"<wsa5:Address>{SUB_ADDRESS}</wsa5:Address>"
                "</tev:SubscriptionReference></tev:CreatePullPointSubscriptionResponse>"
            ))
        if operation == "PullMessages":
            if self.pulls:
                item = self.pulls.popleft()
                if isinstance(item, int):
                    return httpx.Response(item)
                return httpx.Response(200, text=item)
            await _REAL_SLEEP(0.01)  # echte Kamera hält die Anfrage offen
            return httpx.Response(200, text=_pull_response())
        return httpx.Response(200, text=_ENV.format(f"<wsnt:{operation}Response/>"))


def _make_source(camera: FakeCamera, cred: str = "") -> OnvifPullPointSource:
    src = OnvifPullPointSource("cam/test", "host", 8000, cred)
    src._http = httpx.AsyncClient(transport=httpx.MockTransport(camera))
    return src


def _run(camera: FakeCamera, scenario: Callable[[OnvifPullPointSource], Awaitable[Any]],
         cred: str = "") -> Any:
    async def go() -> Any:
        src = _make_source(camera, cred)
        try:
            return await asyncio.wait_for(scenario(src), timeout=10.0)
        finally:
            await src.aclose()

    return asyncio.run(go())


async def _next(src: OnvifPullPointSource) -> list[str]:
    return await asyncio.wait_for(src.wait_rising(), timeout=5.0)


async def _no_rising(src: OnvifPullPointSource, camera: FakeCamera, pulls: int) -> None:
    """Wartet bis ``pulls`` weitere Pulls verarbeitet sind und prüft, dass keine
    Flanke gemeldet wurde."""
    target = camera.count("PullMessages") + pulls
    while camera.count("PullMessages") < target:
        await asyncio.sleep(0.01)
    assert src._rising.empty()


class TestRisingEdges:
    def test_person_rising_edge(self):
        camera = FakeCamera([_pull_response(people(True))])

        async def scenario(src):
            assert await _next(src) == ["person"]
            assert await src.is_active("person") is True
            assert await src.is_active("vehicle") is False

        _run(camera, scenario)
        first = camera.requests[0]
        assert first[0] == "CreatePullPointSubscription"
        assert first[1] == "http://host:8000/onvif/event_service"
        pull = next(r for r in camera.requests if r[0] == "PullMessages")
        assert pull[1] == SUB_ADDRESS

    def test_state_false_deactivates_without_trigger(self):
        camera = FakeCamera([
            _pull_response(people(True)), _pull_response(people(False)),
        ])

        async def scenario(src):
            assert await _next(src) == ["person"]
            while await src.is_active("person"):
                await asyncio.sleep(0.01)
            await _no_rising(src, camera, 3)

        _run(camera, scenario)

    def test_steady_true_does_not_retrigger(self):
        camera = FakeCamera([
            _pull_response(people(True)), _pull_response(people(True)),
            _pull_response(people(True)),
        ])

        async def scenario(src):
            assert await _next(src) == ["person"]
            await _no_rising(src, camera, 5)
            assert await src.is_active("person") is True

        _run(camera, scenario)

    def test_new_edge_after_falling(self):
        camera = FakeCamera([_pull_response(people(True))])

        async def scenario(src):
            assert await _next(src) == ["person"]
            camera.pulls.append(_pull_response(people(False)))
            while await src.is_active("person"):
                await asyncio.sleep(0.01)
            camera.pulls.append(_pull_response(people(True)))
            assert await _next(src) == ["person"]

        _run(camera, scenario)

    def test_motion_and_unknown_topics_are_ignored(self):
        camera = FakeCamera([
            _pull_response(
                motion(True),
                _notification("RuleEngine/MyRuleDetector/FaceDetect", "State", "true"),
                _notification("VideoSource/GlobalSceneChange/ImagingService", "State", "true"),
            ),
        ])

        async def scenario(src):
            task = asyncio.create_task(src.wait_rising())
            await asyncio.sleep(0.2)
            assert not task.done()
            assert not await src.is_active("person")
            assert src._active == {}
            task.cancel()

        _run(camera, scenario)

    def test_notification_without_state_is_ignored(self):
        camera = FakeCamera([
            _pull_response(_notification(
                "RuleEngine/MyRuleDetector/PeopleDetect", "Other", "true",
            )),
        ])

        async def scenario(src):
            task = asyncio.create_task(src.wait_rising())
            await asyncio.sleep(0.2)
            assert not task.done()
            task.cancel()

        _run(camera, scenario)

    def test_simultaneous_classes_in_one_list(self):
        camera = FakeCamera([_pull_response(people(True), vehicle(True))])

        async def scenario(src):
            assert sorted(await _next(src)) == ["person", "vehicle"]

        _run(camera, scenario)

    def test_classes_from_separate_pulls_are_merged_when_queued_together(self):
        async def scenario(src):
            src._rising.put_nowait("person")
            src._rising.put_nowait("vehicle")
            src._rising.put_nowait("person")
            # Vorbelegte Schleife läuft nebenher gegen die Kamera; hier zählt nur
            # das Zusammenfassen ohne Doppelte.
            return await _next(src)

        result = _run(FakeCamera(), scenario)
        assert result == ["person", "vehicle"]

    def test_dogcat_maps_to_animal(self):
        camera = FakeCamera([_pull_response(dogcat(True))])

        async def scenario(src):
            assert await _next(src) == ["animal"]
            assert await src.is_active("animal") is True

        _run(camera, scenario)


class TestErrorsAndResubscribe:
    def test_pull_error_warns_resubscribes_and_recovers(self, monkeypatch, caplog):
        caplog.set_level(logging.WARNING, logger="aifred.lib.vision_edge_onvif")
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            # Nur die Backoff-Pause des Moduls wird verkürzt; die Dauer selbst
            # wird mitgeschrieben und unten gegen die Vorgabe geprüft.
            sleeps.append(seconds)
            await _REAL_SLEEP(0)

        camera = FakeCamera([500, _pull_response(people(True))])
        monkeypatch.setattr(onvif_module.asyncio, "sleep", fake_sleep)

        async def scenario(src):
            return await _next(src)

        assert _run(camera, scenario) == ["person"]
        assert camera.count("CreatePullPointSubscription") == 2
        assert sleeps and sleeps[0] == 2.0
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("edge-ai onvif failed for cam/test" in m and "#1" in m for m in warnings)

    def test_backoff_grows_while_pulls_keep_failing(self, monkeypatch):
        # Erst ein gelungener Pull setzt den Fehlerzähler zurück: scheitert der
        # Pull nach jedem erfolgreichen Neuabonnieren, wächst das Backoff.
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            await _REAL_SLEEP(0)

        camera = FakeCamera([500, 500, 500, _pull_response(people(True))])
        monkeypatch.setattr(onvif_module.asyncio, "sleep", fake_sleep)

        async def scenario(src):
            return await _next(src)

        assert _run(camera, scenario) == ["person"]
        assert sleeps[:3] == [2.0, 4.0, 8.0]

    def test_soap_fault_in_pull_is_failure(self, monkeypatch):
        fault = _ENV.format(
            "<s:Fault><s:Reason><s:Text>Nicht erlaubt</s:Text></s:Reason></s:Fault>"
        )

        async def fake_sleep(seconds: float) -> None:
            await _REAL_SLEEP(0)

        camera = FakeCamera([fault, _pull_response(people(True))])
        monkeypatch.setattr(onvif_module.asyncio, "sleep", fake_sleep)

        async def scenario(src):
            return await _next(src)

        assert _run(camera, scenario) == ["person"]
        assert camera.count("CreatePullPointSubscription") == 2

    def test_resubscribe_discards_own_state(self, monkeypatch):
        async def fake_sleep(seconds: float) -> None:
            await _REAL_SLEEP(0)

        camera = FakeCamera([_pull_response(people(True)), 500, _pull_response(people(True))])
        monkeypatch.setattr(onvif_module.asyncio, "sleep", fake_sleep)

        async def scenario(src):
            assert await _next(src) == ["person"]
            # Nach dem Neuaufbau meldet die Kamera den Anfangszustand erneut →
            # das ist wieder eine steigende Flanke.
            assert await _next(src) == ["person"]

        _run(camera, scenario)
        assert camera.count("CreatePullPointSubscription") == 2

    def test_subscription_without_address_is_failure(self, monkeypatch):
        async def fake_sleep(seconds: float) -> None:
            await _REAL_SLEEP(0)

        monkeypatch.setattr(onvif_module.asyncio, "sleep", fake_sleep)
        calls = {"n": 0}
        camera = FakeCamera([_pull_response(people(True))])

        async def handler(request: httpx.Request) -> httpx.Response:
            if b"CreatePullPointSubscription" in request.content and calls["n"] == 0:
                calls["n"] += 1
                return httpx.Response(200, text=_ENV.format(
                    "<tev:CreatePullPointSubscriptionResponse/>"
                ))
            return await camera(request)

        async def go():
            src = OnvifPullPointSource("cam/test", "host", 8000, "")
            src._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            try:
                return await asyncio.wait_for(_next(src), timeout=5.0)
            finally:
                await src.aclose()

        assert asyncio.run(go()) == ["person"]
        assert camera.count("CreatePullPointSubscription") == 1

    def test_connection_error_is_failure(self, monkeypatch, caplog):
        caplog.set_level(logging.WARNING, logger="aifred.lib.vision_edge_onvif")

        async def fake_sleep(seconds: float) -> None:
            await _REAL_SLEEP(0)

        monkeypatch.setattr(onvif_module.asyncio, "sleep", fake_sleep)
        camera = FakeCamera([_pull_response(people(True))])
        failed = {"done": False}

        async def handler(request: httpx.Request) -> httpx.Response:
            if not failed["done"]:
                failed["done"] = True
                raise httpx.ConnectError("Kamera nicht erreichbar")
            return await camera(request)

        async def go():
            src = OnvifPullPointSource("cam/test", "host", 8000, "")
            src._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            try:
                return await asyncio.wait_for(_next(src), timeout=5.0)
            finally:
                await src.aclose()

        assert asyncio.run(go()) == ["person"]
        assert any("nicht erreichbar" in r.getMessage() for r in caplog.records)


class TestLifecycle:
    def test_aclose_sends_unsubscribe_to_subscription_address(self):
        camera = FakeCamera([_pull_response(people(True))])

        async def scenario(src):
            assert await _next(src) == ["person"]

        _run(camera, scenario)
        assert camera.count("Unsubscribe") == 1
        operation, url, body = next(r for r in camera.requests if r[0] == "Unsubscribe")
        assert url == SUB_ADDRESS
        assert SUB_ADDRESS in body  # wsa:To

    def test_aclose_closes_http_client_and_stops_task(self):
        camera = FakeCamera([_pull_response(people(True))])
        holder: dict[str, OnvifPullPointSource] = {}

        async def scenario(src):
            holder["src"] = src
            await _next(src)

        _run(camera, scenario)
        src = holder["src"]
        assert src._http.is_closed
        assert src._task is not None and src._task.done()
        count = camera.count("PullMessages")
        asyncio.run(asyncio.sleep(0.05))
        assert camera.count("PullMessages") == count

    def test_aclose_without_start_sends_nothing(self):
        camera = FakeCamera()

        async def go():
            src = _make_source(camera)
            await src.aclose()

        asyncio.run(go())
        assert camera.requests == []

    def test_renew_sent_after_renew_interval(self, monkeypatch):
        monkeypatch.setattr(onvif_module, "VISION_ONVIF_RENEW_SEC", 0.05)
        camera = FakeCamera()

        async def scenario(src):
            task = asyncio.create_task(src.wait_rising())
            while camera.count("Renew") < 2:
                await asyncio.sleep(0.01)
            task.cancel()

        _run(camera, scenario)
        renew = next(r for r in camera.requests if r[0] == "Renew")
        assert renew[1] == SUB_ADDRESS
        assert "TerminationTime" in renew[2]
        assert camera.count("CreatePullPointSubscription") == 1

    def test_no_renew_before_interval(self):
        camera = FakeCamera([_pull_response(people(True))])

        async def scenario(src):
            await _next(src)

        _run(camera, scenario)
        assert camera.count("Renew") == 0

    def test_finished_pull_loop_raises_instead_of_hanging(self):
        async def scenario(src):
            async def boom() -> None:
                raise ValueError("kaputt")

            src._task = asyncio.create_task(boom())
            await asyncio.sleep(0.01)
            with pytest.raises(ValueError, match="kaputt"):
                await src.wait_rising()
            src._task = None

        _run(FakeCamera(), scenario)


class TestAuthentication:
    def test_security_header_sent_with_credential(self, monkeypatch):
        monkeypatch.setattr(
            onvif_soap.broker, "get",
            lambda service, key: "admin" if key == "user" else "pw-geheim",
        )
        camera = FakeCamera([_pull_response(people(True))])

        async def scenario(src):
            await _next(src)

        _run(camera, scenario, cred="kamera")
        assert all("UsernameToken" in body for _, _, body in camera.requests)
        assert all("pw-geheim" not in body for _, _, body in camera.requests)

    def test_no_security_header_without_credential(self):
        camera = FakeCamera([_pull_response(people(True))])

        async def scenario(src):
            await _next(src)

        _run(camera, scenario)
        assert all("UsernameToken" not in body for _, _, body in camera.requests)
