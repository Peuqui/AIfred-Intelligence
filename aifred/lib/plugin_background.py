"""What runs in the background runs exactly while the plugin it belongs to is on.

Every tool plugin names its background work itself (``ToolPlugin.background_parts``); a channel's is its listener,
which runs while the channel is configured, its main switch (``monitor``) is on and, for a channel that does not
always answer, its listener switch too. ``sync_background_parts`` compares that with what runs and starts or stops the
difference. It is called once at the server's start and after every switch (``plugin_admin``), so a switch takes effect
at once and the start after a restart sees the same state: one place, no registrations scattered over the start-up.

The parts run in two homes: the Message Hub's thread (``BackgroundPart.in_hub``) or the server's loop. The call comes
from the server's loop.
"""

from __future__ import annotations

import asyncio

from .logging_utils import log_message
from .message_hub import message_hub
from .plugin_base import BackgroundPart
from .plugin_registry import all_channels, channel_switch, discover_tools

_server_tasks: dict[str, asyncio.Task[None]] = {}


def wanted_parts() -> dict[str, BackgroundPart]:
    """Every part that should run now: those of the tool plugins that are on, the listeners of the channels that are."""
    parts: dict[str, BackgroundPart] = {}
    for plugin in discover_tools():
        provider = getattr(plugin, "background_parts", None)
        if callable(provider):
            parts.update(provider())
    for name, channel in all_channels().items():
        listens = channel_switch(name, "monitor") and (channel.always_reply or channel_switch(name, "listener"))
        if channel.is_configured() and listens:
            parts[name] = BackgroundPart(channel.listener_loop, in_hub=True)
    return parts


def _sync_server_tasks(wanted: dict[str, BackgroundPart]) -> None:
    for name in [name for name in _server_tasks if name not in wanted or wanted[name].in_hub]:
        _server_tasks.pop(name).cancel()
        log_message(f"Background: '{name}' stopped")
    for name, part in wanted.items():
        task = _server_tasks.get(name)
        if not part.in_hub and (task is None or task.done()):
            _server_tasks[name] = asyncio.create_task(part.factory(), name=f"background:{name}")
            log_message(f"Background: '{name}' started")


def _sync_hub_workers(wanted: dict[str, BackgroundPart]) -> None:
    wanted_in_hub = {name: part for name, part in wanted.items() if part.in_hub}
    for name in message_hub.registered() - set(wanted_in_hub):
        message_hub.unregister(name)
    for name, part in wanted_in_hub.items():
        if name not in message_hub.registered():
            message_hub.register(name, part.factory)


async def sync_background_parts() -> None:
    """Start what should run and is not running, stop what runs and should not."""
    wanted = wanted_parts()
    _sync_server_tasks(wanted)
    _sync_hub_workers(wanted)
    await message_hub.start_all()


def stop_server_tasks() -> None:
    """The server shuts down: its loop's parts end with it (the hub stops its own workers)."""
    for name in list(_server_tasks):
        _server_tasks.pop(name).cancel()
