"""Nextcloud file tools for LLM function calling, only inside the agent folder
(``files.AgentFiles``). Reading carries TIER_WRITE_DATA like Google Drive: the
tiers keep private files away from external channels, and agents are granted
files separately from calendars and contacts. Names and contents are the
user's data, so they come back fenced as data, not instructions.
"""

import asyncio
import json
from typing import Any

from ....lib.function_calling import Tool
from ....lib.plugin_base import load_tool_description, load_tool_parameters
from ....lib.security import TIER_WRITE_DATA, TIER_WRITE_SYSTEM, wrap_untrusted_data
from ....lib.tool_args import parse_optional_text, parse_text
from .dav import DavError

FILES_MODULE = "files"
DEFAULT_SEARCH_LIMIT = 20
_EXPECTED_ERRORS = (ValueError, DavError)  # InvalidValue, NextcloudRefused and a limit that is no number
_SOURCE = "nextcloud"


def _answer(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _error(exc: Exception) -> str:
    return _answer({"error": str(exc)})


def get_nextcloud_file_tools(store: Any) -> list[Tool]:
    if not store.modules.can_read(FILES_MODULE):
        return []
    files = store.agent_files
    placeholders = {"agent_folder": store.settings.agent_folder}

    async def nextcloud_file_list(folder: str = "", query: str | None = None, limit: int = DEFAULT_SEARCH_LIMIT) -> str:
        try:
            term = parse_optional_text(query, "query")
            rows = await asyncio.to_thread(files.search, term, int(limit)) if term \
                else await asyncio.to_thread(files.listing, parse_text(folder, "folder") if folder else "")
        except _EXPECTED_ERRORS as exc:
            return _error(exc)
        return wrap_untrusted_data(_answer({"total_count": len(rows), "results": rows}), _SOURCE)

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

    def tool(name: str, tier: int, executor: Any) -> Tool:
        return Tool(name=name, tier=tier, executor=executor, description=load_tool_description(__file__, name),
                    parameters=load_tool_parameters(__file__, name, values=placeholders))

    reading = [("nextcloud_file_list", TIER_WRITE_DATA, nextcloud_file_list),
               ("nextcloud_file_read", TIER_WRITE_DATA, nextcloud_file_read)]
    writing = [("nextcloud_file_write", TIER_WRITE_DATA, nextcloud_file_write),
               ("nextcloud_file_move", TIER_WRITE_DATA, nextcloud_file_move),
               ("nextcloud_file_delete", TIER_WRITE_SYSTEM, nextcloud_file_delete)]
    allowed = reading + writing if store.modules.can_write(FILES_MODULE) else reading
    return [tool(name, tier, executor) for name, tier, executor in allowed]
