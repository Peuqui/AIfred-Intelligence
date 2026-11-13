"""Nextcloud file tools for LLM function calling, only inside the agent folder
(``files.AgentFiles``). Upload and download copy a file between that folder and
AIfred's local workspace (``lib/file_manager.py``) byte for byte, without the
content going through the model: images and other binary files too, any size. Reading carries TIER_WRITE_DATA like Google Drive: the
tiers keep private files away from external channels, and agents are granted
files separately from calendars and contacts. Names and contents are the
user's data, so they come back fenced as data, not instructions.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

from ....lib import file_manager
from ....lib.config import LIST_DEFAULT
from ....lib.function_calling import Tool
from ....lib.plugin_base import load_tool_description, load_tool_parameters
from ....lib.security import TIER_WRITE_DATA, TIER_WRITE_SYSTEM, wrap_untrusted_data
from ....lib.tool_args import parse_optional_text, parse_text
from .dav import DavError

FILES_MODULE = "files"
_EXPECTED_ERRORS = (ValueError, DavError, OSError)  # InvalidValue, NextcloudRefused, a limit that is no number,
# and the local file of an upload or download
_SOURCE = "nextcloud"


def _answer(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _error(exc: Exception) -> str:
    return _answer({"error": str(exc)})


def _workspace_file(rel_path: str) -> Path:
    """An existing file in the local workspace."""
    path, refusal = file_manager.safe_resolve(rel_path)
    if refusal is not None:
        raise ValueError(str(refusal))
    assert path is not None
    if not path.is_file():
        raise ValueError(f"no file {rel_path!r} in the workspace")
    return path


def _workspace_target(rel_path: str, name: str, overwrite: bool) -> Path:
    """Where a download goes in the local workspace: ``rel_path``, or ``name`` inside it when it is a folder
    (one that exists, or any path ending in "/", which the download creates). A file there already stays
    unless ``overwrite``."""
    path, refusal = file_manager.safe_resolve(rel_path)
    if refusal is not None:
        raise ValueError(str(refusal))
    assert path is not None
    if path.is_dir() or rel_path.endswith("/"):
        path = path / name
    if path.exists() and not overwrite:
        raise ValueError(f"{rel_path!r} exists already in the workspace; pass overwrite to replace it")
    return path


def get_nextcloud_file_tools(store: Any) -> list[Tool]:
    if not store.modules.can_read(FILES_MODULE):
        return []
    files = store.agent_files
    placeholders = {"agent_folder": store.settings.agent_folder}

    async def nextcloud_file_list(folder: str = "", query: str | None = None, limit: int = LIST_DEFAULT) -> str:
        try:
            term = parse_optional_text(query, "query")
            shown = max(1, int(limit))
            rows = await asyncio.to_thread(files.search, term) if term \
                else await asyncio.to_thread(files.listing, parse_text(folder, "folder") if folder else "")
        except _EXPECTED_ERRORS as exc:
            return _error(exc)
        return wrap_untrusted_data(_answer({"total_count": len(rows), "results": rows[:shown]}), _SOURCE)

    async def nextcloud_file_read(path: str, line_start: int | str = 0, line_end: int | str = 0) -> str:
        try:
            result = await asyncio.to_thread(files.read, parse_text(path, "path"), int(line_start), int(line_end))
        except _EXPECTED_ERRORS as exc:
            return _error(exc)
        return wrap_untrusted_data(_answer(result), _SOURCE)

    async def nextcloud_file_write(path: str, content: str, etag: str | None = None) -> str:
        try:
            result = await asyncio.to_thread(files.write, parse_text(path, "path"), parse_text(content, "content"),
                                             parse_optional_text(etag, "etag"))
        except _EXPECTED_ERRORS as exc:
            return _error(exc)
        return _answer({"success": True, **result})

    async def nextcloud_file_move(path: str, target: str) -> str:
        try:
            result = await asyncio.to_thread(files.move, parse_text(path, "path"), parse_text(target, "target"))
        except _EXPECTED_ERRORS as exc:
            return _error(exc)
        return _answer({"success": True, **result})

    async def nextcloud_file_delete(path: str) -> str:
        try:
            await asyncio.to_thread(files.delete, parse_text(path, "path"))
        except _EXPECTED_ERRORS as exc:
            return _error(exc)
        return _answer({"success": True, "path": path})

    async def nextcloud_file_upload(source: str, path: str, etag: str | None = None) -> str:
        try:
            local = _workspace_file(parse_text(source, "source"))
            result = await asyncio.to_thread(files.upload, local, parse_text(path, "path"),
                                             parse_optional_text(etag, "etag"))
        except _EXPECTED_ERRORS as exc:
            return _error(exc)
        return _answer({"success": True, "source": source, **result})

    async def nextcloud_file_download(path: str, target: str, overwrite: bool = False) -> str:
        try:
            cleaned = parse_text(path, "path")
            local = _workspace_target(parse_text(target, "target"), cleaned.rsplit("/", 1)[-1], bool(overwrite))
            size = await asyncio.to_thread(files.download, cleaned, local)
        except _EXPECTED_ERRORS as exc:
            return _error(exc)
        return _answer({"success": True, "path": cleaned,
                        "target": str(local.relative_to(file_manager.DOCUMENTS_DIR.resolve())), "bytes": size})

    def tool(name: str, tier: int, executor: Any) -> Tool:
        return Tool(name=name, tier=tier, executor=executor, read_only=(name, tier, executor) in reading,
                    description=load_tool_description(__file__, name),
                    parameters=load_tool_parameters(__file__, name, values=placeholders))

    reading = [("nextcloud_file_list", TIER_WRITE_DATA, nextcloud_file_list),
               ("nextcloud_file_read", TIER_WRITE_DATA, nextcloud_file_read)]
    # Reads Nextcloud, but writes the local workspace: no read_only tool, and there only reading Nextcloud is needed.
    downloading = [("nextcloud_file_download", TIER_WRITE_DATA, nextcloud_file_download)]
    writing = [("nextcloud_file_write", TIER_WRITE_DATA, nextcloud_file_write),
               ("nextcloud_file_upload", TIER_WRITE_DATA, nextcloud_file_upload),
               ("nextcloud_file_move", TIER_WRITE_DATA, nextcloud_file_move),
               ("nextcloud_file_delete", TIER_WRITE_SYSTEM, nextcloud_file_delete)]
    allowed = reading + downloading + (writing if store.modules.can_write(FILES_MODULE) else [])
    return [tool(name, tier, executor) for name, tier, executor in allowed]
