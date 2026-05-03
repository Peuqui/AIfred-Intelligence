"""Audio File Index — SQLite/FTS5-backed search across configured sources.

The audio_player plugin's source folders can hold many thousands of files
(NAS mounts with 17k+ tracks). A naive `Path.rglob('*')` on every tool
call is slow over NFS and floods the LLM context with too many items.

This module maintains a persistent SQLite index with two tables:
- `files`        — one row per audio file (path, mtime, size, ID3 tags)
- `files_fts`    — FTS5 virtual table for BM25 ranking on artist/album/
                    title/filename/rel_path

mutagen reads ID3v2/Vorbis/MP4/FLAC tags. Album/Artist/Year/Genre often
contain information that's not in the path — e.g. compilation albums
list real artists in tags, but the folder is named after the compilation.

Index is updated incrementally based on mtime: a full re-scan only walks
the file system, but only changed/new files are re-tagged. Initial scan
of 17k files takes ~3-5 minutes (NFS-bound).
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .config import DATA_DIR
from .logging_utils import log_message

INDEX_DB = DATA_DIR / "audio_index.sqlite"

AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".opus", ".aac"}


@dataclass
class IndexEntry:
    source: str
    rel_path: str
    filename: str
    mtime: int
    size: int
    artist: Optional[str]
    album: Optional[str]
    title: Optional[str]
    year: Optional[int]
    genre: Optional[str]
    duration: Optional[float]
    track: Optional[int]


@dataclass
class ScanResult:
    source: str
    scanned: int        # total files seen
    inserted: int       # new files added to index
    updated: int        # changed files (mtime mismatch)
    deleted: int        # files removed (gone from filesystem)
    elapsed_sec: float
    errors: int


class AudioIndex:
    """SQLite-backed audio file index with FTS5 search."""

    def __init__(self, path: Path = INDEX_DB) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._init_schema()

    # ── Schema ──────────────────────────────────────────────

    def _init_schema(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS files (
                    id        INTEGER PRIMARY KEY,
                    source    TEXT NOT NULL,
                    rel_path  TEXT NOT NULL,
                    filename  TEXT NOT NULL,
                    mtime     INTEGER NOT NULL,
                    size      INTEGER NOT NULL,
                    artist    TEXT,
                    album     TEXT,
                    title     TEXT,
                    year      INTEGER,
                    genre     TEXT,
                    duration  REAL,
                    track     INTEGER,
                    UNIQUE(source, rel_path)
                );

                CREATE INDEX IF NOT EXISTS idx_files_source ON files(source);
                CREATE INDEX IF NOT EXISTS idx_files_mtime  ON files(mtime);

                CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
                    artist, album, title, genre, filename, rel_path,
                    content='files', content_rowid='id',
                    tokenize='unicode61 remove_diacritics 2'
                );

                -- Triggers keep FTS in sync with files table
                CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
                    INSERT INTO files_fts(rowid, artist, album, title, genre, filename, rel_path)
                    VALUES (new.id, new.artist, new.album, new.title, new.genre, new.filename, new.rel_path);
                END;
                CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
                    INSERT INTO files_fts(files_fts, rowid, artist, album, title, genre, filename, rel_path)
                    VALUES ('delete', old.id, old.artist, old.album, old.title, old.genre, old.filename, old.rel_path);
                END;
                CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE ON files BEGIN
                    INSERT INTO files_fts(files_fts, rowid, artist, album, title, genre, filename, rel_path)
                    VALUES ('delete', old.id, old.artist, old.album, old.title, old.genre, old.filename, old.rel_path);
                    INSERT INTO files_fts(rowid, artist, album, title, genre, filename, rel_path)
                    VALUES (new.id, new.artist, new.album, new.title, new.genre, new.filename, new.rel_path);
                END;
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    # ── Tag reading via mutagen ─────────────────────────────

    @staticmethod
    def _read_tags(file_path: Path) -> dict[str, Any]:
        """Return tag dict {artist, album, title, year, genre, duration, track}.

        Returns empty dict on failure — index entry then has just path/filename.
        """
        try:
            from mutagen import File as MutagenFile
            m = MutagenFile(str(file_path), easy=True)
            if m is None:
                return {}
            tags: dict[str, Any] = {}

            def first(key: str) -> Optional[str]:
                vals = m.get(key)
                if not vals:
                    return None
                return str(vals[0]).strip() or None

            tags["artist"] = first("artist") or first("albumartist")
            tags["album"] = first("album")
            tags["title"] = first("title")
            tags["genre"] = first("genre")

            date_str = first("date") or first("year")
            if date_str:
                try:
                    tags["year"] = int(date_str[:4])
                except (ValueError, IndexError):
                    pass

            track_str = first("tracknumber")
            if track_str:
                try:
                    tags["track"] = int(track_str.split("/")[0])
                except (ValueError, IndexError):
                    pass

            if hasattr(m, "info") and hasattr(m.info, "length"):
                tags["duration"] = float(m.info.length)

            return tags
        except Exception as exc:  # noqa: BLE001 — mutagen has many error types
            log_message(f"AudioIndex: tag read failed for {file_path.name}: {exc}", "warning")
            return {}

    # ── Public API ──────────────────────────────────────────

    def scan_source(
        self,
        source: str,
        root_path: str,
        on_progress: Optional[Any] = None,
        force: bool = False,
    ) -> ScanResult:
        """Walk root_path, update index incrementally based on mtime.

        Returns scan statistics. Files no longer present are removed.

        Args:
            source: source label (e.g. 'nas_music').
            root_path: absolute filesystem root for the source.
            on_progress: optional callback (scanned, inserted, updated)
                         every 50 files for UI updates.
            force: if True, ignore mtime and re-read tags for every file
                   (useful when ID3-tags were mass-edited without changing
                   mtime, or when the index is suspected corrupt).
        """
        start = time.monotonic()
        root = Path(root_path).expanduser().resolve()
        if not root.is_dir():
            return ScanResult(source, 0, 0, 0, 0, 0.0, 1)

        scanned = inserted = updated = errors = 0

        # Track which rel_paths we see — anything in DB not seen will be deleted.
        seen: set[str] = set()

        with self._lock, self._connect() as conn:
            # Build mtime-index of existing rows for fast lookup
            existing: dict[str, tuple[int, int]] = {
                row["rel_path"]: (row["id"], row["mtime"])
                for row in conn.execute(
                    "SELECT id, rel_path, mtime FROM files WHERE source = ?", (source,)
                )
            }

            for fpath in root.rglob("*"):
                if not fpath.is_file():
                    continue
                if fpath.suffix.lower() not in AUDIO_EXTENSIONS:
                    continue
                try:
                    stat = fpath.stat()
                except OSError:
                    errors += 1
                    continue

                try:
                    rel = str(fpath.relative_to(root))
                except ValueError:
                    continue

                seen.add(rel)
                scanned += 1
                mtime = int(stat.st_mtime)

                row = existing.get(rel)
                if not force and row is not None and row[1] == mtime:
                    # Unchanged — skip tag read (force=True bypasses this fast-path)
                    if on_progress and scanned % 50 == 0:
                        on_progress(scanned, inserted, updated)
                    continue

                # New or modified — read tags
                tags = self._read_tags(fpath)
                params = {
                    "source": source,
                    "rel_path": rel,
                    "filename": fpath.name,
                    "mtime": mtime,
                    "size": stat.st_size,
                    "artist": tags.get("artist"),
                    "album": tags.get("album"),
                    "title": tags.get("title"),
                    "year": tags.get("year"),
                    "genre": tags.get("genre"),
                    "duration": tags.get("duration"),
                    "track": tags.get("track"),
                }

                if row is None:
                    conn.execute("""
                        INSERT INTO files (source, rel_path, filename, mtime, size,
                                           artist, album, title, year, genre, duration, track)
                        VALUES (:source, :rel_path, :filename, :mtime, :size,
                                :artist, :album, :title, :year, :genre, :duration, :track)
                    """, params)
                    inserted += 1
                else:
                    params["id"] = row[0]
                    conn.execute("""
                        UPDATE files SET mtime=:mtime, size=:size,
                            artist=:artist, album=:album, title=:title,
                            year=:year, genre=:genre, duration=:duration, track=:track
                        WHERE id = :id
                    """, params)
                    updated += 1

                if on_progress and scanned % 50 == 0:
                    on_progress(scanned, inserted, updated)

            # Delete rows for files that no longer exist on disk
            stale = [rel for rel in existing if rel not in seen]
            for rel in stale:
                conn.execute("DELETE FROM files WHERE source = ? AND rel_path = ?",
                             (source, rel))
            deleted = len(stale)

            conn.commit()

        elapsed = time.monotonic() - start
        log_message(
            f"AudioIndex[{source}]: scanned={scanned} +{inserted} ~{updated} "
            f"-{deleted} errors={errors} in {elapsed:.1f}s"
        )
        return ScanResult(source, scanned, inserted, updated, deleted, elapsed, errors)

    def search(
        self,
        query: str,
        source: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """FTS5 search ranked by BM25.

        query: free-text — tokens AND-combined. Examples:
            "lee dorsey"         → matches artist/album/title/path containing both
            "mozart sonate"      → matches Mozart pieces with 'sonate' in title
            "jazz misbehavin"    → cross-field: genre=Jazz + title contains
        """
        if not query.strip():
            return []
        # Quote the query so dashes/special chars don't break FTS5 syntax
        # FTS5 treats "..." as a phrase; we OR the individual tokens
        tokens = [t for t in query.replace('"', '').split() if t]
        if not tokens:
            return []
        # Build prefix-match query: each token can match anywhere in any column
        match_expr = " ".join(f'"{t}"*' for t in tokens)

        sql = """
            SELECT files.*, bm25(files_fts) AS rank
            FROM files_fts
            JOIN files ON files.id = files_fts.rowid
            WHERE files_fts MATCH ?
        """
        params: list[Any] = [match_expr]
        if source:
            sql += " AND files.source = ?"
            params.append(source)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            try:
                rows = conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError as exc:
                log_message(f"AudioIndex search error: {exc}", "warning")
                return []
        return [dict(r) for r in rows]

    def list_subdir(
        self,
        source: str,
        subdir: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """List files under a relative subdirectory (no recursion into deeper subs).

        subdir="" lists immediate children of the source root.
        """
        prefix = subdir.rstrip("/") + "/" if subdir else ""
        sql = """
            SELECT * FROM files
            WHERE source = ?
              AND rel_path LIKE ? || '%'
            ORDER BY rel_path LIMIT ?
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (source, prefix, limit)).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict[str, Any]:
        """Per-source counts + total."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT source, COUNT(*) AS cnt FROM files GROUP BY source"
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        return {
            "total": total,
            "per_source": {r["source"]: r["cnt"] for r in rows},
        }

    def remove_source(self, source: str) -> int:
        """Delete all index entries for a source. Returns rows affected."""
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM files WHERE source = ?", (source,))
            conn.commit()
            return cur.rowcount

    def clear_all(self) -> int:
        """Wipe the entire index (all sources). Returns rows deleted.

        Use when the index is suspected corrupt — schema is rebuilt from
        scratch on the next instance creation. Files-table rows + FTS
        entries are removed via cascading triggers.
        """
        with self._lock, self._connect() as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            conn.execute("DELETE FROM files")
            conn.commit()
            # VACUUM reclaims disk space + may resolve FTS5 corruption
            try:
                conn.execute("VACUUM")
            except sqlite3.OperationalError:
                pass  # VACUUM can fail if other connections hold locks
            return int(cnt)


audio_index = AudioIndex()


# ── Background incremental sync task ─────────────────────

async def sync_audio_index_task() -> None:
    """Background task: periodically run mtime-based incremental sync.

    Only syncs sources that already have entries in the index — initial
    population must be triggered manually via audio_index_rebuild tool
    (otherwise we'd kick off a multi-minute NAS scan unbidden at startup).
    """
    import asyncio as _asyncio
    import json as _json
    from pathlib import Path as _Path
    from .config import AUDIO_INDEX_SYNC_INTERVAL_HOURS

    log_message(
        f"🗂️ AudioIndex sync task started "
        f"(interval: {AUDIO_INDEX_SYNC_INTERVAL_HOURS}h)"
    )

    settings_path = (
        _Path(__file__).parent.parent / "plugins" / "tools" / "audio_player" / "settings.json"
    )

    while True:
        try:
            await _asyncio.sleep(AUDIO_INDEX_SYNC_INTERVAL_HOURS * 3600)

            # Load current sources from plugin settings
            if not settings_path.exists():
                continue
            try:
                with open(settings_path, encoding="utf-8") as f:
                    cfg = _json.load(f)
            except (OSError, _json.JSONDecodeError):
                continue
            sources = cfg.get("sources", {})

            stats = audio_index.stats()
            for label, src in sources.items():
                if src.get("type") != "local_folder":
                    continue
                # Only sync sources that already have entries — never auto-
                # bootstrap (could take 10+ minutes on a fresh NAS mount).
                if stats["per_source"].get(label, 0) == 0:
                    continue
                path = src.get("path", "")
                if not path:
                    continue
                loop = _asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, audio_index.scan_source, label, path
                )
                if result.inserted + result.updated + result.deleted > 0:
                    log_message(
                        f"🗂️ AudioIndex sync[{label}]: "
                        f"+{result.inserted} ~{result.updated} -{result.deleted} "
                        f"in {result.elapsed_sec:.1f}s"
                    )
        except Exception as exc:  # pragma: no cover  # noqa: BLE001
            log_message(f"⚠️ AudioIndex sync task error: {exc}")

