"""Routing Table — maps channel conversations to AIfred sessions.

SQLite-backed mapping of (channel, channel_id) to session_id.
Thread-safe via sqlite3's built-in locking.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR
from .logging_utils import log_message

_DB_PATH = DATA_DIR / "message_hub" / "routing.db"


@dataclass
class Route:
    """A single routing entry."""

    channel: str
    channel_id: str
    session_id: str
    created_at: str
    updated_at: str


class RoutingTable:
    """SQLite-backed routing table for the Message Hub."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or _DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS routes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(channel, channel_id)
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path), timeout=5)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def get_route(self, channel: str, channel_id: str) -> Route | None:
        """Look up the session for a channel conversation."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT channel, channel_id, session_id, created_at, updated_at "
                "FROM routes WHERE channel = ? AND channel_id = ?",
                (channel, channel_id),
            ).fetchone()
        if row is None:
            return None
        return Route(*row)

    def set_route(self, channel: str, channel_id: str, session_id: str) -> None:
        """Create or update a route."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO routes (channel, channel_id, session_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(channel, channel_id)
                DO UPDATE SET session_id = excluded.session_id, updated_at = excluded.updated_at
                """,
                (channel, channel_id, session_id, now, now),
            )
        log_message(f"Routing: {channel}:{channel_id[:16]} -> session {session_id[:8]}")

    def delete_route(self, channel: str, channel_id: str) -> None:
        """Remove a route."""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM routes WHERE channel = ? AND channel_id = ?",
                (channel, channel_id),
            )

    def delete_routes_for_session(self, session_id: str) -> int:
        """Remove all routes pointing to a session. Returns count deleted."""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM routes WHERE session_id = ?",
                (session_id,),
            )
            return cursor.rowcount

# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
routing_table = RoutingTable()
