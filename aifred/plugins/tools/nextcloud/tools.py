"""Nextcloud tools for LLM function calling.

``nextcloud_search`` and ``nextcloud_get`` read; ``nextcloud_create``,
``nextcloud_update`` and ``nextcloud_delete`` write. Reading carries
TIER_WRITE_DATA too: appointments and contacts are private, and the tiers keep
them away from external channels. Tools and entity types exist only for the
modules the plugin settings allow; arguments are parsed strictly; the server
work runs in a worker thread.
"""

import asyncio
import json
from datetime import date, datetime, timedelta
from typing import Any

from ....lib.function_calling import Tool
from ....lib.plugin_base import load_tool_description, load_tool_parameters
from ....lib.security import TIER_WRITE_DATA, TIER_WRITE_SYSTEM, wrap_untrusted_data
from ....lib.tool_args import (
    InvalidValue,
    parse_bool,
    parse_date,
    parse_datetime,
    parse_optional_text,
    parse_text,
    parse_when,
    reject_unknown,
)
from .errors import NextcloudDisabled, NextcloudRefused
from .file_tools import get_nextcloud_file_tools
from .ical import PRIORITY_VALUES
from .store import NextcloudStore
from .vcard import ADDRESS_PARTS, EMAIL_KINDS, PHONE_KINDS

SEARCH_TYPES = ["events", "tasks", "contacts", "notes", "calendars", "tasklists", "addressbooks"]
ITEM_TYPES = ["event", "task", "contact", "note"]
DEFAULT_LIMIT = 20
EVENT_WINDOW_DAYS = 30

_MODULE = {"events": "calendar", "event": "calendar", "calendars": "calendar",
           "tasks": "tasks", "task": "tasks", "tasklists": "tasks",
           "contacts": "contacts", "contact": "contacts", "addressbooks": "contacts",
           "notes": "notes", "note": "notes"}
_EVENT_KEYS = {"title", "start", "end", "all_day", "location", "description", "calendar"}
_TASK_KEYS = {"title", "due", "done", "priority", "description", "list"}
_CONTACT_KEYS = {"name", "first_name", "last_name", "organization", "title", "birthday", "note",
                 "phones", "emails", "addresses", "addressbook"}
_NOTE_KEYS = {"title", "content", "category"}
_EXPECTED_ERRORS = (InvalidValue, NextcloudRefused, NextcloudDisabled)
_DATE_LEN = len("YYYY-MM-DD")


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _json_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_value(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="minutes")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(_json_value(payload), ensure_ascii=False)


def _error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def _require(data: dict, key: str, what: str) -> Any:
    if data.get(key) is None:
        raise InvalidValue(f"{what}: '{key}' is required")
    return data[key]


def _date_or_datetime(value: object, field: str) -> date | datetime:
    if isinstance(value, str) and len(value.strip()) == _DATE_LEN:
        return parse_date(value, field)
    return parse_datetime(value, field)


def _priority(value: object) -> str:
    text = parse_text(value, "priority").strip().lower()
    if text not in PRIORITY_VALUES:
        raise InvalidValue(f"priority must be one of {', '.join(PRIORITY_VALUES)}")
    return text


# ── argument parsing per entity ───────────────────────────────────

def _event_create(store: NextcloudStore, data: dict) -> str:
    reject_unknown(data, _EVENT_KEYS, "event")
    all_day = parse_bool(data.get("all_day", False), "all_day")
    start_raw = _require(data, "start", "event")
    start: date | datetime
    end: date | datetime
    if all_day:
        start = parse_date(start_raw, "start")
        end = parse_date(data.get("end") or start_raw, "end") + timedelta(days=1)
    else:
        start = parse_datetime(start_raw, "start")
        end = parse_datetime(_require(data, "end", "event"), "end")
    return store.create_event(
        calendar=parse_optional_text(data.get("calendar"), "calendar"),
        title=parse_text(_require(data, "title", "event"), "title"), start=start, end=end, all_day=all_day,
        location=parse_optional_text(data.get("location"), "location"),
        description=parse_optional_text(data.get("description"), "description"))


def _event_update(store: NextcloudStore, object_id: str, data: dict) -> None:
    reject_unknown(data, _EVENT_KEYS - {"calendar"}, "event")
    changes: dict[str, Any] = {}
    if "start" in data:
        changes["start"] = parse_when(data["start"], "start", is_end=False)
    if "end" in data:
        changes["end"] = parse_when(data["end"], "end", is_end=True)
    if "all_day" in data:
        changes["all_day"] = parse_bool(data["all_day"], "all_day")
    if "title" in data:
        changes["title"] = parse_text(data["title"], "title")
    for key in ("location", "description"):
        if key in data:
            changes[key] = parse_optional_text(data[key], key)
    store.update_event(object_id, changes)


def _task_create(store: NextcloudStore, data: dict) -> str:
    reject_unknown(data, _TASK_KEYS - {"done"}, "task")
    return store.create_task(
        task_list=parse_optional_text(data.get("list"), "list"),
        title=parse_text(_require(data, "title", "task"), "title"),
        due=_date_or_datetime(data["due"], "due") if data.get("due") is not None else None,
        priority=_priority(data["priority"]) if data.get("priority") is not None else None,
        description=parse_optional_text(data.get("description"), "description"))


def _task_update(store: NextcloudStore, object_id: str, data: dict) -> None:
    reject_unknown(data, _TASK_KEYS - {"list"}, "task")
    changes: dict[str, Any] = {}
    if "title" in data:
        changes["title"] = parse_text(data["title"], "title")
    if "due" in data:
        changes["due"] = _date_or_datetime(data["due"], "due") if data["due"] is not None else None
    if "done" in data:
        changes["done"] = parse_bool(data["done"], "done")
    if "priority" in data:
        changes["priority"] = _priority(data["priority"])
    if "description" in data:
        changes["description"] = parse_optional_text(data["description"], "description")
    store.update_task(object_id, changes)


def _kinds(data: dict, key: str, kinds: dict[str, str]) -> dict[str, str | None]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise InvalidValue(f"contact: '{key}' must be an object of kind → value")
    reject_unknown(value, set(kinds), f"contact {key}")
    return {kind: parse_optional_text(v, f"{key}.{kind}") for kind, v in value.items()}


def _addresses(data: dict) -> dict[str, dict[str, str] | None]:
    value = data.get("addresses", {})
    if not isinstance(value, dict):
        raise InvalidValue("contact: 'addresses' must be an object of kind → address")
    reject_unknown(value, {"home", "work"}, "contact addresses")
    out: dict[str, dict[str, str] | None] = {}
    for kind, parts in value.items():
        if parts is None:
            out[kind] = None
            continue
        if not isinstance(parts, dict):
            raise InvalidValue(f"contact addresses.{kind}: expected an object with {', '.join(ADDRESS_PARTS)}")
        reject_unknown(parts, set(ADDRESS_PARTS), f"contact addresses.{kind}")
        out[kind] = {p: parse_text(v, f"addresses.{kind}.{p}") for p, v in parts.items() if v is not None}
    return out


def _contact_changes(data: dict) -> dict[str, Any]:
    reject_unknown(data, _CONTACT_KEYS, "contact")
    changes: dict[str, Any] = {}
    for key in ("name", "first_name", "last_name", "organization", "title", "note"):
        if key in data:
            changes[key] = parse_optional_text(data[key], key)
    if "birthday" in data:
        changes["birthday"] = parse_date(data["birthday"], "birthday") if data["birthday"] is not None else None
    for key, kinds in (("phones", PHONE_KINDS), ("emails", EMAIL_KINDS)):
        if key in data:
            changes[key] = _kinds(data, key, kinds)
    if "addresses" in data:
        changes["addresses"] = _addresses(data)
    return changes


def _contact_create(store: NextcloudStore, data: dict) -> str:
    changes = _contact_changes(data)
    return store.create_contact(addressbook=parse_optional_text(data.get("addressbook"), "addressbook"),
                                changes={k: v for k, v in changes.items() if k != "addressbook"})


def _contact_update(store: NextcloudStore, object_id: str, data: dict) -> None:
    if "addressbook" in data:
        raise InvalidValue("contact: moving to another address book is not supported")
    store.update_contact(object_id, _contact_changes(data))


def _note_create(store: NextcloudStore, data: dict) -> str:
    reject_unknown(data, _NOTE_KEYS, "note")
    return store.create_note(title=parse_text(_require(data, "title", "note"), "title"),
                             content=parse_optional_text(data.get("content"), "content") or "",
                             category=parse_optional_text(data.get("category"), "category"))


def _note_update(store: NextcloudStore, object_id: str, data: dict) -> str:
    reject_unknown(data, _NOTE_KEYS, "note")
    changes: dict[str, Any] = {}
    if "title" in data:
        changes["title"] = parse_text(data["title"], "title")
    if "content" in data:
        changes["content"] = parse_optional_text(data["content"], "content") or ""
    if "category" in data:
        changes["category"] = parse_optional_text(data["category"], "category")
    return store.update_note(object_id, changes)


def _search(store: NextcloudStore, entity_type: str, query: str | None, date_from: str | None,
            date_to: str | None, completed: object, limit: int) -> list[dict[str, Any]]:
    query = query or None
    if entity_type == "events":
        first = parse_date(date_from, "date_from") if date_from else date.today()
        last = parse_date(date_to, "date_to") if date_to else first + timedelta(days=EVENT_WINDOW_DAYS)
        return store.events(first, last, query, limit)
    if entity_type == "tasks":
        return store.tasks(query, None if completed is None else parse_bool(completed, "completed"), limit)
    if entity_type == "contacts":
        return store.contacts(query, limit)
    if entity_type == "notes":
        return store.notes(query, limit)
    return store.collections(entity_type)


def get_nextcloud_tools(store: NextcloudStore) -> list[Tool]:
    modules = store.modules
    search_types = [t for t in SEARCH_TYPES if modules.can_read(_MODULE[t])]
    get_types = [t for t in ITEM_TYPES if modules.can_read(_MODULE[t])]
    write_types = [t for t in ITEM_TYPES if modules.can_write(_MODULE[t])]

    async def run(func: Any, *args: Any) -> Any:
        return await asyncio.to_thread(func, *args)

    async def nextcloud_search(entity_type: str, query: str | None = None, date_from: str | None = None,
                               date_to: str | None = None, completed: object = None,
                               limit: int = DEFAULT_LIMIT) -> str:
        try:
            if entity_type not in search_types:
                raise InvalidValue(f"entity_type must be one of {', '.join(search_types)}")
            results = await run(_search, store, entity_type, query, date_from, date_to, completed, limit)
        except _EXPECTED_ERRORS as exc:
            return _error(str(exc))
        return _dump({"total_count": len(results), "results": results})

    async def nextcloud_get(entity_type: str, entity_id: str) -> str:
        getters = {"event": store.get_event, "task": store.get_task, "contact": store.get_contact,
                   "note": store.get_note}
        try:
            if entity_type not in get_types:
                raise InvalidValue(f"entity_type must be one of {', '.join(get_types)}")
            result = await run(getters[entity_type], parse_text(entity_id, "entity_id"))
        except _EXPECTED_ERRORS as exc:
            return _error(str(exc))
        # A note can hold anything pasted into it, so its text is fenced as data.
        return wrap_untrusted_data(_dump({"result": result}), "nextcloud") if entity_type == "note" \
            else _dump({"result": result})

    async def nextcloud_create(entity_type: str, data: dict) -> str:
        creators = {"event": _event_create, "task": _task_create, "contact": _contact_create, "note": _note_create}
        try:
            if entity_type not in write_types:
                raise InvalidValue(f"entity_type must be one of {', '.join(write_types)}")
            if not isinstance(data, dict):
                raise InvalidValue("data must be an object")
            new_id = await run(creators[entity_type], store, data)
        except _EXPECTED_ERRORS as exc:
            return _error(str(exc))
        return _dump({"success": True, "id": new_id})

    async def nextcloud_update(entity_type: str, entity_id: str, data: dict) -> str:
        updaters = {"event": _event_update, "task": _task_update, "contact": _contact_update, "note": _note_update}
        try:
            if entity_type not in write_types:
                raise InvalidValue(f"entity_type must be one of {', '.join(write_types)}")
            if not isinstance(data, dict):
                raise InvalidValue("data must be an object")
            object_id = parse_text(entity_id, "entity_id")
            new_id = await run(updaters[entity_type], store, object_id, data)  # a note's id follows its title
        except _EXPECTED_ERRORS as exc:
            return _error(str(exc))
        return _dump({"success": True, "id": new_id or object_id})

    async def nextcloud_delete(entity_type: str, entity_id: str) -> str:
        deleters = {"event": store.delete_event, "task": store.delete_task, "contact": store.delete_contact,
                    "note": store.delete_note}
        try:
            if entity_type not in write_types:
                raise InvalidValue(f"entity_type must be one of {', '.join(write_types)}")
            object_id = parse_text(entity_id, "entity_id")
            await run(deleters[entity_type], object_id)
        except _EXPECTED_ERRORS as exc:
            return _error(str(exc))
        return _dump({"success": True, "id": object_id})

    def tool(name: str, tier: int, executor: Any, types: list[str]) -> Tool:
        return Tool(name=name, tier=tier, executor=executor, read_only=name in ("nextcloud_search", "nextcloud_get"),
                    description=load_tool_description(__file__, name),
                    parameters=load_tool_parameters(__file__, name, enums={"entity_type": types}))

    candidates = (
        ("nextcloud_search", TIER_WRITE_DATA, nextcloud_search, search_types),
        ("nextcloud_get", TIER_WRITE_DATA, nextcloud_get, get_types),
        ("nextcloud_create", TIER_WRITE_DATA, nextcloud_create, write_types),
        ("nextcloud_update", TIER_WRITE_DATA, nextcloud_update, write_types),
        ("nextcloud_delete", TIER_WRITE_SYSTEM, nextcloud_delete, write_types),
    )
    return [*(tool(name, tier, executor, types) for name, tier, executor, types in candidates if types),
            *get_nextcloud_file_tools(store)]
