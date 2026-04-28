"""Google Contacts tools — vollständiges CRUD + Gruppen via People API v1."""

from __future__ import annotations

import json
import logging

import httpx

from .....lib.function_calling import Tool
from .....lib.security import TIER_READONLY, TIER_WRITE_DATA

logger = logging.getLogger(__name__)

PEOPLE_API = "https://people.googleapis.com/v1"
PERSON_FIELDS = "names,emailAddresses,phoneNumbers,organizations,biographies,memberships"
GROUPS_API = "https://people.googleapis.com/v1/contactGroups"


async def _get_token() -> str:
    from .....lib.oauth.broker import oauth_broker
    token = await oauth_broker.get_token("google")
    if not token:
        raise RuntimeError("Google nicht verbunden. Bitte erst in den Einstellungen autorisieren.")
    return token


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


async def _resolve_group_resource_name(token: str, group_name: str) -> str:
    """Gruppenname → resourceName (z.B. 'contactGroups/abc123')."""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            GROUPS_API,
            headers={"Authorization": f"Bearer {token}"},
            params={"pageSize": 200},
            timeout=15,
        )
        r.raise_for_status()
    groups = r.json().get("contactGroups", [])
    # Exakter Name zuerst, dann case-insensitive
    for g in groups:
        if g.get("name") == group_name:
            return g["resourceName"]
    for g in groups:
        if g.get("name", "").lower() == group_name.lower():
            return g["resourceName"]
    raise ValueError(f"Gruppe '{group_name}' nicht gefunden.")


def get_contacts_tools(lang: str = "de") -> list[Tool]:

    async def list_all_contacts(max_results: int = 500) -> str:
        """Alle Kontakte abrufen (paginiert, max. max_results)."""
        token = await _get_token()
        contacts: list[dict] = []
        page_token: str | None = None
        async with httpx.AsyncClient() as client:
            while len(contacts) < max_results:
                params: dict = {
                    "personFields": PERSON_FIELDS,
                    "pageSize": min(100, max_results - len(contacts)),
                    "sortOrder": "LAST_MODIFIED_DESCENDING",
                }
                if page_token:
                    params["pageToken"] = page_token
                r = await client.get(
                    f"{PEOPLE_API}/people/me/connections",
                    headers={"Authorization": f"Bearer {token}"},
                    params=params,
                    timeout=15,
                )
                r.raise_for_status()
                data = r.json()
                for person in data.get("connections", []):
                    contacts.append(_format_person(person))
                page_token = data.get("nextPageToken")
                if not page_token:
                    break
        return json.dumps(contacts, ensure_ascii=False)

    async def list_groups() -> str:
        """Alle Kontaktgruppen/Labels auflisten."""
        token = await _get_token()
        async with httpx.AsyncClient() as client:
            r = await client.get(
                GROUPS_API,
                headers={"Authorization": f"Bearer {token}"},
                params={"pageSize": 200},
                timeout=15,
            )
            r.raise_for_status()
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
        token = await _get_token()
        group_rn = await _resolve_group_resource_name(token, group_name)

        # Gruppe mit Member-Ressourcennamen laden
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{PEOPLE_API}/{group_rn}",
                headers={"Authorization": f"Bearer {token}"},
                params={"maxMembers": max_members},
                timeout=15,
            )
            r.raise_for_status()
        member_rns = r.json().get("memberResourceNames", [])
        if not member_rns:
            return json.dumps([], ensure_ascii=False)

        # Batch-GET für alle Mitglieder
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{PEOPLE_API}/people:batchGet",
                headers={"Authorization": f"Bearer {token}"},
                params={"resourceNames": member_rns, "personFields": PERSON_FIELDS},
                timeout=15,
            )
            r.raise_for_status()
        responses = r.json().get("responses", [])
        persons = [_format_person(resp["person"]) for resp in responses if "person" in resp]
        return json.dumps(persons, ensure_ascii=False)

    async def search_contacts(query: str, max_results: int = 10) -> str:
        """Kontakte nach Name oder E-Mail durchsuchen."""
        token = await _get_token()
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{PEOPLE_API}/people:searchContacts",
                headers={"Authorization": f"Bearer {token}"},
                params={"query": query, "readMask": PERSON_FIELDS, "pageSize": max_results},
                timeout=15,
            )
            r.raise_for_status()
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
        token = await _get_token()
        # People API: Name.displayName ist OUTPUT_ONLY — Google leitet den Wert
        # aus givenName/familyName ab. Wir splitten beim letzten Whitespace,
        # damit z.B. "Max Mustermann" → given="Max", family="Mustermann" wird;
        # einteilige Namen landen komplett in givenName.
        if " " in display_name.strip():
            given, family = display_name.rsplit(" ", 1)
            name_entry = {"givenName": given.strip(), "familyName": family.strip()}
        else:
            name_entry = {"givenName": display_name.strip()}
        body: dict = {"names": [name_entry]}
        if email:
            body["emailAddresses"] = [{"value": email}]
        if phone:
            body["phoneNumbers"] = [{"value": phone}]
        if organization:
            body["organizations"] = [{"name": organization}]
        if notes:
            body["biographies"] = [{"value": notes, "contentType": "TEXT_PLAIN"}]

        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{PEOPLE_API}/people:createContact",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
                timeout=15,
            )
            r.raise_for_status()
        p = r.json()
        person_rn = p.get("resourceName", "")

        if group and person_rn:
            group_rn = await _resolve_group_resource_name(token, group)
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{PEOPLE_API}/{group_rn}/members:modify",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"resourceNamesToAdd": [person_rn]},
                    timeout=15,
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
        token = await _get_token()
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{PEOPLE_API}/{resource_name}",
                headers={"Authorization": f"Bearer {token}"},
                params={"personFields": PERSON_FIELDS},
                timeout=15,
            )
            r.raise_for_status()
            current = r.json()

        etag = current.get("etag", "")
        patch: dict = {"etag": etag, "resourceName": resource_name}
        update_fields: list[str] = []

        if display_name:
            # Name.displayName ist OUTPUT_ONLY — selbe Falle wie bei create_contact.
            if " " in display_name.strip():
                given, family = display_name.rsplit(" ", 1)
                patch["names"] = [{"givenName": given.strip(), "familyName": family.strip()}]
            else:
                patch["names"] = [{"givenName": display_name.strip()}]
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
            async with httpx.AsyncClient() as client:
                r = await client.patch(
                    f"{PEOPLE_API}/{resource_name}:updateContact",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"updatePersonFields": ",".join(update_fields)},
                    json=patch,
                    timeout=15,
                )
                r.raise_for_status()

        if group:
            group_rn = await _resolve_group_resource_name(token, group)
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{PEOPLE_API}/{group_rn}/members:modify",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"resourceNamesToAdd": [resource_name]},
                    timeout=15,
                )

        return json.dumps({"resource_name": resource_name, "updated": True}, ensure_ascii=False)

    async def delete_contact(resource_name: str) -> str:
        """Kontakt löschen."""
        token = await _get_token()
        async with httpx.AsyncClient() as client:
            r = await client.delete(
                f"{PEOPLE_API}/{resource_name}:deleteContact",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            r.raise_for_status()
        return json.dumps({"resource_name": resource_name, "deleted": True}, ensure_ascii=False)

    return [
        Tool(
            name="google_contacts_list_all",
            description="Alle Google-Kontakte abrufen (paginiert). Nützlich zum Durchsuchen oder Aufräumen.",
            parameters={
                "type": "object",
                "properties": {
                    "max_results": {"type": "integer", "description": "Max. Anzahl Kontakte (Standard: 500)"},
                },
                "required": [],
            },
            executor=list_all_contacts,
            tier=TIER_READONLY,
        ),
        Tool(
            name="google_contacts_list_groups",
            description="Alle Kontaktgruppen/Labels auflisten.",
            parameters={"type": "object", "properties": {}, "required": []},
            executor=list_groups,
            tier=TIER_READONLY,
        ),
        Tool(
            name="google_contacts_list_by_group",
            description="Alle Kontakte einer bestimmten Gruppe/eines Labels abrufen.",
            parameters={
                "type": "object",
                "properties": {
                    "group_name":  {"type": "string", "description": "Name der Gruppe (z.B. 'Familie', 'Arbeit')"},
                    "max_members": {"type": "integer", "description": "Max. Mitglieder (Standard: 200)"},
                },
                "required": ["group_name"],
            },
            executor=list_by_group,
            tier=TIER_READONLY,
        ),
        Tool(
            name="google_contacts_search",
            description=(
                "Sucht Google-Kontakte nach Name oder E-Mail-Adresse. "
                "Nützlich um Empfänger für E-Mails/Nachrichten aufzulösen."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query":       {"type": "string", "description": "Suchbegriff (Name oder E-Mail)"},
                    "max_results": {"type": "integer", "description": "Max. Treffer (Standard: 10)"},
                },
                "required": ["query"],
            },
            executor=search_contacts,
            tier=TIER_READONLY,
        ),
        Tool(
            name="google_contacts_create",
            description="Legt einen neuen Kontakt in Google Contacts an.",
            parameters={
                "type": "object",
                "properties": {
                    "display_name": {"type": "string", "description": "Vollständiger Name"},
                    "email":        {"type": "string", "description": "E-Mail-Adresse (optional)"},
                    "phone":        {"type": "string", "description": "Telefonnummer (optional)"},
                    "organization": {"type": "string", "description": "Firma / Organisation (optional)"},
                    "notes":        {"type": "string", "description": "Notizen (optional)"},
                    "group":        {"type": "string", "description": "Gruppenname (optional, z.B. 'Familie')"},
                },
                "required": ["display_name"],
            },
            executor=create_contact,
            tier=TIER_WRITE_DATA,
        ),
        Tool(
            name="google_contacts_update",
            description="Aktualisiert einen bestehenden Kontakt. Nur gesetzte Felder werden überschrieben.",
            parameters={
                "type": "object",
                "properties": {
                    "resource_name": {"type": "string", "description": "Ressourcenname (aus google_contacts_search)"},
                    "display_name":  {"type": "string", "description": "Neuer Name (optional)"},
                    "email":         {"type": "string", "description": "Neue E-Mail (optional)"},
                    "phone":         {"type": "string", "description": "Neue Telefonnummer (optional)"},
                    "organization":  {"type": "string", "description": "Neue Organisation (optional)"},
                    "notes":         {"type": "string", "description": "Neue Notizen (optional)"},
                    "group":         {"type": "string", "description": "Gruppe zuweisen (optional)"},
                },
                "required": ["resource_name"],
            },
            executor=update_contact,
            tier=TIER_WRITE_DATA,
        ),
        Tool(
            name="google_contacts_delete",
            description="Löscht einen Kontakt. resource_name kommt aus google_contacts_search.",
            parameters={
                "type": "object",
                "properties": {
                    "resource_name": {"type": "string", "description": "Ressourcenname des Kontakts"},
                },
                "required": ["resource_name"],
            },
            executor=delete_contact,
            tier=TIER_WRITE_DATA,
        ),
    ]
