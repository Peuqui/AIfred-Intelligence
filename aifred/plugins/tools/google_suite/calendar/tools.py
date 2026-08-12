"""Google Calendar tools — CRUD für Termine via Calendar API v3."""

from __future__ import annotations

import json
from typing import Any

from .....lib.function_calling import Tool
from .....lib.plugin_base import load_tool_description
from .....lib.security import TIER_WRITE_DATA, TIER_WRITE_SYSTEM
from .._common import PLUGIN_DIR, _google_request

CALENDAR_API = "https://www.googleapis.com/calendar/v3"


def get_calendar_tools() -> list[Tool]:
    async def list_events(
        start: str,
        end: str,
        calendar_id: str = "primary",
        max_results: int = 20,
    ) -> str:
        """Termine zwischen start und end abrufen (ISO 8601)."""
        r = await _google_request(
            "GET",
            f"{CALENDAR_API}/calendars/{calendar_id}/events",
            params={
                "timeMin": start,
                "timeMax": end,
                "maxResults": max_results,
                "singleEvents": "true",
                "orderBy": "startTime",
            },
        )
        items = r.json().get("items", [])
        result = []
        for ev in items:
            result.append({
                "id": ev.get("id"),
                "title": ev.get("summary", "(no title)"),
                "start": ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date"),
                "end": ev.get("end", {}).get("dateTime") or ev.get("end", {}).get("date"),
                "location": ev.get("location"),
                "description": ev.get("description"),
                "attendees": [a.get("email") for a in ev.get("attendees", [])],
            })
        return json.dumps(result, ensure_ascii=False)

    async def create_event(
        title: str,
        start: str,
        end: str,
        calendar_id: str = "primary",
        description: str = "",
        location: str = "",
        attendees: str = "",
    ) -> str:
        """Neuen Termin erstellen. start/end in ISO 8601. attendees als kommagetrennte E-Mails."""
        body: dict[str, Any] = {
            "summary": title,
            "start": {"dateTime": start},
            "end": {"dateTime": end},
        }
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        if attendees:
            body["attendees"] = [{"email": e.strip()} for e in attendees.split(",") if e.strip()]
        r = await _google_request(
            "POST", f"{CALENDAR_API}/calendars/{calendar_id}/events", json=body,
        )
        ev = r.json()
        return json.dumps({"id": ev.get("id"), "title": ev.get("summary"), "link": ev.get("htmlLink")}, ensure_ascii=False)

    async def update_event(
        event_id: str,
        calendar_id: str = "primary",
        title: str = "",
        start: str = "",
        end: str = "",
        description: str = "",
        location: str = "",
    ) -> str:
        """Bestehenden Termin ändern. Nur gesetzte Felder werden überschrieben.

        PATCH braucht nur die geänderten Felder — kein GET-Roundtrip nötig
        (der frühere GET wurde nie ausgewertet, Copy-Paste vom tasks-PUT).
        """
        patch: dict[str, Any] = {}
        if title:
            patch["summary"] = title
        if start:
            patch["start"] = {"dateTime": start}
        if end:
            patch["end"] = {"dateTime": end}
        if description:
            patch["description"] = description
        if location:
            patch["location"] = location

        await _google_request(
            "PATCH",
            f"{CALENDAR_API}/calendars/{calendar_id}/events/{event_id}",
            json=patch,
        )
        return json.dumps({"id": event_id, "updated": True}, ensure_ascii=False)

    async def delete_event(event_id: str, calendar_id: str = "primary") -> str:
        """Termin löschen."""
        await _google_request(
            "DELETE", f"{CALENDAR_API}/calendars/{calendar_id}/events/{event_id}",
        )
        return json.dumps({"id": event_id, "deleted": True}, ensure_ascii=False)

    async def list_calendars() -> str:
        """Alle verfügbaren Kalender auflisten."""
        r = await _google_request("GET", f"{CALENDAR_API}/users/me/calendarList")
        items = r.json().get("items", [])
        result = [{"id": c.get("id"), "name": c.get("summary"), "primary": c.get("primary", False)} for c in items]
        return json.dumps(result, ensure_ascii=False)

    return [
        Tool(
            name="google_calendar_list_events",
            description=(
                load_tool_description(PLUGIN_DIR, "google_calendar_list_events")
            ),
            parameters={
                "type": "object",
                "properties": {
                    "start":       {"type": "string", "description": "Start of the time window (RFC 3339)"},
                    "end":         {"type": "string", "description": "End of the time window (RFC 3339)"},
                    "calendar_id": {"type": "string", "description": "Calendar ID (default: primary)"},
                    "max_results": {"type": "integer", "description": "Maximum number of results (default: 20)"},
                },
                "required": ["start", "end"],
            },
            executor=list_events,
            tier=TIER_WRITE_DATA,  # reads private calendar data → block external channels
        ),
        Tool(
            name="google_calendar_create_event",
            description=load_tool_description(PLUGIN_DIR, "google_calendar_create_event"),
            parameters={
                "type": "object",
                "properties": {
                    "title":       {"type": "string", "description": "Event title"},
                    "start":       {"type": "string", "description": "Start time (RFC 3339)"},
                    "end":         {"type": "string", "description": "End time (RFC 3339)"},
                    "calendar_id": {"type": "string", "description": "Calendar ID (default: primary)"},
                    "description": {"type": "string", "description": "Description (optional)"},
                    "location":    {"type": "string", "description": "Location (optional)"},
                    "attendees":   {"type": "string", "description": "Comma-separated attendee e-mail addresses (optional)"},
                },
                "required": ["title", "start", "end"],
            },
            executor=create_event,
            tier=TIER_WRITE_DATA,
        ),
        Tool(
            name="google_calendar_update_event",
            description=load_tool_description(PLUGIN_DIR, "google_calendar_update_event"),
            parameters={
                "type": "object",
                "properties": {
                    "event_id":    {"type": "string", "description": "ID of the event"},
                    "calendar_id": {"type": "string", "description": "Calendar ID (default: primary)"},
                    "title":       {"type": "string", "description": "New title (optional)"},
                    "start":       {"type": "string", "description": "New start time, RFC 3339 (optional)"},
                    "end":         {"type": "string", "description": "New end time, RFC 3339 (optional)"},
                    "description": {"type": "string", "description": "New description (optional)"},
                    "location":    {"type": "string", "description": "New location (optional)"},
                },
                "required": ["event_id"],
            },
            executor=update_event,
            tier=TIER_WRITE_DATA,
        ),
        Tool(
            name="google_calendar_delete_event",
            description=load_tool_description(PLUGIN_DIR, "google_calendar_delete_event"),
            parameters={
                "type": "object",
                "properties": {
                    "event_id":    {"type": "string", "description": "ID of the event to delete"},
                    "calendar_id": {"type": "string", "description": "Calendar ID (default: primary)"},
                },
                "required": ["event_id"],
            },
            executor=delete_event,
            # Repo-Konvention (lib/security.py): Deletes sind TIER_WRITE_SYSTEM
            tier=TIER_WRITE_SYSTEM,
        ),
        Tool(
            name="google_calendar_list_calendars",
            description=load_tool_description(PLUGIN_DIR, "google_calendar_list_calendars"),
            parameters={"type": "object", "properties": {}, "required": []},
            executor=list_calendars,
            tier=TIER_WRITE_DATA,  # reads private calendar data → block external channels
        ),
    ]
