"""The one Drive folder the agents work in (``GOOGLE_DRIVE_AGENT_FOLDER``).

Agents may read, write, move and delete everything inside it, subfolders
included, and nothing outside. The folder itself is the boundary: they may
fill it but not delete or move it. The document manager (``source.py``) does
not go through here — the user sees the whole Drive.
"""

from __future__ import annotations

from .....lib.logging_utils import log_message
from .._common import _google_request
from ._api import DRIVE_API, FOLDER_MIME, escape_drive_term


class OutsideAgentFolder(PermissionError):
    pass


class AgentFolder:
    """Boundary of the agents' Drive access, resolved once per toolkit build:
    a renamed setting or a folder created meanwhile counts from the next turn."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._folder_ids: set[str] | None = None
        self._root_id = ""

    async def root_id(self) -> str:
        await self.folder_ids()
        return self._root_id

    async def folder_ids(self) -> set[str]:
        """The agent folder and all folders below it."""
        if self._folder_ids is None:
            self._root_id = await self._find_or_create_root()
            self._folder_ids = await self._collect_subfolders(self._root_id)
        return self._folder_ids

    async def require_folder(self, folder_id: str) -> str:
        """A folder agents may list or create in; empty = the agent folder."""
        if not folder_id:
            return await self.root_id()
        if folder_id not in await self.folder_ids():
            raise self._refusal()
        return folder_id

    async def require_inside(self, file_id: str) -> None:
        """A file or folder agents may read, change, move or delete — strictly
        inside, the agent folder itself excluded."""
        if file_id == await self.root_id():
            raise OutsideAgentFolder(f"The agent folder '{self.name}' itself cannot be changed, moved or deleted.")
        r = await _google_request("GET", f"{DRIVE_API}/files/{file_id}", params={"fields": "parents"})
        if not self.holds(r.json()):
            raise self._refusal()

    def adopt(self, folder_id: str) -> None:
        """A folder the agent just created inside: usable within the same turn."""
        assert self._folder_ids is not None, "created via require_folder(), so resolved"
        self._folder_ids.add(folder_id)

    def holds(self, file: dict) -> bool:
        """Whether a file listing entry (with ``parents``) lies inside."""
        assert self._folder_ids is not None, "folder_ids() resolves the folder first"
        parents = file.get("parents") or []
        return bool(parents) and parents[0] in self._folder_ids

    async def _find_or_create_root(self) -> str:
        if not self.name:
            raise RuntimeError("GOOGLE_DRIVE_AGENT_FOLDER is empty — set the agents' Drive folder in the Google Suite settings.")
        query = (
            f"name = '{escape_drive_term(self.name)}' and mimeType = '{FOLDER_MIME}' "
            "and 'root' in parents and trashed = false"
        )
        r = await _google_request("GET", f"{DRIVE_API}/files", params={"q": query, "fields": "files(id)"})
        found = r.json().get("files", [])
        if len(found) > 1:
            raise RuntimeError(
                f"{len(found)} folders named '{self.name}' in My Drive — the agent folder is ambiguous, rename all but one."
            )
        if found:
            return str(found[0]["id"])
        r = await _google_request(
            "POST", f"{DRIVE_API}/files", params={"fields": "id"},
            json={"name": self.name, "mimeType": FOLDER_MIME},
        )
        log_message(f"Google Drive: agent folder '{self.name}' created in My Drive")
        return str(r.json()["id"])

    async def _collect_subfolders(self, root_id: str) -> set[str]:
        found = {root_id}
        level = [root_id]
        while level:
            parents = " or ".join(f"'{folder_id}' in parents" for folder_id in level)
            query = f"({parents}) and mimeType = '{FOLDER_MIME}' and trashed = false"
            level = []
            page_token = ""
            while True:
                params = {"q": query, "fields": "nextPageToken,files(id)", "pageSize": 1000}
                if page_token:
                    params["pageToken"] = page_token
                r = await _google_request("GET", f"{DRIVE_API}/files", params=params)
                body = r.json()
                level += [f["id"] for f in body.get("files", []) if f["id"] not in found]
                page_token = body.get("nextPageToken", "")
                if not page_token:
                    break
            found.update(level)
        return found

    def _refusal(self) -> OutsideAgentFolder:
        return OutsideAgentFolder(f"Outside the agent folder '{self.name}' — agents may only work inside it.")
