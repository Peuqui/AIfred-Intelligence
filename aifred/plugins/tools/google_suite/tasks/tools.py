"""Google Tasks tools — CRUD für Aufgaben via Tasks API v1."""

from __future__ import annotations

import json
from typing import Any

from .....lib.function_calling import Tool
from .....lib.plugin_base import load_tool_description, load_tool_parameters
from .....lib.security import TIER_WRITE_DATA, TIER_WRITE_SYSTEM
from .._common import PLUGIN_DIR, _google_request

TASKS_API = "https://tasks.googleapis.com/tasks/v1"


def get_tasks_tools() -> list[Tool]:
    async def list_tasklists() -> str:
        """Alle Task-Listen des Nutzers auflisten."""
        r = await _google_request("GET", f"{TASKS_API}/users/@me/lists")
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
        r = await _google_request(
            "GET",
            f"{TASKS_API}/lists/{tasklist_id}/tasks",
            params={
                "showCompleted": str(show_completed).lower(),
                "maxResults": max_results,
            },
        )
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
        body: dict[str, Any] = {"title": title}
        if notes:
            body["notes"] = notes
        if due:
            body["due"] = due
        r = await _google_request(
            "POST", f"{TASKS_API}/lists/{tasklist_id}/tasks", json=body,
        )
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
        # Erst aktuellen Stand laden (PUT braucht vollständiges Objekt)
        r = await _google_request(
            "GET", f"{TASKS_API}/lists/{tasklist_id}/tasks/{task_id}",
        )
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

        await _google_request(
            "PUT", f"{TASKS_API}/lists/{tasklist_id}/tasks/{task_id}", json=task,
        )
        return json.dumps({"id": task_id, "updated": True}, ensure_ascii=False)

    async def complete_task(task_id: str, tasklist_id: str = "@default") -> str:
        """Aufgabe als erledigt markieren."""
        return await update_task(task_id, tasklist_id, status="completed")

    async def delete_task(task_id: str, tasklist_id: str = "@default") -> str:
        """Aufgabe löschen."""
        await _google_request(
            "DELETE", f"{TASKS_API}/lists/{tasklist_id}/tasks/{task_id}",
        )
        return json.dumps({"id": task_id, "deleted": True}, ensure_ascii=False)

    return [
        Tool(
            name="google_tasks_list_tasklists",
            description=load_tool_description(PLUGIN_DIR, "google_tasks_list_tasklists"),
            parameters=load_tool_parameters(PLUGIN_DIR, "google_tasks_list_tasklists"),
            executor=list_tasklists,
            tier=TIER_WRITE_DATA,  # reads private task data → block external channels
        ),
        Tool(
            name="google_tasks_list",
            description=load_tool_description(PLUGIN_DIR, "google_tasks_list"),
            parameters=load_tool_parameters(PLUGIN_DIR, "google_tasks_list"),
            executor=list_tasks,
            tier=TIER_WRITE_DATA,  # reads private task data → block external channels
        ),
        Tool(
            name="google_tasks_create",
            description=load_tool_description(PLUGIN_DIR, "google_tasks_create"),
            parameters=load_tool_parameters(PLUGIN_DIR, "google_tasks_create"),
            executor=create_task,
            tier=TIER_WRITE_DATA,
        ),
        Tool(
            name="google_tasks_update",
            description=load_tool_description(PLUGIN_DIR, "google_tasks_update"),
            parameters=load_tool_parameters(PLUGIN_DIR, "google_tasks_update"),
            executor=update_task,
            tier=TIER_WRITE_DATA,
        ),
        Tool(
            name="google_tasks_complete",
            description=load_tool_description(PLUGIN_DIR, "google_tasks_complete"),
            parameters=load_tool_parameters(PLUGIN_DIR, "google_tasks_complete"),
            executor=complete_task,
            tier=TIER_WRITE_DATA,
        ),
        Tool(
            name="google_tasks_delete",
            description=load_tool_description(PLUGIN_DIR, "google_tasks_delete"),
            parameters=load_tool_parameters(PLUGIN_DIR, "google_tasks_delete"),
            executor=delete_task,
            # Repo-Konvention (lib/security.py): Deletes sind TIER_WRITE_SYSTEM
            tier=TIER_WRITE_SYSTEM,
        ),
    ]
