"""Google Drive tools — Dateiverwaltung via Drive API v3."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import httpx

from .....lib.function_calling import Tool
from .....lib.security import TIER_READONLY, TIER_WRITE_DATA

logger = logging.getLogger(__name__)

DRIVE_API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"

# Google-interne MIME-Typen → Export-Format
_GOOGLE_EXPORT_MIME: dict[str, str] = {
    "application/vnd.google-apps.document":     "text/plain",
    "application/vnd.google-apps.spreadsheet":  "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}

_FILE_FIELDS = "id,name,mimeType,modifiedTime,size,parents,webViewLink"


async def _get_token() -> str:
    from .....lib.oauth.broker import oauth_broker
    token = await oauth_broker.get_token("google")
    if not token:
        raise RuntimeError("Google nicht verbunden. Bitte erst in den Einstellungen autorisieren.")
    return token


def get_drive_tools(lang: str = "de") -> list[Tool]:

    async def list_files(
        folder_id: str = "",
        page_size: int = 30,
        order_by: str = "modifiedTime desc",
    ) -> str:
        """Dateien im Drive auflisten, optional gefiltert nach Ordner."""
        token = await _get_token()
        query = "trashed=false"
        if folder_id:
            query += f" and '{folder_id}' in parents"
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{DRIVE_API}/files",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "q": query,
                    "pageSize": page_size,
                    "orderBy": order_by,
                    "fields": f"files({_FILE_FIELDS})",
                },
                timeout=15,
            )
            r.raise_for_status()
        files = r.json().get("files", [])
        result = [
            {
                "id": f.get("id"),
                "name": f.get("name"),
                "type": f.get("mimeType"),
                "modified": f.get("modifiedTime"),
                "size": f.get("size"),
                "link": f.get("webViewLink"),
            }
            for f in files
        ]
        return json.dumps(result, ensure_ascii=False)

    async def search_files(query: str, page_size: int = 20) -> str:
        """Dateien nach Name oder Inhalt suchen (Drive Query Syntax)."""
        token = await _get_token()
        # Nutze fullText-Suche falls query kein Drive-Operator enthält
        if "=" not in query and "in" not in query:
            drive_query = f"fullText contains '{query}' and trashed=false"
        else:
            drive_query = query + " and trashed=false"
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{DRIVE_API}/files",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "q": drive_query,
                    "pageSize": page_size,
                    "fields": f"files({_FILE_FIELDS})",
                },
                timeout=15,
            )
            r.raise_for_status()
        files = r.json().get("files", [])
        result = [
            {
                "id": f.get("id"),
                "name": f.get("name"),
                "type": f.get("mimeType"),
                "modified": f.get("modifiedTime"),
                "link": f.get("webViewLink"),
            }
            for f in files
        ]
        return json.dumps(result, ensure_ascii=False)

    async def get_file(file_id: str) -> str:
        """Dateiinhalt lesen. Google Docs/Sheets werden als Text exportiert."""
        token = await _get_token()
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient() as client:
            # Metadaten abrufen um MIME-Typ zu kennen
            meta = await client.get(
                f"{DRIVE_API}/files/{file_id}",
                headers=headers,
                params={"fields": "id,name,mimeType"},
                timeout=15,
            )
            meta.raise_for_status()
            mime = meta.json().get("mimeType", "")
            name = meta.json().get("name", file_id)

            export_mime = _GOOGLE_EXPORT_MIME.get(mime)
            if export_mime:
                # Google-natives Format → Export
                r = await client.get(
                    f"{DRIVE_API}/files/{file_id}/export",
                    headers=headers,
                    params={"mimeType": export_mime},
                    timeout=30,
                )
            else:
                # Binär- oder Text-Datei → direkt herunterladen
                r = await client.get(
                    f"{DRIVE_API}/files/{file_id}",
                    headers=headers,
                    params={"alt": "media"},
                    timeout=30,
                )
            r.raise_for_status()

        return json.dumps({"id": file_id, "name": name, "content": r.text}, ensure_ascii=False)

    async def create_file(
        name: str,
        content: str,
        folder_id: str = "",
        mime_type: str = "text/plain",
    ) -> str:
        """Neue Textdatei erstellen und Inhalt hochladen (multipart upload)."""
        token = await _get_token()
        boundary = uuid.uuid4().hex
        metadata: dict[str, Any] = {"name": name, "mimeType": mime_type}
        if folder_id:
            metadata["parents"] = [folder_id]

        body = (
            f"--{boundary}\r\n"
            "Content-Type: application/json; charset=UTF-8\r\n\r\n"
            + json.dumps(metadata)
            + f"\r\n--{boundary}\r\n"
            f"Content-Type: {mime_type}\r\n\r\n"
            + content
            + f"\r\n--{boundary}--"
        )
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{UPLOAD_API}/files",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": f"multipart/related; boundary={boundary}",
                },
                params={"uploadType": "multipart", "fields": _FILE_FIELDS},
                content=body.encode("utf-8"),
                timeout=30,
            )
            r.raise_for_status()
        f = r.json()
        return json.dumps(
            {"id": f.get("id"), "name": f.get("name"), "link": f.get("webViewLink")},
            ensure_ascii=False,
        )

    async def update_file(file_id: str, content: str, mime_type: str = "text/plain") -> str:
        """Inhalt einer bestehenden Datei ersetzen."""
        token = await _get_token()
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{UPLOAD_API}/files/{file_id}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": mime_type,
                },
                params={"uploadType": "media"},
                content=content.encode("utf-8"),
                timeout=30,
            )
            r.raise_for_status()
        return json.dumps({"id": file_id, "updated": True}, ensure_ascii=False)

    async def delete_file(file_id: str) -> str:
        """Datei dauerhaft löschen (nicht in den Papierkorb verschieben)."""
        token = await _get_token()
        async with httpx.AsyncClient() as client:
            r = await client.delete(
                f"{DRIVE_API}/files/{file_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            r.raise_for_status()
        return json.dumps({"id": file_id, "deleted": True}, ensure_ascii=False)

    async def create_folder(name: str, parent_id: str = "") -> str:
        """Neuen Ordner im Drive erstellen."""
        token = await _get_token()
        metadata: dict[str, Any] = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            metadata["parents"] = [parent_id]
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{DRIVE_API}/files",
                headers={"Authorization": f"Bearer {token}"},
                params={"fields": _FILE_FIELDS},
                json=metadata,
                timeout=15,
            )
            r.raise_for_status()
        f = r.json()
        return json.dumps({"id": f.get("id"), "name": f.get("name")}, ensure_ascii=False)

    async def move_file(file_id: str, target_folder_id: str) -> str:
        """Datei in einen anderen Ordner verschieben."""
        token = await _get_token()
        # Aktuelle Parents laden
        async with httpx.AsyncClient() as client:
            meta = await client.get(
                f"{DRIVE_API}/files/{file_id}",
                headers={"Authorization": f"Bearer {token}"},
                params={"fields": "parents"},
                timeout=15,
            )
            meta.raise_for_status()
            old_parents = ",".join(meta.json().get("parents", []))

            r = await client.patch(
                f"{DRIVE_API}/files/{file_id}",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "addParents": target_folder_id,
                    "removeParents": old_parents,
                    "fields": "id,parents",
                },
                timeout=15,
            )
            r.raise_for_status()
        return json.dumps({"id": file_id, "moved_to": target_folder_id}, ensure_ascii=False)

    return [
        Tool(
            name="google_drive_list_files",
            description=(
                "Listet Dateien im Google Drive auf. "
                "Optional nach Ordner filtern via folder_id."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "folder_id":  {"type": "string", "description": "Ordner-ID (optional, Standard: Root)"},
                    "page_size":  {"type": "integer", "description": "Max. Anzahl Ergebnisse (Standard: 30)"},
                    "order_by":   {"type": "string", "description": "Sortierung (Standard: modifiedTime desc)"},
                },
                "required": [],
            },
            executor=list_files,
            tier=TIER_READONLY,
        ),
        Tool(
            name="google_drive_search",
            description=(
                "Sucht Dateien im Google Drive. "
                "Einfache Stichwortsuche (z.B. 'Projektplan') oder Drive Query Syntax "
                "(z.B. \"name contains 'Bericht'\")."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query":     {"type": "string", "description": "Suchbegriff oder Drive-Query"},
                    "page_size": {"type": "integer", "description": "Max. Ergebnisse (Standard: 20)"},
                },
                "required": ["query"],
            },
            executor=search_files,
            tier=TIER_READONLY,
        ),
        Tool(
            name="google_drive_get_file",
            description=(
                "Liest den Inhalt einer Drive-Datei. "
                "Google Docs werden als Klartext exportiert, Sheets als CSV. "
                "Der Inhalt wird auf 50.000 Zeichen begrenzt."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "description": "ID der Datei"},
                },
                "required": ["file_id"],
            },
            executor=get_file,
            tier=TIER_READONLY,
        ),
        Tool(
            name="google_drive_create_file",
            description="Erstellt eine neue Textdatei im Google Drive und befüllt sie mit Inhalt.",
            parameters={
                "type": "object",
                "properties": {
                    "name":      {"type": "string", "description": "Dateiname (mit Endung, z.B. 'notiz.txt')"},
                    "content":   {"type": "string", "description": "Dateiinhalt"},
                    "folder_id": {"type": "string", "description": "Zielordner-ID (optional)"},
                    "mime_type": {"type": "string", "description": "MIME-Typ (Standard: text/plain)"},
                },
                "required": ["name", "content"],
            },
            executor=create_file,
            tier=TIER_WRITE_DATA,
        ),
        Tool(
            name="google_drive_update_file",
            description="Überschreibt den Inhalt einer bestehenden Drive-Datei.",
            parameters={
                "type": "object",
                "properties": {
                    "file_id":   {"type": "string", "description": "ID der zu aktualisierenden Datei"},
                    "content":   {"type": "string", "description": "Neuer Dateiinhalt"},
                    "mime_type": {"type": "string", "description": "MIME-Typ (Standard: text/plain)"},
                },
                "required": ["file_id", "content"],
            },
            executor=update_file,
            tier=TIER_WRITE_DATA,
        ),
        Tool(
            name="google_drive_delete_file",
            description="Löscht eine Datei dauerhaft aus dem Google Drive.",
            parameters={
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "description": "ID der zu löschenden Datei"},
                },
                "required": ["file_id"],
            },
            executor=delete_file,
            tier=TIER_WRITE_DATA,
        ),
        Tool(
            name="google_drive_create_folder",
            description="Erstellt einen neuen Ordner im Google Drive.",
            parameters={
                "type": "object",
                "properties": {
                    "name":      {"type": "string", "description": "Ordnername"},
                    "parent_id": {"type": "string", "description": "Übergeordneter Ordner (optional)"},
                },
                "required": ["name"],
            },
            executor=create_folder,
            tier=TIER_WRITE_DATA,
        ),
        Tool(
            name="google_drive_move_file",
            description="Verschiebt eine Datei in einen anderen Ordner.",
            parameters={
                "type": "object",
                "properties": {
                    "file_id":          {"type": "string", "description": "ID der zu verschiebenden Datei"},
                    "target_folder_id": {"type": "string", "description": "ID des Zielordners"},
                },
                "required": ["file_id", "target_folder_id"],
            },
            executor=move_file,
            tier=TIER_WRITE_DATA,
        ),
    ]
