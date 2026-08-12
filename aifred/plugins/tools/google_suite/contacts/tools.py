"""Google Contacts tools — vollständiges CRUD + Gruppen via People API v1."""

from __future__ import annotations

import json

from .....lib.function_calling import Tool
from .....lib.plugin_base import load_tool_description
from .....lib.security import TIER_WRITE_DATA, TIER_WRITE_SYSTEM
from .._common import PLUGIN_DIR, _google_request

PEOPLE_API = "https://people.googleapis.com/v1"
PERSON_FIELDS = "names,emailAddresses,phoneNumbers,organizations,biographies,memberships"
GROUPS_API = "https://people.googleapis.com/v1/contactGroups"

# Google-API-Limits (HTTP 400 bei Überschreitung — deshalb clampen)
_BATCH_GET_MAX = 200        # people:batchGet resourceNames
_SEARCH_PAGE_MAX = 30       # people:searchContacts pageSize


def _format_person(p: dict) -> dict:
    """Relevante Felder aus einer Person-Ressource extrahieren."""
    names = p.get("names", [])
    emails = p.get("emailAddresses", [])
    phones = p.get("phoneNumbers", [])
    orgs = p.get("organizations", [])
    groups = [
        m.get("contactGroupMembership", {}).get("contactGroupResourceName")
        for m in p.get("memberships", [])
        if "contactGroupMembership" in m
    ]
    return {
        "resource_name": p.get("resourceName"),
        "name": names[0].get("displayName") if names else None,
        "emails": [e.get("value") for e in emails],
        "phones": [ph.get("value") for ph in phones],
        "organization": orgs[0].get("name") if orgs else None,
        "groups": groups,
    }


def _split_display_name(display_name: str) -> dict[str, str]:
    """People API: Name.displayName ist OUTPUT_ONLY — Google leitet den Wert
    aus givenName/familyName ab. Split beim letzten Whitespace, damit z.B.
    "Max Mustermann" → given="Max", family="Mustermann" wird; einteilige
    Namen landen komplett in givenName. (SSOT für create UND update.)"""
    if " " in display_name.strip():
        given, family = display_name.rsplit(" ", 1)
        return {"givenName": given.strip(), "familyName": family.strip()}
    return {"givenName": display_name.strip()}


async def _resolve_group_resource_name(group_name: str) -> str:
    """Gruppenname → resourceName (z.B. 'contactGroups/abc123')."""
    r = await _google_request("GET", GROUPS_API, params={"pageSize": 200})
    groups = r.json().get("contactGroups", [])
    # Exakter Name zuerst, dann case-insensitive
    for g in groups:
        if g.get("name") == group_name:
            return str(g["resourceName"])
    for g in groups:
        if g.get("name", "").lower() == group_name.lower():
            return str(g["resourceName"])
    raise ValueError(f"Contact group '{group_name}' not found.")


def get_contacts_tools() -> list[Tool]:

    async def list_all_contacts(max_results: int = 500) -> str:
        """Alle Kontakte abrufen (paginiert, max. max_results)."""
        contacts: list[dict] = []
        page_token: str | None = None
        while len(contacts) < max_results:
            params: dict = {
                "personFields": PERSON_FIELDS,
                "pageSize": min(100, max_results - len(contacts)),
                "sortOrder": "LAST_MODIFIED_DESCENDING",
            }
            if page_token:
                params["pageToken"] = page_token
            r = await _google_request(
                "GET", f"{PEOPLE_API}/people/me/connections", params=params,
            )
            data = r.json()
            for person in data.get("connections", []):
                contacts.append(_format_person(person))
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return json.dumps(contacts, ensure_ascii=False)

    async def list_groups() -> str:
        """Alle Kontaktgruppen/Labels auflisten."""
        r = await _google_request("GET", GROUPS_API, params={"pageSize": 200})
        groups = r.json().get("contactGroups", [])
        result = [
            {
                "resource_name": g.get("resourceName"),
                "name": g.get("name"),
                "member_count": g.get("memberCount", 0),
                "type": g.get("groupType"),
            }
            for g in groups
        ]
        return json.dumps(result, ensure_ascii=False)

    async def list_by_group(group_name: str, max_members: int = 200) -> str:
        """Alle Kontakte einer Gruppe/eines Labels abrufen."""
        max_members = max(1, min(max_members, _BATCH_GET_MAX))
        group_rn = await _resolve_group_resource_name(group_name)

        # Gruppe mit Member-Ressourcennamen laden
        r = await _google_request(
            "GET", f"{PEOPLE_API}/{group_rn}", params={"maxMembers": max_members},
        )
        member_rns = r.json().get("memberResourceNames", [])
        if not member_rns:
            return json.dumps([], ensure_ascii=False)

        # Batch-GET für alle Mitglieder (API-Limit: max 200 resourceNames)
        r = await _google_request(
            "GET",
            f"{PEOPLE_API}/people:batchGet",
            params={
                "resourceNames": member_rns[:_BATCH_GET_MAX],
                "personFields": PERSON_FIELDS,
            },
        )
        responses = r.json().get("responses", [])
        persons = [_format_person(resp["person"]) for resp in responses if "person" in resp]
        return json.dumps(persons, ensure_ascii=False)

    async def search_contacts(query: str, max_results: int = 10) -> str:
        """Kontakte nach Name oder E-Mail durchsuchen."""
        r = await _google_request(
            "GET",
            f"{PEOPLE_API}/people:searchContacts",
            params={
                "query": query,
                "readMask": PERSON_FIELDS,
                # API-Limit: pageSize max 30
                "pageSize": max(1, min(max_results, _SEARCH_PAGE_MAX)),
            },
        )
        results = r.json().get("results", [])
        persons = [_format_person(res["person"]) for res in results if "person" in res]
        return json.dumps(persons, ensure_ascii=False)

    async def create_contact(
        display_name: str,
        email: str = "",
        phone: str = "",
        organization: str = "",
        notes: str = "",
        group: str = "",
    ) -> str:
        """Neuen Kontakt anlegen. group ist ein optionaler Gruppenname."""
        body: dict = {"names": [_split_display_name(display_name)]}
        if email:
            body["emailAddresses"] = [{"value": email}]
        if phone:
            body["phoneNumbers"] = [{"value": phone}]
        if organization:
            body["organizations"] = [{"name": organization}]
        if notes:
            body["biographies"] = [{"value": notes, "contentType": "TEXT_PLAIN"}]

        r = await _google_request(
            "POST", f"{PEOPLE_API}/people:createContact", json=body,
        )
        p = r.json()
        person_rn = p.get("resourceName", "")

        if group and person_rn:
            group_rn = await _resolve_group_resource_name(group)
            await _google_request(
                "POST",
                f"{PEOPLE_API}/{group_rn}/members:modify",
                json={"resourceNamesToAdd": [person_rn]},
            )

        return json.dumps(_format_person(p), ensure_ascii=False)

    async def update_contact(
        resource_name: str,
        display_name: str = "",
        email: str = "",
        phone: str = "",
        organization: str = "",
        notes: str = "",
        group: str = "",
    ) -> str:
        """Kontakt aktualisieren. Nur gesetzte Felder werden überschrieben."""
        r = await _google_request(
            "GET", f"{PEOPLE_API}/{resource_name}",
            params={"personFields": PERSON_FIELDS},
        )
        current = r.json()

        etag = current.get("etag", "")
        patch: dict = {"etag": etag, "resourceName": resource_name}
        update_fields: list[str] = []

        if display_name:
            patch["names"] = [_split_display_name(display_name)]
            update_fields.append("names")
        if email:
            patch["emailAddresses"] = [{"value": email}]
            update_fields.append("emailAddresses")
        if phone:
            patch["phoneNumbers"] = [{"value": phone}]
            update_fields.append("phoneNumbers")
        if organization:
            patch["organizations"] = [{"name": organization}]
            update_fields.append("organizations")
        if notes:
            patch["biographies"] = [{"value": notes, "contentType": "TEXT_PLAIN"}]
            update_fields.append("biographies")

        if update_fields:
            await _google_request(
                "PATCH",
                f"{PEOPLE_API}/{resource_name}:updateContact",
                params={"updatePersonFields": ",".join(update_fields)},
                json=patch,
            )

        if group:
            group_rn = await _resolve_group_resource_name(group)
            await _google_request(
                "POST",
                f"{PEOPLE_API}/{group_rn}/members:modify",
                json={"resourceNamesToAdd": [resource_name]},
            )

        return json.dumps({"resource_name": resource_name, "updated": True}, ensure_ascii=False)

    async def delete_contact(resource_name: str) -> str:
        """Kontakt löschen."""
        await _google_request(
            "DELETE", f"{PEOPLE_API}/{resource_name}:deleteContact",
        )
        return json.dumps({"resource_name": resource_name, "deleted": True}, ensure_ascii=False)

    return [
        Tool(
            name="google_contacts_list_all",
            description=load_tool_description(PLUGIN_DIR, "google_contacts_list_all"),
            parameters={
                "type": "object",
                "properties": {
                    "max_results": {"type": "integer", "description": "Maximum number of contacts (default: 500)"},
                },
                "required": [],
            },
            executor=list_all_contacts,
            tier=TIER_WRITE_DATA,  # reads private contact data → block external channels
        ),
        Tool(
            name="google_contacts_list_groups",
            description=load_tool_description(PLUGIN_DIR, "google_contacts_list_groups"),
            parameters={"type": "object", "properties": {}, "required": []},
            executor=list_groups,
            tier=TIER_WRITE_DATA,  # reads private contact data → block external channels
        ),
        Tool(
            name="google_contacts_list_by_group",
            description=load_tool_description(PLUGIN_DIR, "google_contacts_list_by_group"),
            parameters={
                "type": "object",
                "properties": {
                    "group_name":  {"type": "string", "description": "Group name (e.g. 'Familie', 'Arbeit')"},
                    "max_members": {"type": "integer", "description": "Maximum members (default and API limit: 200)"},
                },
                "required": ["group_name"],
            },
            executor=list_by_group,
            tier=TIER_WRITE_DATA,  # reads private contact data → block external channels
        ),
        Tool(
            name="google_contacts_search",
            description=(
                load_tool_description(PLUGIN_DIR, "google_contacts_search")
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query":       {"type": "string", "description": "Search term (name or e-mail)"},
                    "max_results": {"type": "integer", "description": "Maximum hits (default: 10, API limit: 30)"},
                },
                "required": ["query"],
            },
            executor=search_contacts,
            tier=TIER_WRITE_DATA,  # reads private contact data → block external channels
        ),
        Tool(
            name="google_contacts_create",
            description=load_tool_description(PLUGIN_DIR, "google_contacts_create"),
            parameters={
                "type": "object",
                "properties": {
                    "display_name": {"type": "string", "description": "Full name"},
                    "email":        {"type": "string", "description": "E-mail address (optional)"},
                    "phone":        {"type": "string", "description": "Phone number (optional)"},
                    "organization": {"type": "string", "description": "Company / organization (optional)"},
                    "notes":        {"type": "string", "description": "Notes (optional)"},
                    "group":        {"type": "string", "description": "Group name (optional, e.g. 'Familie')"},
                },
                "required": ["display_name"],
            },
            executor=create_contact,
            tier=TIER_WRITE_DATA,
        ),
        Tool(
            name="google_contacts_update",
            description=load_tool_description(PLUGIN_DIR, "google_contacts_update"),
            parameters={
                "type": "object",
                "properties": {
                    "resource_name": {"type": "string", "description": "Resource name (from google_contacts_search)"},
                    "display_name":  {"type": "string", "description": "New name (optional)"},
                    "email":         {"type": "string", "description": "New e-mail (optional)"},
                    "phone":         {"type": "string", "description": "New phone number (optional)"},
                    "organization":  {"type": "string", "description": "New organization (optional)"},
                    "notes":         {"type": "string", "description": "New notes (optional)"},
                    "group":         {"type": "string", "description": "Assign to group (optional)"},
                },
                "required": ["resource_name"],
            },
            executor=update_contact,
            tier=TIER_WRITE_DATA,
        ),
        Tool(
            name="google_contacts_delete",
            description=load_tool_description(PLUGIN_DIR, "google_contacts_delete"),
            parameters={
                "type": "object",
                "properties": {
                    "resource_name": {"type": "string", "description": "Resource name of the contact"},
                },
                "required": ["resource_name"],
            },
            executor=delete_contact,
            # Repo-Konvention (lib/security.py): Deletes sind TIER_WRITE_SYSTEM
            tier=TIER_WRITE_SYSTEM,
        ),
    ]
