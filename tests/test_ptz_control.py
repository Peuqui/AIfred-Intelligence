"""Tests für aifred.lib.ptz_control — Controller-Registry und PtzDriver
(gedrückt halten fährt, Loslassen hält an, Sicherheitsabschaltung)."""

from __future__ import annotations

import asyncio

import pytest

from aifred.lib import ptz_control
from aifred.lib.ptz_control import DIRECTIONS, PTZError, PtzDriver, get_controller
from aifred.lib.reolink_ai import ReolinkAIError

SOURCE = "cam/rtsp_test"


class FakeController:
    """Hält die Aufrufe fest, ohne SOAP zu sprechen."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def continuous_move(self, pan: float, tilt: float, zoom: float = 0.0) -> None:
        self.calls.append(("move", pan, tilt, zoom))

    def stop(self) -> None:
        self.calls.append(("stop",))


class FakeGuardClient:
    """Kamera-Client-Attrappe: merkt sich die Aufrufe der Wächterposition."""

    def __init__(self, error: Exception | None = None) -> None:
        self.guard_calls: list[int] = []
        self.error = error

    async def ptz_goto_guard(self, channel: int) -> None:
        if self.error is not None:
            raise self.error
        self.guard_calls.append(channel)


@pytest.fixture(autouse=True)
def _clean_registry():
    ptz_control._controllers.clear()
    yield
    ptz_control._controllers.clear()


@pytest.fixture
def camera(monkeypatch):
    """``find_camera_config`` liefert einen PTZ-Eintrag; die Konfiguration ist
    zur Laufzeit änderbar."""
    config = {"name": "Test", "host": "10.0.0.5", "onvif_port": 8000,
              "cred": "test", "ptz": True}
    monkeypatch.setattr(ptz_control, "find_camera_config", lambda source_id: config)
    return config


@pytest.fixture
def fake(monkeypatch):
    controller = FakeController()
    monkeypatch.setattr(ptz_control, "get_controller", lambda source_id: controller)
    return controller


class TestRegistry:
    def test_no_camera_config_gives_none(self, monkeypatch):
        monkeypatch.setattr(ptz_control, "find_camera_config", lambda source_id: None)
        assert get_controller(SOURCE) is None

    def test_camera_without_ptz_flag_gives_none(self, monkeypatch):
        monkeypatch.setattr(
            ptz_control, "find_camera_config",
            lambda source_id: {"name": "X", "host": "h", "ptz": False},
        )
        assert get_controller(SOURCE) is None

    def test_controller_is_cached_per_source(self, camera):
        assert get_controller(SOURCE) is get_controller(SOURCE)

    def test_controller_rebuilt_when_target_changes(self, camera):
        first = get_controller(SOURCE)
        camera["host"] = "10.0.0.6"
        second = get_controller(SOURCE)
        assert second is not first
        assert second.host == "10.0.0.6"

    def test_default_onvif_port(self, monkeypatch):
        monkeypatch.setattr(
            ptz_control, "find_camera_config",
            lambda source_id: {"name": "X", "host": "h", "ptz": True},
        )
        assert get_controller(SOURCE).onvif_port == 8000


class TestDirections:
    def test_signs_follow_onvif_convention(self):
        assert DIRECTIONS["left"][0] < 0 < DIRECTIONS["right"][0]
        assert DIRECTIONS["down"][1] < 0 < DIRECTIONS["up"][1]
        assert DIRECTIONS["zoom_out"][2] < 0 < DIRECTIONS["zoom_in"][2]


class TestDriver:
    def test_press_moves_with_configured_speed(self, fake):
        async def scenario():
            driver = PtzDriver()
            await driver.press(SOURCE, "right")
            driver._deadman[SOURCE].cancel()
        asyncio.run(scenario())
        assert fake.calls == [
            ("move", ptz_control.VISION_PTZ_SPEEDS[ptz_control.VISION_PTZ_DEFAULT_SPEED], 0.0, 0.0)
        ]

    def test_speed_levels_scale_the_velocity(self, fake):
        async def scenario():
            driver = PtzDriver()
            await driver.press(SOURCE, "up", "fast")
            driver._deadman[SOURCE].cancel()
        asyncio.run(scenario())
        assert fake.calls == [("move", 0.0, ptz_control.VISION_PTZ_SPEEDS["fast"], 0.0)]

    def test_unknown_speed_level_is_an_error(self, fake):
        with pytest.raises(KeyError):
            asyncio.run(PtzDriver().press(SOURCE, "up", "warp"))

    def test_keep_alive_extends_the_deadman_only_while_moving(self, fake, monkeypatch):
        monkeypatch.setattr(ptz_control, "VISION_PTZ_DEADMAN_SEC", 0.3)

        async def scenario():
            driver = PtzDriver()
            await driver.keep_alive(SOURCE)  # ohne Bewegung ohne Wirkung
            assert SOURCE not in driver._deadman
            await driver.press(SOURCE, "left")
            for _ in range(4):
                await asyncio.sleep(0.15)
                await driver.keep_alive(SOURCE)
            # 0,6 s nach dem Druck, aber der Herzschlag hielt die Frist offen:
            assert [c[0] for c in fake.calls] == ["move"]
            await asyncio.sleep(0.5)
        asyncio.run(scenario())
        assert [c[0] for c in fake.calls] == ["move", "stop"]

    def test_unknown_direction_is_an_error(self, fake):
        with pytest.raises(KeyError):
            asyncio.run(PtzDriver().press(SOURCE, "sideways"))

    def test_press_on_camera_without_ptz_raises(self, monkeypatch):
        monkeypatch.setattr(ptz_control, "get_controller", lambda source_id: None)
        with pytest.raises(PTZError):
            asyncio.run(PtzDriver().press(SOURCE, "left"))

    def test_release_without_press_sends_nothing(self, fake):
        asyncio.run(PtzDriver().release(SOURCE))
        assert fake.calls == []

    def test_release_stops_after_press(self, fake, monkeypatch):
        monkeypatch.setattr(ptz_control, "VISION_PTZ_MIN_MOVE_SEC", 0.0)

        async def scenario():
            driver = PtzDriver()
            await driver.press(SOURCE, "up")
            await driver.release(SOURCE)
            assert SOURCE not in driver._deadman
        asyncio.run(scenario())
        assert [c[0] for c in fake.calls] == ["move", "stop"]

    def test_short_tap_still_moves_for_the_minimum_time(self, fake, monkeypatch):
        monkeypatch.setattr(ptz_control, "VISION_PTZ_MIN_MOVE_SEC", 0.2)

        async def scenario():
            driver = PtzDriver()
            loop = asyncio.get_running_loop()
            await driver.press(SOURCE, "left")
            started = loop.time()
            await driver.release(SOURCE)
            return loop.time() - started
        waited = asyncio.run(scenario())
        assert waited >= 0.15
        assert [c[0] for c in fake.calls] == ["move", "stop"]

    def test_deadman_stops_the_head_when_release_never_comes(self, fake, monkeypatch):
        monkeypatch.setattr(ptz_control, "VISION_PTZ_DEADMAN_SEC", 0.1)

        async def scenario():
            driver = PtzDriver()
            await driver.press(SOURCE, "down")
            await asyncio.sleep(0.4)
        asyncio.run(scenario())
        assert [c[0] for c in fake.calls] == ["move", "stop"]

    def test_second_press_rearms_the_deadman(self, fake, monkeypatch):
        monkeypatch.setattr(ptz_control, "VISION_PTZ_DEADMAN_SEC", 0.3)

        async def scenario():
            driver = PtzDriver()
            await driver.press(SOURCE, "left")
            await asyncio.sleep(0.2)
            await driver.press(SOURCE, "left")
            await asyncio.sleep(0.2)
            # 0,4 s nach dem ersten, aber nur 0,2 s nach dem zweiten Druck:
            assert [c[0] for c in fake.calls] == ["move", "move"]
            await asyncio.sleep(0.3)
        asyncio.run(scenario())
        assert [c[0] for c in fake.calls] == ["move", "move", "stop"]

    def test_stop_works_without_press_and_cancels_deadman(self, fake):
        async def scenario():
            driver = PtzDriver()
            await driver.press(SOURCE, "up")
            await driver.stop(SOURCE)
            assert SOURCE not in driver._deadman
            assert SOURCE not in driver._pressed_at
            await driver.stop(SOURCE)  # ohne laufende Bewegung ebenfalls ok
        asyncio.run(scenario())
        assert [c[0] for c in fake.calls] == ["move", "stop", "stop"]

    def test_home_goes_to_the_guard_position_and_ends_a_running_move(self, fake, monkeypatch):
        client = FakeGuardClient()
        monkeypatch.setattr(ptz_control, "find_camera_config", lambda source_id: {"ptz": True, "channel": 0})
        monkeypatch.setattr(ptz_control.vision_snap, "get_client", lambda source_id: client)

        async def scenario():
            driver = PtzDriver()
            await driver.press(SOURCE, "left")
            await driver.home(SOURCE)
            assert SOURCE not in driver._deadman
            assert SOURCE not in driver._pressed_at
        asyncio.run(scenario())
        assert [c[0] for c in fake.calls] == ["move"]
        assert client.guard_calls == [0]

    def test_home_on_camera_without_ptz_raises(self, monkeypatch):
        monkeypatch.setattr(ptz_control, "find_camera_config", lambda source_id: None)
        with pytest.raises(PTZError):
            asyncio.run(PtzDriver().home(SOURCE))

    def test_home_without_camera_client_raises(self, monkeypatch):
        monkeypatch.setattr(ptz_control, "find_camera_config", lambda source_id: {"ptz": True})
        monkeypatch.setattr(ptz_control.vision_snap, "get_client", lambda source_id: None)
        with pytest.raises(PTZError):
            asyncio.run(PtzDriver().home(SOURCE))

    def test_home_reports_a_camera_error_as_ptz_error(self, monkeypatch):
        client = FakeGuardClient(error=ReolinkAIError("SetPtzGuard returned code=-1"))
        monkeypatch.setattr(ptz_control, "find_camera_config", lambda source_id: {"ptz": True})
        monkeypatch.setattr(ptz_control.vision_snap, "get_client", lambda source_id: client)
        with pytest.raises(PTZError, match="SetPtzGuard"):
            asyncio.run(PtzDriver().home(SOURCE))

    def test_heartbeat_repeats_the_zoom_command_but_not_pan(self, fake, monkeypatch):
        # Der Zoommotor fährt pro Befehl nur einen Schritt; gehaltener Zoom
        # braucht darum die Wiederholung im Herzschlag, Pan/Tilt nicht.
        monkeypatch.setattr(ptz_control, "VISION_PTZ_DEADMAN_SEC", 5.0)

        async def scenario():
            driver = PtzDriver()
            await driver.press(SOURCE, "zoom_in")
            await driver.keep_alive(SOURCE)
            await driver.keep_alive(SOURCE)
            await driver.stop(SOURCE)
            await driver.press(SOURCE, "left")
            await driver.keep_alive(SOURCE)
            await driver.keep_alive(SOURCE)
            await driver.stop(SOURCE)
        asyncio.run(scenario())
        names = [c[0] for c in fake.calls]
        assert names == ["move", "move", "move", "stop", "move", "stop"]
        zoom = ptz_control.VISION_PTZ_SPEEDS[ptz_control.VISION_PTZ_DEFAULT_SPEED]
        assert fake.calls[1] == ("move", 0.0, 0.0, zoom)
