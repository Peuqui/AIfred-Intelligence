"""The trash of the chats.

Deleting a chat (the bucket in the chat list) moves its file to ``data/sessions_trash/``, with the time it went in. The
files of the chat (uploaded images, spoken audio, browser screenshots, HTML pages) stay where they are: the chat can be
brought back whole. Only what leaves the trash for good takes them along: a click on "delete for good" or "empty the
trash", or the nightly cleanup of ``SESSION_TRASH_RETENTION_DAYS`` days.

A chat that comes back lost its channel routes (a Hub channel that wrote into it): they are removed when it goes into the
trash, as a message of the channel must not be routed into a chat that is not there.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .browser_push import notify_changed
from .config import DATA_DIR, SESSION_TRASH_RETENTION_DAYS
from .session_storage import _remove_chat_files, get_session_path
from .user_error import Conflict, Missing

TRASH_DIR = DATA_DIR / "sessions_trash"
DELETED_AT = "deleted_at"


@dataclass(frozen=True)
class TrashedSession:
    session_id: str
    title: str | None
    message_count: int
    last_message_at: str
    deleted_at: datetime
    expires_at: datetime


def _trash_path(session_id: str) -> Path:
    """The file of a chat in the trash; ValueError for a string that is no session id."""
    return TRASH_DIR / get_session_path(session_id).name


def _read(path: Path) -> dict[str, Any]:
    session: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return session


def _write(path: Path, session: dict[str, Any]) -> None:
    """Atomic like the session files: a crash leaves the old file, not half a new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(session, file, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def _owned_by(session: dict[str, Any], owner: str) -> bool:
    return str(session.get("owner", "")).lower() == owner.lower()


def trash(session_id: str, owner: str) -> None:
    """Move the chat of ``owner`` into the trash. :class:`Missing` when it is not there or not the owner's."""
    source = get_session_path(session_id)
    if not source.exists():
        raise Missing("no_session")
    session = _read(source)
    if not _owned_by(session, owner):
        raise Missing("no_session")
    session[DELETED_AT] = datetime.now().isoformat(timespec="seconds")
    _write(TRASH_DIR / source.name, session)
    source.unlink()
    from .routing_table import routing_table

    routing_table.delete_routes_for_session(session_id)
    notify_changed("sessions")


def restore(session_id: str, owner: str) -> None:
    """Bring a chat of ``owner`` back out of the trash. :class:`Missing` when it is not in it,
    :class:`Conflict` when a chat of that id is there again."""
    source = _trash_path(session_id)
    if not source.exists():
        raise Missing("no_session")
    session = _read(source)
    if not _owned_by(session, owner):
        raise Missing("no_session")
    target = get_session_path(session_id)
    if target.exists():
        raise Conflict("session_exists")
    del session[DELETED_AT]
    _write(target, session)
    source.unlink()
    notify_changed("sessions")


def _purge(path: Path, session: dict[str, Any]) -> None:
    """Remove a chat in the trash for good, with the files of its chat."""
    from .formatting import cleanup_session_html

    cleanup_session_html(session)
    _remove_chat_files(path.stem)
    path.unlink()


def purge(session_id: str, owner: str) -> None:
    """Delete a chat of ``owner`` in the trash for good. :class:`Missing` when it is not in it."""
    path = _trash_path(session_id)
    if not path.exists():
        raise Missing("no_session")
    session = _read(path)
    if not _owned_by(session, owner):
        raise Missing("no_session")
    _purge(path, session)


def _deleted_at(session: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(session[DELETED_AT])


def listed(owner: str) -> list[TrashedSession]:
    """The chats of ``owner`` in the trash, the one deleted last first."""
    found = []
    for path in TRASH_DIR.glob("*.json"):
        session = _read(path)
        if not _owned_by(session, owner):
            continue
        deleted_at = _deleted_at(session)
        data = session.get("data", {})
        found.append(TrashedSession(
            session_id=path.stem,
            title=data.get("title"),
            message_count=len(data.get("chat_history", [])),
            last_message_at=str(session.get("last_message_at", "")),
            deleted_at=deleted_at,
            expires_at=deleted_at + timedelta(days=SESSION_TRASH_RETENTION_DAYS),
        ))
    return sorted(found, key=lambda entry: entry.deleted_at, reverse=True)


def empty(owner: str) -> int:
    """Delete every chat of ``owner`` in the trash for good; how many there were."""
    entries = listed(owner)
    for entry in entries:
        purge(entry.session_id, owner)
    return len(entries)


def purge_expired(now: datetime | None = None) -> int:
    """Delete the chats (of every owner) that lie in the trash longer than ``SESSION_TRASH_RETENTION_DAYS``;
    how many."""
    cutoff = (now or datetime.now()) - timedelta(days=SESSION_TRASH_RETENTION_DAYS)
    removed = 0
    for path in TRASH_DIR.glob("*.json"):
        session = _read(path)
        if _deleted_at(session) < cutoff:
            _purge(path, session)
            removed += 1
    return removed


async def session_trash_cleanup_task() -> None:
    """Background task: the nightly slot (``GARBAGE_COLLECTION_HOUR``, as the other cleanups) removes the chats that
    lay in the trash too long."""
    import asyncio

    from .cleanup_utils import seconds_until_next_run
    from .config import GARBAGE_COLLECTION_HOUR
    from .logging_utils import log_exception, log_message

    log_message(
        f"🗑️ Chat trash cleanup task started "
        f"(slot: {GARBAGE_COLLECTION_HOUR:02d}:00 local, retention: {SESSION_TRASH_RETENTION_DAYS}d)"
    )
    while True:
        try:
            await asyncio.sleep(seconds_until_next_run(GARBAGE_COLLECTION_HOUR))
            removed = await asyncio.to_thread(purge_expired)
            if removed > 0:
                log_message(f"🗑️ Chat trash cleanup: {removed} chat(s) removed for good")
        except Exception as exc:  # noqa: BLE001 — the nightly task must not end; logged
            log_exception("⚠️ Chat trash cleanup task error", exc)
