"""Off means off (14.11.2026): what runs in the background runs exactly while the plugin it belongs to is on.

``plugin_background.sync_background_parts`` is the one place that starts and stops it, at the server's start and after
every switch. Stand-ins for the plugins, the channels and the hub; the settings in a temporary file.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from aifred.lib import plugin_admin, plugin_background, plugin_registry, settings
from aifred.lib.plugin_base import BackgroundPart


class _Hub:
    def __init__(self) -> None:
        self.workers: set[str] = set()
        self.started = 0

    def registered(self) -> set[str]:
        return set(self.workers)

    def register(self, name: str, _factory: Any) -> None:
        self.workers.add(name)

    def unregister(self, name: str) -> None:
        self.workers.discard(name)

    async def start_all(self) -> None:
        self.started += 1


async def _forever() -> None:
    await asyncio.Event().wait()


class _Plugin:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[str] = []

    def background_parts(self) -> dict[str, BackgroundPart]:
        return {f"{self.name}_hub": BackgroundPart(_forever, in_hub=True), f"{self.name}_loop": BackgroundPart(_forever)}

    async def on_switched_off(self) -> None:
        self.calls.append("off")

    def on_switched_on(self) -> None:
        self.calls.append("on")


class _Channel:
    def __init__(self, name: str, always_reply: bool = False) -> None:
        self.name = name
        self.always_reply = always_reply

    def is_configured(self) -> bool:
        return True

    async def listener_loop(self) -> None:
        await _forever()


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({}))
    monkeypatch.setattr(settings, "SETTINGS_FILE", settings_file)
    hub = _Hub()
    plugin = _Plugin("alpha")
    state: dict[str, Any] = {"hub": hub, "plugin": plugin, "on": {"alpha"}, "channels": {"mail": _Channel("mail"), "chat": _Channel("chat", True)}}
    monkeypatch.setattr(plugin_background, "message_hub", hub)
    monkeypatch.setattr(plugin_background, "discover_tools", lambda: [plugin] if "alpha" in state["on"] else [])
    monkeypatch.setattr(plugin_background, "all_channels", lambda: dict(state["channels"]))
    monkeypatch.setattr(plugin_background, "_server_tasks", {})
    # The switches of plugin_admin without the real plugin folders.
    monkeypatch.setattr(plugin_registry, "get_channel", lambda name: state["channels"].get(name))
    monkeypatch.setattr(plugin_registry, "list_all_plugins", lambda: [{"name": "alpha", "type": "tool"}])
    monkeypatch.setattr(plugin_registry, "is_plugin_enabled", lambda name: name in state["on"])
    monkeypatch.setattr(plugin_registry, "get_tool_plugin", lambda name: plugin if name in state["on"] else None)
    monkeypatch.setattr(plugin_registry, "disable_plugin", lambda name: state["on"].discard(name) is None)
    monkeypatch.setattr(plugin_registry, "enable_plugin", lambda name: state["on"].add(name) is None)
    return state


def _toggles(**channels: dict[str, bool]) -> None:
    settings.update_settings({plugin_registry.CHANNEL_TOGGLES: channels})


def test_the_parts_of_a_plugin_run_exactly_while_it_is_on(world: dict[str, Any]) -> None:
    async def scenario() -> None:
        await plugin_background.sync_background_parts()
        assert world["hub"].workers == {"alpha_hub"} and set(plugin_background._server_tasks) == {"alpha_loop"}
        task = plugin_background._server_tasks["alpha_loop"]
        world["on"].discard("alpha")                     # switched off
        await plugin_background.sync_background_parts()
        assert world["hub"].workers == set() and plugin_background._server_tasks == {}
        await asyncio.sleep(0)
        assert task.cancelled()

    asyncio.run(scenario())


def test_the_start_after_a_restart_sees_the_same_state_as_a_switch(world: dict[str, Any]) -> None:
    """A plugin that is off at the start gets no part: the start is the same sync as a switch."""
    world["on"].clear()

    async def scenario() -> None:
        await plugin_background.sync_background_parts()
        assert world["hub"].workers == set() and plugin_background._server_tasks == {}
        assert world["hub"].started == 1             # the hub itself runs (channels may come)

    asyncio.run(scenario())


def test_switching_a_tool_runs_its_hooks_and_the_sync(world: dict[str, Any]) -> None:
    async def scenario() -> None:
        await plugin_admin.set_tool("alpha", True)       # already on: nothing happens
        assert world["plugin"].calls == []
        await plugin_admin.set_tool("alpha", False)
        assert world["plugin"].calls == ["off"] and world["hub"].workers == set()
        await plugin_admin.set_tool("alpha", True)
        assert world["plugin"].calls == ["off", "on"] and world["hub"].workers == {"alpha_hub"}

    asyncio.run(scenario())


def test_a_channel_listens_only_with_its_main_switch(world: dict[str, Any]) -> None:
    """Until 14.11.2026 only the listener switch (or, for a channel that always answers, the main switch) counted
    after a start: the main switch of a mail channel did not stop its listener."""
    async def scenario() -> None:
        _toggles(mail={"monitor": True, "listener": True}, chat={"monitor": True})
        await plugin_background.sync_background_parts()
        assert {"mail", "chat"} <= world["hub"].workers
        await plugin_admin.set_channel("mail", monitor=False)
        assert "mail" not in world["hub"].workers and "chat" in world["hub"].workers
        await plugin_admin.set_channel("mail", monitor=True)       # its listener switch is still on
        assert "mail" in world["hub"].workers
        await plugin_admin.set_channel("chat", monitor=False)
        assert "chat" not in world["hub"].workers

    asyncio.run(scenario())


def test_a_channel_with_the_listener_off_does_not_listen(world: dict[str, Any]) -> None:
    _toggles(mail={"monitor": True, "listener": False})
    asyncio.run(plugin_background.sync_background_parts())
    assert "mail" not in world["hub"].workers
