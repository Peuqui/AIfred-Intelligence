"""Google Tasks tools — CRUD für Aufgaben via Tasks API v1."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .....lib.function_calling import Tool
from .....lib.security import TIER_READONLY, TIER_WRITE_DATA

logger = logging.getLogger(__name__)

TASKS_API = "https://tasks.googleapis.com/tasks/v1"


async def _get_token() -> str:
    from .....lib.oauth.broker import oauth_broker
    token = await oauth_broker.get_token("google")
    if not token:
        raise RuntimeError("Google nicht verbunden. Bitte erst in den Einstellungen autorisieren.")
    return token


def get_tasks_tools(lang: str = "de") -> list[Tool]:
    async def list_tasklists() -> str:
        """Alle Task-Listen des Nutzers auflisten."""
        token = await _get_token()
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{TASKS_API}/users/@me/lists",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            r.raise_for_status()
        items = r.json().get("items", [])
        return json.dumps(
            [{"id": t["id"], "title": t.get("title", "")} for t in items],
            ensure_ascii=False,
        )

    async def list_tasks(
        tasklist_id: str = "@default",
        show_completed: bool = False,
        max_results: int = 50,
    ) -> str:
        """Aufgaben einer Task-Liste abrufen."""
        token = await _get_token()
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{TASKS_API}/lists/{tasklist_id}/tasks",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "showCompleted": str(show_completed).lower(),
                    "maxResults": max_results,
                },
                timeout=15,
            )
            r.raise_for_status()
        items = r.json().get("items", [])
        result = []
        for t in items:
            result.append({
                "id": t.get("id"),
                "title": t.get("title", ""),
                "notes": t.get("notes"),
                "due": t.get("due"),
                "status": t.get("status"),
                "completed": t.get("completed"),
            })
        return json.dumps(result, ensure_ascii=False)

    async def create_task(
        title: str,
        notes: str = "",
        due: str = "",
        tasklist_id: str = "@default",
    ) -> str:
        """Neue Aufgabe erstellen. due im RFC 3339 Format (z.B. 2026-04-22T00:00:00Z)."""
        token = await _get_token()
        body: dict[str, Any] = {"title": title}
        if notes:
            body["notes"] = notes
        if due:
            body["due"] = due
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{TASKS_API}/lists/{tasklist_id}/tasks",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
                timeout=15,
            )
            r.raise_for_status()
        t = r.json()
        return json.dumps({"id": t.get("id"), "title": t.get("title")}, ensure_ascii=False)

    async def update_task(
        task_id: str,
        tasklist_id: str = "@default",
        title: str = "",
        notes: str = "",
        due: str = "",
        status: str = "",
    ) -> str:
        """Aufgabe aktualisieren. status: 'needsAction' oder 'completed'."""
        token = await _get_token()
        # Erst aktuellen Stand laden (PUT braucht vollständiges Objekt)
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{TASKS_API}/lists/{tasklist_id}/tasks/{task_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            r.raise_for_status()
            task = r.json()

        if title:
            task["title"] = title
        if notes:
            task["notes"] = notes
        if due:
            task["due"] = due
        if status:
            task["status"] = status
            if status == "completed" and "completed" not in task:
                from datetime import datetime, timezone
                task["completed"] = datetime.now(timezone.utc).isoformat()
            elif status == "needsAction":
                task.pop("completed", None)

        async with httpx.AsyncClient() as client:
            r = await client.put(
                f"{TASKS_API}/lists/{tasklist_id}/tasks/{task_id}",
                headers={"Authorization": f"Bearer {token}"},
                json=task,
                timeout=15,
            )
            r.raise_for_status()
        return json.dumps({"id": task_id, "updated": True}, ensure_ascii=False)

    async def complete_task(task_id: str, tasklist_id: str = "@default") -> str:
        """Aufgabe als erledigt markieren."""
        return await update_task(task_id, tasklist_id, status="completed")

    async def delete_task(task_id: str, tasklist_id: str = "@default") -> str:
        """Aufgabe löschen."""
        token = await _get_token()
        async with httpx.AsyncClient() as client:
            r = await client.delete(
                f"{TASKS_API}/lists/{tasklist_id}/tasks/{task_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            r.raise_for_status()
        return json.dumps({"id": task_id, "deleted": True}, ensure_ascii=False)

    return [
        Tool(
            name="google_tasks_list_tasklists",
            description="Listet alle Google Task-Listen des Nutzers auf.",
            parameters={"type": "object", "properties": {}, "required": []},
            executor=list_tasklists,
            tier=TIER_READONLY,
        ),
        Tool(
            name="google_tasks_list",
            description="Aufgaben einer Task-Liste abrufen.",
            parameters={
                "type": "object",
                "properties": {
                    "tasklist_id":     {"type": "string", "description": "Task-Listen-ID (Standard: @default)"},
                    "show_completed":  {"type": "boolean", "description": "Erledigte Aufgaben einschließen (Standard: false)"},
                    "max_results":     {"type": "integer", "description": "Max. Anzahl Ergebnisse (Standard: 50)"},
                },
                "required": [],
            },
            executor=list_tasks,
            tier=TIER_READONLY,
        ),
        Tool(
            name="google_tasks_create",
            description="Neue Aufgabe in Google Tasks erstellen.",
            parameters={
                "type": "object",
                "properties": {
                    "title":        {"type": "string", "description": "Titel der Aufgabe"},
                    "notes":        {"type": "string", "description": "Notizen/Beschreibung (optional)"},
                    "due":          {"type": "string", "description": "Fälligkeitsdatum RFC 3339 (optional)"},
                    "tasklist_id":  {"type": "string", "description": "Task-Listen-ID (Standard: @default)"},
                },
                "required": ["title"],
            },
            executor=create_task,
            tier=TIER_WRITE_DATA,
        ),
        Tool(
            name="google_tasks_update",
            description="Aufgabe aktualisieren. Nur angegebene Felder werden geändert.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id":      {"type": "string", "description": "ID der Aufgabe"},
                    "tasklist_id":  {"type": "string", "description": "Task-Listen-ID (Standard: @default)"},
                    "title":        {"type": "string", "description": "Neuer Titel (optional)"},
                    "notes":        {"type": "string", "description": "Neue Notizen (optional)"},
                    "due":          {"type": "string", "description": "Neues Fälligkeitsdatum RFC 3339 (optional)"},
                    "status":       {"type": "string", "description": "'needsAction' oder 'completed' (optional)"},
                },
                "required": ["task_id"],
            },
            executor=update_task,
            tier=TIER_WRITE_DATA,
        ),
        Tool(
            name="google_tasks_complete",
            description="Aufgabe als erledigt markieren.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id":     {"type": "string", "description": "ID der Aufgabe"},
                    "tasklist_id": {"type": "string", "description": "Task-Listen-ID (Standard: @default)"},
                },
                "required": ["task_id"],
            },
            executor=complete_task,
            tier=TIER_WRITE_DATA,
        ),
        Tool(
            name="google_tasks_delete",
            description="Aufgabe aus Google Tasks löschen.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id":     {"type": "string", "description": "ID der zu löschenden Aufgabe"},
                    "tasklist_id": {"type": "string", "description": "Task-Listen-ID (Standard: @default)"},
                },
                "required": ["task_id"],
            },
            executor=delete_task,
            tier=TIER_WRITE_DATA,
        ),
    ]
