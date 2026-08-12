"""EPIM database tools for LLM function calling.

Provides 5 tools for full CRUD on EPIM entities:
- epim_search: Search/read tasks, contacts, notes, todos, passwords
- epim_get: Read one entry with full details (note tabs, contact fields)
- epim_create: Create new entries
- epim_update: Update existing entries
- epim_delete: Soft-delete entries
"""

import json
import logging
from typing import Any, Optional

from ....lib.function_calling import Tool
from ....lib.security import TIER_READONLY, TIER_WRITE_DATA, TIER_WRITE_SYSTEM

logger = logging.getLogger(__name__)

# Canonical entity type mapping — all aliases resolve to one key
ENTITY_ALIASES: dict[str, str] = {
    "task": "task", "tasks": "task", "termin": "task", "termine": "task",
    "kalender": "task", "calendar": "task",
    "contact": "contact", "contacts": "contact", "kontakt": "contact", "kontakte": "contact",
    "note": "note", "notes": "note", "notiz": "note", "notizen": "note",
    "todo": "todo", "todos": "todo", "aufgabe": "todo", "aufgaben": "todo",
    "password": "password", "passwords": "password", "passwort": "password", "passwoerter": "password",
    "category": "category", "categories": "category", "kategorie": "category", "kategorien": "category",
    "calendar_list": "calendar_list", "kalender_liste": "calendar_list",
    "todolist": "todolist", "todolists": "todolist", "todoliste": "todolist", "todolisten": "todolist",
    "notetree": "notetree", "notetrees": "notetree", "notizbaum": "notetree",
    "note_tab": "note_tab", "notiz_tab": "note_tab",
}

VALID_ENTITY_TYPES = "tasks, contacts, notes, todos, passwords, categories, calendar_list, todolists, notetrees"

_PASSWORD_WRITE_REFUSED = (
    "Error: refused to create/update a password entry from an external "
    "channel (credential-safety guard). Password entries can only be "
    "modified from the web UI."
)


def _resolve_entity(entity_type: str) -> str | None:
    """Resolve entity type alias to canonical name. Returns None if unknown."""
    return ENTITY_ALIASES.get(entity_type.lower())


# EN→DE-Aliasse für flache Kontaktfelder. Alle Ziele MÜSSEN in
# DEFAULT_CONTACT_FIELDS existieren — encode_fieldsdata ist fail-loud bei
# unbekannten Namen (der frühere work_email-Geist bewies, warum).
_CONTACT_EN_TO_DE = {
    "first_name": "Vorname", "last_name": "Nachname",
    "phone": "Telefon", "mobile": "Mobiltelefon",
    "email": "E-Mail", "address": "Adresse",
    "city": "Ort", "zip": "PLZ", "state": "Bundesland",
    "country": "Land", "birthday": "Geburtstag",
    "website": "Webseite", "fax": "Fax",
    "company": "Firma", "job_title": "Position", "notes": "Notizen",
    "work_phone": "Telefon geschäftlich",
    "work_address": "Adresse geschäftlich",
    "work_city": "Ort geschäftlich", "work_zip": "PLZ geschäftlich",
}


def _extract_contact_fields(data: dict) -> "dict[str, Any] | None":
    """Flache Kontaktfelder ({"Telefon": ...} / {"phone": ...}) aus dem
    data-Dict extrahieren, EN-Aliasse auf die EPIM-Feldnamen mappen.

    SSOT für epim_create UND epim_update — die deutschen Namen kommen aus
    der Feldmap DEFAULT_CONTACT_FIELDS (keine driftende Zweitliste).
    None, wenn nichts extrahierbar ist.
    """
    from .db import DEFAULT_CONTACT_FIELDS
    keys = set(DEFAULT_CONTACT_FIELDS.values()) | set(_CONTACT_EN_TO_DE)
    fields = {k: v for k, v in data.items() if k in keys and v}
    return {_CONTACT_EN_TO_DE.get(k, k): v for k, v in fields.items()} or None


def _serialize(obj: Any, key: str = "") -> Any:
    """JSON-serialize EPIM results (handle datetime, large IDs etc.)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {k: _serialize(v, key=k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return "<binary>"
    # Convert large integer IDs to strings so LLMs don't truncate them
    if isinstance(obj, int) and "ID" in key.upper():
        return str(obj)
    return obj


def get_epim_tools(source: str = "browser") -> list[Tool]:
    """Create EPIM database tools for LLM function calling.

    Returns empty list if EPIM is not available.

    ``source`` is the channel origin (browser/email/discord/…). Password
    entries hold plaintext credentials, so create/update on ``password`` is
    restricted to the browser (user present, driving) — an injected prompt
    arriving through any external channel must not plant or overwrite a
    credential entry. Non-password EPIM writes stay at their tool tier.
    """
    from .db import get_epim_db

    db = get_epim_db()
    if db is None:
        return []

    # ----------------------------------------------------------
    # epim_search
    # ----------------------------------------------------------
    async def _epim_search(
        entity_type: str,
        query: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        completed: Optional[bool] = None,
        limit: int = 20,
    ) -> str:
        """Search EPIM database."""
        from ....lib.logging_utils import log_message
        log_message(f"🗓️ epim_search: {entity_type} query={query}")

        entity = _resolve_entity(entity_type)
        if not entity:
            return json.dumps({"error": f"Unknown entity_type: {entity_type}. Use: {VALID_ENTITY_TYPES}"})

        results: list[dict] | dict | None = None

        if entity == "task":
            results = db.search_tasks(title=query, date_from=date_from, date_to=date_to, limit=limit)
        elif entity == "contact":
            results = db.search_contacts(name=query, limit=limit)
        elif entity == "note":
            results = db.search_notes(title=query, text=query, limit=limit)
        elif entity == "todo":
            results = db.search_todos(title=query, completed=completed, limit=limit)
        elif entity == "password":
            results = db.search_passwords(subject=query, limit=limit)
        elif entity == "category":
            results = db.get_categories()
        elif entity == "calendar_list":
            results = db.get_calendars()
        elif entity == "todolist":
            results = db.get_todolists()
        elif entity == "notetree":
            results = db.get_notetrees()
        else:
            # e.g. "note_tab" resolves via ENTITY_ALIASES but has no search path
            # — return an explicit error instead of a confusing {results: null}.
            return json.dumps({
                "error": f"entity_type {entity_type!r} is not searchable. Use: {VALID_ENTITY_TYPES}"
            })

        serialized = _serialize(results)
        count = len(serialized) if isinstance(serialized, list) else 1
        log_message(f"✅ epim_search: {count} {entity_type} found")

        # Add short index numbers so the LLM can reference entries easily
        if isinstance(serialized, list):
            for i, item in enumerate(serialized, 1):
                if isinstance(item, dict):
                    item["_index"] = i

        return json.dumps({"total_count": count, "results": serialized}, ensure_ascii=False, default=str)

    # ----------------------------------------------------------
    # epim_get
    # ----------------------------------------------------------
    async def _epim_get(entity_type: str, entity_id: int | str) -> str:
        """Read one EPIM entry with full details."""
        from ....lib.logging_utils import log_message
        entity_id = int(entity_id)  # Accept string IDs from LLM
        log_message(f"🗓️ epim_get: {entity_type} id={entity_id}")

        entity = _resolve_entity(entity_type)
        if entity == "password":
            # search_passwords deliberately returns subjects only; a detail
            # read would hand plaintext credentials to whatever channel is
            # driving the LLM. Password contents stay out of the tool surface.
            return json.dumps({
                "error": "password entries cannot be read via epim_get "
                "(credential safety) — only searched by subject."
            })
        if entity not in ("task", "contact", "note"):
            return json.dumps({
                "error": f"Unknown entity_type for get: {entity_type}. "
                "Use: task, contact, note (todos already include their text in epim_search)"
            })

        result: Optional[dict]
        if entity == "task":
            result = db.get_task(entity_id)
            if result is not None:
                # Raw hex-encoded custom-field blob — meaningless to the LLM
                result.pop("FIELDSDATA", None)
                result.pop("FIELDSDATA2", None)
        elif entity == "contact":
            result = db.get_contact(entity_id)
        else:
            result = db.get_note(entity_id)

        if result is None:
            log_message(f"❌ epim_get: {entity} {entity_id} not found")
            return json.dumps({"error": f"{entity} {entity_id} not found"})

        log_message(f"✅ epim_get: {entity} {entity_id}")
        return json.dumps({"result": _serialize(result)}, ensure_ascii=False, default=str)

    # ----------------------------------------------------------
    # epim_create
    # ----------------------------------------------------------
    async def _epim_create(entity_type: str, data: dict) -> str:
        """Create a new EPIM entry."""
        from ....lib.logging_utils import log_message
        log_message(f"🗓️ epim_create: {entity_type}")

        entity = _resolve_entity(entity_type)
        if entity not in ("task", "contact", "note", "todo", "password"):
            return json.dumps({"error": f"Unknown entity_type for create: {entity_type}. Use: task, contact, note, todo, password"})

        if entity == "password" and source != "browser":
            return json.dumps({"error": _PASSWORD_WRITE_REFUSED})

        if entity == "task":
            title = data.get("title", "Neuer Termin")
            new_id = db.create_task(
                title=title,
                start=data.get("start", ""),
                end=data.get("end", ""),
                location=data.get("location"),
                allday=data.get("allday", False),
                text=data.get("text"),
                priority=data.get("priority", 0),
                tags=data.get("tags"),
                calendar_id=data.get("calendar_id"),
                calendar_name=data.get("calendar_name") or data.get("calendar"),
                category_id=data.get("category_id"),
                category_name=data.get("category_name") or data.get("category"),
            )
            log_message(f"✅ epim_create: Task {new_id} '{title}'")
            return json.dumps({"success": True, "id": new_id, "title": title})

        elif entity == "contact":
            name = data.get("name", "Neuer Kontakt")
            # Fields can be nested {"fields": {"Telefon": "..."}} or flat {"Telefon": "..."}
            fields = data.get("fields") or _extract_contact_fields(data)
            new_id = db.create_contact(
                name=name,
                fields=fields if fields else None,
                tags=data.get("tags"),
            )
            log_message(f"✅ epim_create: Contact {new_id} '{name}'")
            return json.dumps({"success": True, "id": new_id, "name": name})

        elif entity == "note":
            title = data.get("title", "Neue Notiz")
            new_id = db.create_note(
                title=title,
                tab_name=data.get("tab_name", "Tab 1"),
                tab_text=data.get("text", ""),
                tags=data.get("tags"),
                tree_id=data.get("tree_id"),
                tree_name=data.get("tree_name") or data.get("tree"),
            )
            log_message(f"✅ epim_create: Note {new_id} '{title}'")
            return json.dumps({"success": True, "id": new_id, "title": title})

        elif entity == "todo":
            title = data.get("title", "Neue Aufgabe")
            new_id = db.create_todo(
                title=title,
                start=data.get("start"),
                end=data.get("end"),
                priority=data.get("priority", 0),
                text=data.get("text"),
                tags=data.get("tags"),
                list_id=data.get("list_id"),
                list_name=data.get("list_name") or data.get("list"),
            )
            log_message(f"✅ epim_create: Todo {new_id} '{title}'")
            return json.dumps({"success": True, "id": new_id, "title": title})

        else:  # password
            subject = data.get("subject", "Neuer Eintrag")
            new_id = db.create_password(
                subject=subject,
                fields=data.get("fields"),
                group_id=data.get("group_id"),
                tags=data.get("tags"),
            )
            log_message(f"✅ epim_create: Password {new_id} '{subject}'")
            return json.dumps({"success": True, "id": new_id, "subject": subject})

    # ----------------------------------------------------------
    # epim_update
    # ----------------------------------------------------------
    async def _epim_update(entity_type: str, entity_id: int | str, data: dict) -> str:
        """Update an existing EPIM entry."""
        from ....lib.logging_utils import log_message
        entity_id = int(entity_id)  # Accept string IDs from LLM
        log_message(f"🗓️ epim_update: {entity_type} id={entity_id}")

        entity = _resolve_entity(entity_type)
        if entity not in ("task", "contact", "note", "note_tab", "todo", "password"):
            return json.dumps({"error": f"Unknown entity_type for update: {entity_type}. Use: task, contact, note, note_tab, todo, password"})

        if entity == "password" and source != "browser":
            return json.dumps({"error": _PASSWORD_WRITE_REFUSED})

        if entity == "task":
            # Map LLM-friendly field names to DB column names
            field_map = {
                "start": "STARTTIME", "end": "ENDTIME",
                "title": "TITLE", "location": "LOCATION",
                "text": "TEXT", "priority": "PRIORITY",
                "allday": "ALLDAY", "tags": "TAGS",
            }
            mapped = {field_map.get(k, k): v for k, v in data.items()}
            ok = db.update_task(entity_id, **mapped)
        elif entity == "contact":
            # Flache Felder ({"Telefon": ...}) wie beim Create extrahieren —
            # vorher ignorierte der Update-Pfad sie kommentarlos.
            ok = db.update_contact(
                entity_id,
                name=data.get("name"),
                fields=data.get("fields") or _extract_contact_fields(data),
                tags=data.get("tags"),
            )
        elif entity == "note":
            ok = db.update_note(entity_id, title=data.get("title"), tags=data.get("tags"))
        elif entity == "note_tab":
            ok = db.update_note_tab(entity_id, name=data.get("name"), text=data.get("text"))
        elif entity == "todo":
            # Same LLM→column mapping as task, otherwise 'start'/'end' etc. are
            # silently dropped (the DB allowlist only knows STARTTIME/ENDTIME).
            todo_field_map = {
                "start": "STARTTIME", "end": "ENDTIME",
                "title": "TITLE", "text": "TEXT",
                "priority": "PRIORITY", "tags": "TAGS",
            }
            mapped = {todo_field_map.get(k, k): v for k, v in data.items()}
            ok = db.update_todo(entity_id, **mapped)
        else:  # password
            ok = db.update_password(
                entity_id,
                subject=data.get("subject"),
                fields=data.get("fields"),
                tags=data.get("tags"),
            )

        status = "updated" if ok else "not found or unchanged"
        log_message(f"{'✅' if ok else '❌'} epim_update: {entity_type} {entity_id} → {status}")
        return json.dumps({"success": ok, "id": entity_id, "status": status})

    # ----------------------------------------------------------
    # epim_delete
    # ----------------------------------------------------------
    async def _epim_delete(entity_type: str, entity_id: int | str) -> str:
        """Soft-delete an EPIM entry."""
        from ....lib.logging_utils import log_message
        entity_id = int(entity_id)  # Accept string IDs from LLM
        log_message(f"🗓️ epim_delete: {entity_type} id={entity_id}")

        entity = _resolve_entity(entity_type)
        if entity not in ("task", "contact", "note", "todo", "password"):
            return json.dumps({"error": f"Unknown entity_type for delete: {entity_type}. Use: task, contact, note, todo, password"})

        # Symmetrisch zu create/update: Passwort-Operationen nur mit
        # anwesendem User (Browser), nicht von externen Kanälen.
        if entity == "password" and source != "browser":
            return json.dumps({"error": _PASSWORD_WRITE_REFUSED})

        if entity == "task":
            ok = db.delete_task(entity_id)
        elif entity == "contact":
            ok = db.delete_contact(entity_id)
        elif entity == "note":
            ok = db.delete_note(entity_id)
        elif entity == "todo":
            ok = db.delete_todo(entity_id)
        else:  # password
            ok = db.delete_password(entity_id)

        status = "deleted" if ok else "not found"
        log_message(f"{'✅' if ok else '❌'} epim_delete: {entity_type} {entity_id} → {status}")
        return json.dumps({"success": ok, "id": entity_id, "status": status})

    # ----------------------------------------------------------
    # Tool definitions — Descriptions aus prompts/tools/ beim Plugin
    # (Standard-Konvention load_tool_description, nur EN, fail-loud)
    # ----------------------------------------------------------
    from ....lib.plugin_base import load_tool_description
    search_desc = load_tool_description(__file__, "epim_search")
    get_desc = load_tool_description(__file__, "epim_get")
    create_desc = load_tool_description(__file__, "epim_create")
    update_desc = load_tool_description(__file__, "epim_update")
    delete_desc = load_tool_description(__file__, "epim_delete")

    return [
        Tool(
            name="epim_search",
            tier=TIER_READONLY,
            description=search_desc,
            parameters={
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "description": "Entity type: tasks, contacts, notes, todos, passwords, categories, calendar_list, todolists, notetrees",
                    },
                    "query": {
                        "type": "string",
                        "description": "Search text (title, name, subject)",
                    },
                    "date_from": {
                        "type": "string",
                        "description": "Start date filter (YYYY-MM-DD), only for tasks",
                    },
                    "date_to": {
                        "type": "string",
                        "description": "End date filter (YYYY-MM-DD), only for tasks",
                    },
                    "completed": {
                        "type": "boolean",
                        "description": "Filter by completion status, only for todos",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default: 20)",
                    },
                },
                "required": ["entity_type"],
            },
            executor=_epim_search,
        ),
        Tool(
            name="epim_get",
            tier=TIER_READONLY,
            description=get_desc,
            parameters={
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "description": "Entity type: task, contact, note",
                    },
                    "entity_id": {
                        "type": "string",
                        "description": "IDTASK/IDCONTACT/IDNOTE from epim_search results. IMPORTANT: Copy the FULL ID string exactly as returned — do not shorten or round it!",
                    },
                },
                "required": ["entity_type", "entity_id"],
            },
            executor=_epim_get,
        ),
        Tool(
            name="epim_create",
            tier=TIER_WRITE_DATA,
            description=create_desc,
            parameters={
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "description": "Entity type: task, contact, note, todo, password",
                    },
                    "data": {
                        "type": "object",
                        "description": (
                            "Entity data. Task: {title, start, end, location, allday, text, priority, tags}. "
                            "Contact: {name, fields: {Vorname, Nachname, Telefon, E-Mail, ...}, tags}. "
                            "Note: {title, text, tab_name, tree_id, tags}. "
                            "Todo: {title, start, end, priority, text, tags, list_id}. "
                            "Password: {subject, fields: {key: value, ...}, tags, group_id}."
                        ),
                    },
                },
                "required": ["entity_type", "data"],
            },
            executor=_epim_create,
        ),
        Tool(
            name="epim_update",
            tier=TIER_WRITE_DATA,
            description=update_desc,
            parameters={
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "description": "Entity type: task, contact, note, note_tab, todo, password",
                    },
                    "entity_id": {
                        "type": "string",
                        "description": "IDTASK/IDCONTACT etc. from epim_search results. IMPORTANT: Copy the FULL ID string exactly as returned — do not shorten or round it!",
                    },
                    "data": {
                        "type": "object",
                        "description": "Fields to update. Task: {start, end, title, location, text, priority, tags}. Use datetime format: YYYY-MM-DD HH:MM",
                    },
                },
                "required": ["entity_type", "entity_id", "data"],
            },
            executor=_epim_update,
        ),
        Tool(
            name="epim_delete",
            tier=TIER_WRITE_SYSTEM,
            description=delete_desc,
            parameters={
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "description": "Entity type: task, contact, note, todo, password",
                    },
                    "entity_id": {
                        "type": "string",
                        "description": "IDTASK/IDCONTACT etc. from epim_search results. IMPORTANT: Copy the FULL ID string exactly!",
                    },
                },
                "required": ["entity_type", "entity_id"],
            },
            executor=_epim_delete,
        ),
    ]
