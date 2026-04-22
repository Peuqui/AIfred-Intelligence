"""Google Calendar tools — CRUD für Termine via Calendar API v3."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .....lib.function_calling import Tool
from .....lib.security import TIER_READONLY, TIER_WRITE_DATA

logger = logging.getLogger(__name__)

CALENDAR_API = "https://www.googleapis.com/calendar/v3"


async def _get_token() -> str:
    from .....lib.oauth.broker import oauth_broker
    token = await oauth_broker.get_token("google")
    if not token:
        raise RuntimeError("Google nicht verbunden. Bitte erst in den Einstellungen autorisieren.")
    return token


def get_calendar_tools(lang: str = "de") -> list[Tool]:
    async def list_events(
        start: str,
        end: str,
        calendar_id: str = "primary",
        max_results: int = 20,
    ) -> str:
        """Termine zwischen start und end abrufen (ISO 8601)."""
        token = await _get_token()
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{CALENDAR_API}/calendars/{calendar_id}/events",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "timeMin": start,
                    "timeMax": end,
                    "maxResults": max_results,
                    "singleEvents": "true",
                    "orderBy": "startTime",
                },
                timeout=15,
            )
            r.raise_for_status()
        items = r.json().get("items", [])
        result = []
        for ev in items:
            result.append({
                "id": ev.get("id"),
                "title": ev.get("summary", "(kein Titel)"),
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
        token = await _get_token()
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
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{CALENDAR_API}/calendars/{calendar_id}/events",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
                timeout=15,
            )
            r.raise_for_status()
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
        """Bestehenden Termin ändern. Nur gesetzte Felder werden überschrieben."""
        token = await _get_token()
        # Erst den aktuellen Stand laden (PATCH braucht nur geänderte Felder)
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{CALENDAR_API}/calendars/{calendar_id}/events/{event_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            r.raise_for_status()

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

        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{CALENDAR_API}/calendars/{calendar_id}/events/{event_id}",
                headers={"Authorization": f"Bearer {token}"},
                json=patch,
                timeout=15,
            )
            r.raise_for_status()
        return json.dumps({"id": event_id, "updated": True}, ensure_ascii=False)

    async def delete_event(event_id: str, calendar_id: str = "primary") -> str:
        """Termin löschen."""
        token = await _get_token()
        async with httpx.AsyncClient() as client:
            r = await client.delete(
                f"{CALENDAR_API}/calendars/{calendar_id}/events/{event_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            r.raise_for_status()
        return json.dumps({"id": event_id, "deleted": True}, ensure_ascii=False)

    async def list_calendars() -> str:
        """Alle verfügbaren Kalender auflisten."""
        token = await _get_token()
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{CALENDAR_API}/users/me/calendarList",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            r.raise_for_status()
        items = r.json().get("items", [])
        result = [{"id": c.get("id"), "name": c.get("summary"), "primary": c.get("primary", False)} for c in items]
        return json.dumps(result, ensure_ascii=False)

    return [
        Tool(
            name="google_calendar_list_events",
            description=(
                "Listet Google-Kalender-Termine in einem Zeitraum auf. "
                "start und end müssen RFC 3339 sein, z.B. 2026-04-22T00:00:00Z."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "start":       {"type": "string", "description": "Startzeitpunkt (RFC 3339)"},
                    "end":         {"type": "string", "description": "Endzeitpunkt (RFC 3339)"},
                    "calendar_id": {"type": "string", "description": "Kalender-ID (Standard: primary)"},
                    "max_results": {"type": "integer", "description": "Max. Anzahl Ergebnisse (Standard: 20)"},
                },
                "required": ["start", "end"],
            },
            executor=list_events,
            tier=TIER_READONLY,
        ),
        Tool(
            name="google_calendar_create_event",
            description="Erstellt einen neuen Termin im Google Kalender.",
            parameters={
                "type": "object",
                "properties": {
                    "title":       {"type": "string", "description": "Titel des Termins"},
                    "start":       {"type": "string", "description": "Startzeit (RFC 3339)"},
                    "end":         {"type": "string", "description": "Endzeit (RFC 3339)"},
                    "calendar_id": {"type": "string", "description": "Kalender-ID (Standard: primary)"},
                    "description": {"type": "string", "description": "Beschreibung (optional)"},
                    "location":    {"type": "string", "description": "Ort (optional)"},
                    "attendees":   {"type": "string", "description": "Kommagetrennte E-Mail-Adressen (optional)"},
                },
                "required": ["title", "start", "end"],
            },
            executor=create_event,
            tier=TIER_WRITE_DATA,
        ),
        Tool(
            name="google_calendar_update_event",
            description="Ändert einen bestehenden Termin. Nur angegebene Felder werden überschrieben.",
            parameters={
                "type": "object",
                "properties": {
                    "event_id":    {"type": "string", "description": "ID des Termins"},
                    "calendar_id": {"type": "string", "description": "Kalender-ID (Standard: primary)"},
                    "title":       {"type": "string", "description": "Neuer Titel (optional)"},
                    "start":       {"type": "string", "description": "Neue Startzeit RFC 3339 (optional)"},
                    "end":         {"type": "string", "description": "Neue Endzeit RFC 3339 (optional)"},
                    "description": {"type": "string", "description": "Neue Beschreibung (optional)"},
                    "location":    {"type": "string", "description": "Neuer Ort (optional)"},
                },
                "required": ["event_id"],
            },
            executor=update_event,
            tier=TIER_WRITE_DATA,
        ),
        Tool(
            name="google_calendar_delete_event",
            description="Löscht einen Termin aus dem Google Kalender.",
            parameters={
                "type": "object",
                "properties": {
                    "event_id":    {"type": "string", "description": "ID des zu löschenden Termins"},
                    "calendar_id": {"type": "string", "description": "Kalender-ID (Standard: primary)"},
                },
                "required": ["event_id"],
            },
            executor=delete_event,
            tier=TIER_WRITE_DATA,
        ),
        Tool(
            name="google_calendar_list_calendars",
            description="Listet alle verfügbaren Google-Kalender des Nutzers auf.",
            parameters={"type": "object", "properties": {}, "required": []},
            executor=list_calendars,
            tier=TIER_READONLY,
        ),
    ]
