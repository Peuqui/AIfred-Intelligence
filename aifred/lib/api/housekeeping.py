"""What the settings page keeps an eye on: the local files nothing deletes by itself (chat exports, sandbox output;
lib/storage_manager.py) and the audit log of the tools' runs (lib/security.py)."""

import asyncio

from pydantic import BaseModel, ConfigDict, Field

from ..logging_utils import log_message
from ..user_error import NotDone
from .app import api_app


class ManagedFile(BaseModel):
    id: str = Field(description="<kind>/<path in its folder>")
    kind: str = Field(description="export or sandbox")
    name: str
    filename: str
    url: str
    size_kb: int
    mtime: str = Field(description="When it was last written, as the UI shows a time")
    is_html: bool


class FileIds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str]


class ShowcaseCopy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str


class ShowcaseCopied(BaseModel):
    name: str


class AuditEntry(BaseModel):
    timestamp: str
    session_id: str
    session_short: str
    agent_id: str
    source: str
    tool_name: str
    tool_tier: str
    success: bool
    duration: str
    args: str = Field(description="The arguments, defused when written and shortened")


def _files() -> list[ManagedFile]:
    from ..storage_manager import list_managed_files

    return [ManagedFile(**{key: entry[key] for key in ManagedFile.model_fields}) for entry in list_managed_files()]


@api_app.get("/storage/files", response_model=list[ManagedFile], tags=["Housekeeping"])
async def storage_files() -> list[ManagedFile]:
    return await asyncio.to_thread(_files)


@api_app.post("/storage/files/delete", response_model=list[ManagedFile], tags=["Housekeeping"])
async def storage_files_delete(chosen: FileIds) -> list[ManagedFile]:
    """Delete the named files (an id outside the managed folders is left alone); answers with the list."""
    from ..storage_manager import delete_managed_files

    deleted = await asyncio.to_thread(delete_managed_files, chosen.ids)
    log_message(f"🗑️ Storage: {deleted}/{len(chosen.ids)} files deleted")
    return await asyncio.to_thread(_files)


@api_app.post("/storage/showcase", response_model=ShowcaseCopied, tags=["Housekeeping"])
async def storage_showcase(copy: ShowcaseCopy) -> ShowcaseCopied:
    """Copy an exported chat (HTML) into the GitHub showcase (docs/examples/)."""
    from ..storage_manager import copy_to_showcase

    name = await asyncio.to_thread(copy_to_showcase, copy.id)
    if not name:
        raise NotDone("showcase_not_copied", file=copy.id)
    log_message(f"📺 Storage: copied to showcase: {name}")
    return ShowcaseCopied(name=name)


@api_app.get("/audit", response_model=list[AuditEntry], tags=["Housekeeping"])
async def audit_entries() -> list[AuditEntry]:
    """The newest tool runs, newest first."""
    from ..security import load_audit_entries

    entries = await asyncio.to_thread(load_audit_entries)
    return [AuditEntry(timestamp=entry["timestamp"], session_id=entry["session_id"], session_short=entry["session_short"],
                       agent_id=entry["agent_id"], source=entry["source"], tool_name=entry["tool_name"],
                       tool_tier=entry["tool_tier"], success=entry["success"] == "OK", duration=entry["duration"],
                       args=entry["args"])
            for entry in entries]
