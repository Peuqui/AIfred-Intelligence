"""EPIM tools for LLM function calling.

``epim_search`` and ``epim_get`` read; ``epim_create``, ``epim_update`` and
``epim_delete`` write. Each tool, and each entity type in its enum, exists
only for the modules the plugin settings allow. Arguments are parsed
strictly (``values``); the database layer runs in a worker thread so a
query never blocks the event loop.
"""

import asyncio
import json
import logging
from datetime import date, datetime
from typing import Any

from ....lib.function_calling import Tool
from ....lib.plugin_base import load_tool_description, load_tool_parameters
from ....lib.security import TIER_READONLY, TIER_WRITE_DATA, TIER_WRITE_SYSTEM
from ....lib.tool_args import (
    InvalidValue,
    parse_bool,
    parse_date,
    parse_datetime,
    parse_id,
    parse_optional_text,
    parse_text,
    parse_when,
    reject_unknown,
)
from .db import EpimDatabase, EpimModuleDisabled, EpimRefused, EpimWriteDisabled

logger = logging.getLogger(__name__)

SEARCH_TYPES = ["tasks", "contacts", "notes", "todos", "passwords",
                "categories", "calendars", "todolists", "notetrees"]
GET_TYPES = ["task", "contact", "note"]
CREATE_TYPES = ["task", "contact", "note", "todo"]
UPDATE_TYPES = ["task", "contact", "note", "note_tab", "todo"]
DELETE_TYPES = ["task", "contact", "note", "todo", "password"]
DEFAULT_LIMIT = 20

# Entity type → the modules it belongs to (categories serve calendar and todos).
_ENTITY_MODULES: dict[str, tuple[str, ...]] = {
    "tasks": ("calendar",), "task": ("calendar",), "calendars": ("calendar",),
    "contacts": ("contacts",), "contact": ("contacts",),
    "notes": ("notes",), "note": ("notes",), "note_tab": ("notes",), "notetrees": ("notes",),
    "todos": ("todos",), "todo": ("todos",), "todolists": ("todos",),
    "passwords": ("passwords",), "password": ("passwords",),
    "categories": ("calendar", "todos"),
}


def readable_types(db: EpimDatabase, types: list[str]) -> list[str]:
    return [t for t in types if any(db.can_read(m) for m in _ENTITY_MODULES[t])]


def writable_types(db: EpimDatabase, types: list[str]) -> list[str]:
    return [t for t in types if any(db.can_write(m) for m in _ENTITY_MODULES[t])]

_TASK_KEYS = {"title", "start", "end", "all_day", "location", "text", "category", "calendar", "tags"}
_CONTACT_KEYS = {"name", "fields", "tags"}
_NOTE_KEYS = {"title", "tags"}
_NOTE_CREATE_KEYS = {"title", "text", "tab_name", "note_tree", "tags"}
_NOTE_TAB_KEYS = {"name", "text", "append_text"}
_TODO_KEYS = {"title", "start", "end", "text", "tags", "todo_list", "done"}

_EXPECTED_ERRORS = (InvalidValue, EpimRefused, EpimWriteDisabled, EpimModuleDisabled)
# Password entries hold credentials: deleting one needs the user at the
# browser, never a message arriving through an external channel.
_PASSWORD_CHANNEL = "browser"


def _json_value(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {k: _json_value(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_value(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ", timespec="minutes") if isinstance(value, datetime) else value.isoformat()
    # Models round long integers; IDs travel as strings.
    if isinstance(value, int) and not isinstance(value, bool) and (key == "id" or key.endswith("_id")):
        return str(value)
    return value


def _ok(**payload: Any) -> str:
    return json.dumps(_json_value({"success": True, **payload}), ensure_ascii=False)


def _error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def _require(data: dict, key: str, what: str) -> Any:
    if key not in data or data[key] is None:
        raise InvalidValue(f"{what}: '{key}' is required")
    return data[key]


def _tags(data: dict) -> tuple[str | None, bool]:
    return (parse_optional_text(data["tags"], "tags"), True) if "tags" in data else (None, False)


def _contact_fields(data: dict) -> dict[str, str | None]:
    fields = data.get("fields", {})
    if not isinstance(fields, dict):
        raise InvalidValue("contact: 'fields' must be an object of field name → value")
    return {str(label): parse_optional_text(value, str(label)) for label, value in fields.items()}


def _task_create(db: EpimDatabase, data: dict) -> int:
    reject_unknown(data, _TASK_KEYS, "task")
    all_day = parse_bool(data.get("all_day", False), "all_day")
    start_raw = _require(data, "start", "task")
    if all_day:
        start = datetime.combine(parse_date(start_raw, "start"), datetime.min.time())
        end = parse_when(data.get("end", start_raw), "end", is_end=True)
    else:
        start = parse_datetime(start_raw, "start")
        end = parse_datetime(_require(data, "end", "task"), "end")
    return db.create_task(
        title=parse_text(_require(data, "title", "task"), "title"),
        start=start, end=end, all_day=all_day,
        location=parse_optional_text(data.get("location"), "location"),
        text=parse_optional_text(data.get("text"), "text"),
        category=parse_optional_text(data.get("category"), "category"),
        calendar=parse_optional_text(data.get("calendar"), "calendar"),
        tags=_tags(data)[0],
    )


def _task_update(db: EpimDatabase, task_id: int, data: dict) -> None:
    reject_unknown(data, _TASK_KEYS, "task")
    changes: dict[str, Any] = {}
    if "start" in data:
        changes["start"] = parse_when(data["start"], "start", is_end=False)
    if "end" in data:
        changes["end"] = parse_when(data["end"], "end", is_end=True)
    if "all_day" in data:
        changes["all_day"] = parse_bool(data["all_day"], "all_day")
    if "title" in data:
        changes["title"] = parse_text(data["title"], "title")
    for key in ("location", "text", "category", "tags"):
        if key in data:
            changes[key] = parse_optional_text(data[key], key)
    if "calendar" in data:
        changes["calendar"] = parse_text(data["calendar"], "calendar")
    db.update_task(task_id, changes)


def _contact_create(db: EpimDatabase, data: dict) -> int:
    reject_unknown(data, _CONTACT_KEYS, "contact")
    return db.create_contact(name=parse_optional_text(data.get("name"), "name"),
                             fields=_contact_fields(data), tags=_tags(data)[0])


def _contact_update(db: EpimDatabase, contact_id: int, data: dict) -> None:
    reject_unknown(data, _CONTACT_KEYS, "contact")
    tags, tags_given = _tags(data)
    name = parse_text(data["name"], "name") if "name" in data else None
    db.update_contact(contact_id, name=name, fields=_contact_fields(data), tags=tags, tags_given=tags_given)


def _note_create(db: EpimDatabase, data: dict) -> int:
    reject_unknown(data, _NOTE_CREATE_KEYS, "note")
    return db.create_note(
        title=parse_text(_require(data, "title", "note"), "title"),
        text=parse_text(data.get("text", ""), "text"),
        tab_name=parse_text(_require(data, "tab_name", "note"), "tab_name"),
        note_tree=parse_optional_text(data.get("note_tree"), "note_tree"),
        tags=_tags(data)[0],
    )


def _note_update(db: EpimDatabase, note_id: int, data: dict) -> None:
    reject_unknown(data, _NOTE_KEYS, "note")
    tags, tags_given = _tags(data)
    title = parse_text(data["title"], "title") if "title" in data else None
    db.update_note(note_id, title=title, tags=tags, tags_given=tags_given)


def _note_tab_update(db: EpimDatabase, tab_id: int, data: dict) -> None:
    reject_unknown(data, _NOTE_TAB_KEYS, "note_tab")
    if "text" in data and "append_text" in data:
        raise InvalidValue("note_tab: give either 'text' (replace) or 'append_text', not both")
    db.update_note_tab(
        tab_id,
        name=parse_text(data["name"], "name") if "name" in data else None,
        text=parse_text(data["text"], "text") if "text" in data else None,
        append_text=parse_text(data["append_text"], "append_text") if "append_text" in data else None,
    )


def _todo_create(db: EpimDatabase, data: dict) -> int:
    reject_unknown(data, _TODO_KEYS - {"done"}, "todo")
    return db.create_todo(
        title=parse_text(_require(data, "title", "todo"), "title"),
        todo_list=parse_optional_text(data.get("todo_list"), "todo_list"),
        start=parse_when(data["start"], "start", is_end=False) if data.get("start") is not None else None,
        end=parse_when(data["end"], "end", is_end=True) if data.get("end") is not None else None,
        text=parse_optional_text(data.get("text"), "text"),
        tags=_tags(data)[0],
    )


def _todo_update(db: EpimDatabase, todo_id: int, data: dict) -> None:
    reject_unknown(data, _TODO_KEYS, "todo")
    changes: dict[str, Any] = {}
    for key, is_end in (("start", False), ("end", True)):
        if key in data:
            changes[key] = None if data[key] is None else parse_when(data[key], key, is_end)
    if "title" in data:
        changes["title"] = parse_text(data["title"], "title")
    for key in ("text", "tags"):
        if key in data:
            changes[key] = parse_optional_text(data[key], key)
    if "todo_list" in data:
        changes["todo_list"] = parse_text(data["todo_list"], "todo_list")
    if "done" in data:
        changes["done"] = parse_bool(data["done"], "done")
    db.update_todo(todo_id, changes)


def _search(db: EpimDatabase, entity_type: str, query: str | None, date_from: str | None,
            date_to: str | None, completed: object, limit: int) -> list[dict[str, Any]]:
    query = query or None
    if entity_type == "tasks":
        return db.search_tasks(query,
                               parse_date(date_from, "date_from") if date_from else None,
                               parse_date(date_to, "date_to") if date_to else None, limit)
    if entity_type == "contacts":
        return db.search_contacts(query, limit)
    if entity_type == "notes":
        return db.search_notes(query, limit)
    if entity_type == "todos":
        done = None if completed is None else parse_bool(completed, "completed")
        return db.search_todos(query, done, limit)
    if entity_type == "passwords":
        return db.search_passwords(query, limit)
    lookups = db.lookups()
    return [{"id": e.id, "name": e.name} for e in lookups[entity_type]]


def get_epim_tools(db: EpimDatabase, source: str) -> list[Tool]:
    """Tools for the modules ``db`` allows. ``source`` is the channel the
    request came from (browser, email, …)."""
    search_types = readable_types(db, SEARCH_TYPES)
    get_types = readable_types(db, GET_TYPES)
    create_types = writable_types(db, CREATE_TYPES)
    update_types = writable_types(db, UPDATE_TYPES)
    delete_types = writable_types(db, DELETE_TYPES)

    async def run(label: str, func: Any, *args: Any) -> Any:
        from ....lib.logging_utils import log_message
        log_message(f"🗓️ {label}")
        return await asyncio.to_thread(func, *args)

    async def epim_search(entity_type: str, query: str | None = None, date_from: str | None = None,
                          date_to: str | None = None, completed: object = None,
                          limit: int = DEFAULT_LIMIT) -> str:
        try:
            if entity_type not in search_types:
                raise InvalidValue(f"entity_type must be one of {', '.join(search_types)}")
            results = await run(f"epim_search {entity_type} query={query}", _search, db,
                                entity_type, query, date_from, date_to, completed, limit)
        except _EXPECTED_ERRORS as exc:
            return _error(str(exc))
        return json.dumps(_json_value({"total_count": len(results), "results": results}), ensure_ascii=False)

    async def epim_get(entity_type: str, entity_id: object) -> str:
        getters = {"task": db.get_task, "contact": db.get_contact, "note": db.get_note}
        try:
            if entity_type not in get_types:
                raise InvalidValue(f"entity_type must be one of {', '.join(get_types)}")
            row_id = parse_id(entity_id, "entity_id")
            result = await run(f"epim_get {entity_type} {row_id}", getters[entity_type], row_id)
        except _EXPECTED_ERRORS as exc:
            return _error(str(exc))
        if result is None:
            return _error(f"{entity_type} {entity_id} not found")
        return json.dumps(_json_value({"result": result}), ensure_ascii=False)

    async def epim_create(entity_type: str, data: dict) -> str:
        creators = {"task": _task_create, "contact": _contact_create, "note": _note_create,
                    "todo": _todo_create}
        try:
            if entity_type not in create_types:
                raise InvalidValue(f"entity_type must be one of {', '.join(create_types)}")
            if not isinstance(data, dict):
                raise InvalidValue("data must be an object")
            new_id = await run(f"epim_create {entity_type}", creators[entity_type], db, data)
        except _EXPECTED_ERRORS as exc:
            return _error(str(exc))
        return _ok(id=new_id)

    async def epim_update(entity_type: str, entity_id: object, data: dict) -> str:
        updaters = {"task": _task_update, "contact": _contact_update, "note": _note_update,
                    "note_tab": _note_tab_update, "todo": _todo_update}
        try:
            if entity_type not in update_types:
                raise InvalidValue(f"entity_type must be one of {', '.join(update_types)}")
            if not isinstance(data, dict):
                raise InvalidValue("data must be an object")
            row_id = parse_id(entity_id, "entity_id")
            await run(f"epim_update {entity_type} {row_id}", updaters[entity_type], db, row_id, data)
        except _EXPECTED_ERRORS as exc:
            return _error(str(exc))
        return _ok(id=row_id)

    async def epim_delete(entity_type: str, entity_id: object) -> str:
        deleters = {"task": db.delete_task, "contact": db.delete_contact, "note": db.delete_note,
                    "todo": db.delete_todo, "password": db.delete_password}
        try:
            if entity_type not in delete_types:
                raise InvalidValue(f"entity_type must be one of {', '.join(delete_types)}")
            if entity_type == "password" and source != _PASSWORD_CHANNEL:
                raise EpimRefused("password entries can only be deleted from the web UI")
            row_id = parse_id(entity_id, "entity_id")
            await run(f"epim_delete {entity_type} {row_id}", deleters[entity_type], row_id)
        except _EXPECTED_ERRORS as exc:
            return _error(str(exc))
        return _ok(id=row_id)

    def tool(name: str, tier: int, executor: Any, enum: list[str]) -> Tool:
        return Tool(name=name, tier=tier, executor=executor,
                    description=load_tool_description(__file__, name),
                    parameters=load_tool_parameters(__file__, name, enums={"entity_type": enum}))

    candidates = (
        ("epim_search", TIER_READONLY, epim_search, search_types),
        ("epim_get", TIER_READONLY, epim_get, get_types),
        ("epim_create", TIER_WRITE_DATA, epim_create, create_types),
        ("epim_update", TIER_WRITE_DATA, epim_update, update_types),
        ("epim_delete", TIER_WRITE_SYSTEM, epim_delete, delete_types),
    )
    return [tool(name, tier, executor, types) for name, tier, executor, types in candidates if types]

