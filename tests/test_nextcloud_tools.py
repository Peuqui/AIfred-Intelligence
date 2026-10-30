"""Nextcloud tools and instructions against the live server, in the test
collections of the test user. Skipped without local credentials;
every test removes what it creates."""

import asyncio
import json

import pytest
from test_nextcloud_dav import CREDENTIALS, _credentials

from aifred.lib.plugin_modules import ModuleAccess
from aifred.plugins.tools import nextcloud as nextcloud_plugin
from aifred.plugins.tools.nextcloud import store as store_module
from aifred.plugins.tools.nextcloud.settings import NextcloudSettings
from aifred.plugins.tools.nextcloud.store import NextcloudStore
from aifred.plugins.tools.nextcloud.tools import get_nextcloud_tools

pytestmark = pytest.mark.skipif(not CREDENTIALS.exists(), reason=f"no Nextcloud test credentials ({CREDENTIALS})")
ALL_WRITE = ModuleAccess({"calendar": "write", "tasks": "write", "contacts": "write", "files": "off"})


def _store(modules: ModuleAccess) -> NextcloudStore:
    creds = _credentials()
    return NextcloudStore(NextcloudSettings(creds["NEXTCLOUD_URL"], creds["NEXTCLOUD_USER"], modules),
                          creds["NEXTCLOUD_APP_PASSWORD"])


def _call(tools: dict, name: str, **kwargs) -> dict:
    result: dict = json.loads(asyncio.run(tools[name].executor(**kwargs)))
    return result


@pytest.fixture
def tools():
    store = _store(ALL_WRITE)
    yield {t.name: t for t in get_nextcloud_tools(store)}
    store.close()


def test_event_lifecycle_through_the_tools(tools):
    created = _call(tools, "nextcloud_create", entity_type="event",
                    data={"title": "Test Urlaub", "all_day": True, "start": "2026-11-02", "end": "2026-11-06"})
    event_id = created["id"]
    try:
        found = _call(tools, "nextcloud_search", entity_type="events", query="test urlaub",
                      date_from="2026-11-01", date_to="2026-11-30")
        assert [(r["start"], r["end"]) for r in found["results"]] == [("2026-11-02", "2026-11-06")]
        assert "error" in _call(tools, "nextcloud_update", entity_type="event", entity_id=event_id,
                                data={"all_day": False})
        assert _call(tools, "nextcloud_update", entity_type="event", entity_id=event_id,
                     data={"start": "2026-11-09"})["success"]
        moved = _call(tools, "nextcloud_get", entity_type="event", entity_id=event_id)["result"]
        assert (moved["start"], moved["end"]) == ("2026-11-09", "2026-11-13")  # duration kept
    finally:
        assert _call(tools, "nextcloud_delete", entity_type="event", entity_id=event_id)["success"]


def test_unknown_or_malformed_arguments_are_reported(tools):
    assert "priority" in _call(tools, "nextcloud_create", entity_type="event",
                               data={"title": "x", "start": "2026-11-02 10:00", "end": "2026-11-02 11:00",
                                     "priority": "high"})["error"]
    assert "YYYY-MM-DD" in _call(tools, "nextcloud_create", entity_type="event",
                                 data={"title": "x", "start": "02.11.2026 10:00", "end": "02.11.2026 11:00"})["error"]
    assert "high, medium" in _call(tools, "nextcloud_create", entity_type="task",
                                   data={"title": "x", "priority": "urgent"})["error"]


def test_task_and_contact_through_the_tools(tools):
    task = _call(tools, "nextcloud_create", entity_type="task", data={"title": "Test Steuer", "due": "2026-11-07"})
    contact = _call(tools, "nextcloud_create", entity_type="contact",
                    data={"first_name": "Jörg", "last_name": "Meier", "phones": {"mobile": "0172 1"}})
    try:
        assert _call(tools, "nextcloud_update", entity_type="task", entity_id=task["id"], data={"done": True})["success"]
        open_tasks = _call(tools, "nextcloud_search", entity_type="tasks", query="test steuer", completed=False)
        assert open_tasks["total_count"] == 0
        assert "home, mobile, work" in _call(tools, "nextcloud_update", entity_type="contact",
                                             entity_id=contact["id"], data={"phones": {"fax": "1"}})["error"]
        _call(tools, "nextcloud_update", entity_type="contact", entity_id=contact["id"],
              data={"emails": {"work": "j@firma.de"}})
        found = _call(tools, "nextcloud_search", entity_type="contacts", query="jörg")["results"]
        assert [(c["name"], c["emails"]) for c in found] == [("Jörg Meier", {"work": "j@firma.de"})]
    finally:
        _call(tools, "nextcloud_delete", entity_type="task", entity_id=task["id"])
        _call(tools, "nextcloud_delete", entity_type="contact", entity_id=contact["id"])


def test_modules_shape_tools_and_instructions(monkeypatch):
    store = _store(ModuleAccess({"calendar": "write", "tasks": "off", "contacts": "read", "files": "off"}))
    tools = {t.name: t for t in get_nextcloud_tools(store)}
    assert tools["nextcloud_search"].parameters["properties"]["entity_type"]["enum"] == [
        "events", "contacts", "calendars", "addressbooks"]
    assert tools["nextcloud_create"].parameters["properties"]["entity_type"]["enum"] == ["event"]
    assert "error" in _call(tools, "nextcloud_create", entity_type="contact", data={"name": "x"})
    monkeypatch.setattr(store_module, "get_store", lambda: store)
    text = nextcloud_plugin.plugin.get_prompt_instructions("de")
    assert "TERMINE ÄNDERN" in text and "KONTAKTE:" in text
    assert "AUFGABEN:" not in text
    assert "{upcoming_week}" not in text and "DATUMSREFERENZ" in text
    store.close()


def test_read_only_modules_offer_no_write_tools():
    store = _store(ModuleAccess({"calendar": "read", "tasks": "read", "contacts": "read", "files": "off"}))
    assert {t.name for t in get_nextcloud_tools(store)} == {"nextcloud_search", "nextcloud_get"}
    store.close()
