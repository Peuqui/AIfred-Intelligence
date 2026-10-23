"""Google Suite: per-service levels decide scopes, tools and the Drive source
(no Google account needed — settings and broker are stubbed)."""

import asyncio

import pytest

from aifred.lib.document_sources import DocumentSourceError
from aifred.lib.oauth import broker as broker_module
from aifred.lib.plugin_base import PluginContext
from aifred.plugins.tools import google_suite
from aifred.plugins.tools.google_suite.calendar.tools import (
    READ_TOOL_NAMES as CALENDAR_READ,
)
from aifred.plugins.tools.google_suite.calendar.tools import get_calendar_tools
from aifred.plugins.tools.google_suite.contacts.tools import (
    READ_TOOL_NAMES as CONTACTS_READ,
)
from aifred.plugins.tools.google_suite.contacts.tools import get_contacts_tools
from aifred.plugins.tools.google_suite.drive.source import DriveDocumentSource
from aifred.plugins.tools.google_suite.drive.tools import READ_TOOL_NAMES as DRIVE_READ
from aifred.plugins.tools.google_suite.drive.tools import get_drive_tools
from aifred.plugins.tools.google_suite.tasks.tools import READ_TOOL_NAMES as TASKS_READ
from aifred.plugins.tools.google_suite.tasks.tools import get_tasks_tools

CTX = PluginContext(agent_id="test", lang="de", session_id="")
SCOPE = "https://www.googleapis.com/auth/"
IDENTITY = [f"{SCOPE}userinfo.email", f"{SCOPE}userinfo.profile"]


@pytest.fixture
def plugin(monkeypatch):
    """The plugin with settings from ``plugin.levels`` (service → level)."""
    instance = google_suite.GooglePlugin()
    levels: dict[str, str] = {}
    monkeypatch.setattr(instance, "_load_settings",
                        lambda: {f"GOOGLE_MODULE_{s.upper()}": level for s, level in levels.items()})
    instance.levels = levels  # type: ignore[attr-defined]
    return instance


def test_read_tool_names_exist():
    for tools, names in ((get_calendar_tools(), CALENDAR_READ), (get_contacts_tools(), CONTACTS_READ),
                         (get_tasks_tools(), TASKS_READ), (get_drive_tools("AIfred"), DRIVE_READ)):
        offered = {t.name for t in tools}
        assert names < offered  # every read name is a real tool, and some tools write


def test_scopes_cover_the_services_switched_on(plugin):
    plugin.levels.update(calendar="write", contacts="read", tasks="off", drive="read")
    assert plugin.aggregated_scopes() == [f"{SCOPE}calendar", f"{SCOPE}contacts", f"{SCOPE}drive", *IDENTITY]


def test_default_offers_only_read_tools(plugin):
    assert {t.name for t in plugin.get_tools(CTX)} == CALENDAR_READ | CONTACTS_READ | TASKS_READ | DRIVE_READ


def test_grant_needs_a_right_per_service_switched_on(plugin):
    plugin.levels.update(calendar="read", contacts="off", tasks="off", drive="off")
    assert plugin.oauth_grant_current([f"{SCOPE}calendar"])  # identity does not count
    assert plugin.oauth_grant_current([f"{SCOPE}calendar", f"{SCOPE}drive", *IDENTITY])  # more rights do no harm
    assert not plugin.oauth_grant_current([f"{SCOPE}calendar.readonly", *IDENTITY])  # read-only grant is not enough
    assert not plugin.oauth_grant_current(IDENTITY)


def test_level_changes_need_no_reconnect(plugin, monkeypatch):
    from aifred.lib.credential_broker import broker as credentials
    monkeypatch.setattr(credentials, "get", lambda service, key: "client" if service == "google" else "")
    monkeypatch.setattr(broker_module.oauth_broker, "is_connected", lambda name: True)
    granted = [f"{SCOPE}calendar", f"{SCOPE}contacts", f"{SCOPE}tasks", *IDENTITY]
    monkeypatch.setattr(broker_module.oauth_broker, "granted_scopes", lambda name: granted)
    plugin.levels.update(calendar="write", contacts="read", tasks="off", drive="off")
    assert plugin.is_available()
    plugin.levels.update(calendar="read", contacts="write", tasks="read")
    assert plugin.is_available()
    plugin.levels.update(drive="read")  # never granted: reconnect needed
    assert not plugin.is_available()


def test_tools_per_level(plugin):
    plugin.levels.update(calendar="write", contacts="off", tasks="read", drive="off")
    names = {t.name for t in plugin.get_tools(CTX)}
    assert names == {t.name for t in get_calendar_tools()} | TASKS_READ


def test_drive_source_follows_the_drive_level(plugin):
    writable = {}
    for level in ("off", "read", "write"):
        plugin.levels.update(drive=level)
        source = plugin.document_source
        writable[level] = None if source is None else source.writable
    assert writable == {"off": None, "read": False, "write": True}


def test_read_only_drive_source_refuses_uploads():
    with pytest.raises(DocumentSourceError):
        asyncio.run(DriveDocumentSource(writable=False).upload("", "a.txt", b"x", "text/plain"))
