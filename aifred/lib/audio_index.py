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

                -- Folder-mtime tracking: lets scan_source skip whole
                -- subtrees that haven't been touched since the last scan.
                -- Linux folder mtime changes when direct children are
                -- added/removed/renamed (NOT when their content changes
                -- or when nested folders change) — perfect cheap skip.
                CREATE TABLE IF NOT EXISTS folders (
                    id        INTEGER PRIMARY KEY,
                    source    TEXT NOT NULL,
                    rel_path  TEXT NOT NULL,
                    mtime     INTEGER NOT NULL,
                    UNIQUE(source, rel_path)
                );
                CREATE INDEX IF NOT EXISTS idx_folders_source ON folders(source);

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

            self._migrate_fts_add_genre(conn)

    def _migrate_fts_add_genre(self, conn: sqlite3.Connection) -> None:
        """Migration: alte FTS5-Tabellen ohne ``genre``-Spalte neu aufbauen.

        ``CREATE VIRTUAL TABLE IF NOT EXISTS`` greift nicht wenn die Tabelle
        schon existiert — alte DBs (vor Genre-Schema) wären sonst dauerhaft
        ohne Genre-Suche, obwohl die Spalte in der Quelltabelle ``files``
        längst gefüllt ist. Diese Migration:
          1. Prüft ob ``genre`` in den FTS-Spalten fehlt
          2. Wenn ja: dropt FTS + Triggers, baut neu auf, repopuliert aus
             ``files`` (kein erneutes Tag-Reading nötig — Daten sind da)

        Idempotent: nach erfolgreicher Migration ist genre drin und die
        Funktion ist beim nächsten Start ein No-op.
        """
        cols = [r[1] for r in conn.execute("PRAGMA table_info(files_fts)").fetchall()]
        if "genre" in cols:
            return

        from .logging_utils import log_message
        log_message("🔧 audio_index: Migrating FTS5 schema — adding 'genre' column")

        conn.executescript("""
            DROP TRIGGER IF EXISTS files_ai;
            DROP TRIGGER IF EXISTS files_ad;
            DROP TRIGGER IF EXISTS files_au;
            DROP TABLE  IF EXISTS files_fts;

            CREATE VIRTUAL TABLE files_fts USING fts5(
                artist, album, title, genre, filename, rel_path,
                content='files', content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            );

            CREATE TRIGGER files_ai AFTER INSERT ON files BEGIN
                INSERT INTO files_fts(rowid, artist, album, title, genre, filename, rel_path)
                VALUES (new.id, new.artist, new.album, new.title, new.genre, new.filename, new.rel_path);
            END;
            CREATE TRIGGER files_ad AFTER DELETE ON files BEGIN
                INSERT INTO files_fts(files_fts, rowid, artist, album, title, genre, filename, rel_path)
                VALUES ('delete', old.id, old.artist, old.album, old.title, old.genre, old.filename, old.rel_path);
            END;
            CREATE TRIGGER files_au AFTER UPDATE ON files BEGIN
                INSERT INTO files_fts(files_fts, rowid, artist, album, title, genre, filename, rel_path)
                VALUES ('delete', old.id, old.artist, old.album, old.title, old.genre, old.filename, old.rel_path);
                INSERT INTO files_fts(rowid, artist, album, title, genre, filename, rel_path)
                VALUES (new.id, new.artist, new.album, new.title, new.genre, new.filename, new.rel_path);
            END;

            INSERT INTO files_fts(rowid, artist, album, title, genre, filename, rel_path)
            SELECT id, artist, album, title, genre, filename, rel_path FROM files;
        """)

        count = conn.execute("SELECT COUNT(*) FROM files_fts").fetchone()[0]
        log_message(f"✅ audio_index: FTS5 schema migrated — {count} files re-indexed (genre searchable)")

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
        """Walk root_path, update index incrementally.

        Optimization: a per-folder mtime cache lets us skip whole subtrees
        whose folder.mtime hasn't changed since the last scan. Linux
        folder mtime updates when direct children are added/removed/
        renamed — exactly when our index needs an update.

        Tag-only edits (e.g. mutagen rewrites a file in place without
        removing/adding any siblings) update file.mtime but NOT folder.
        mtime. To pick those up, the user must use force=True.

        Args:
            source: source label.
            root_path: absolute filesystem root.
            on_progress: callback (scanned, inserted, updated).
            force: if True, ignore both folder.mtime and file.mtime
                   caches and re-tag everything.
        """
        start = time.monotonic()
        root = Path(root_path).expanduser().resolve()
        if not root.is_dir():
            return ScanResult(source, 0, 0, 0, 0, 0.0, 1)

        stats = {"scanned": 0, "inserted": 0, "updated": 0, "errors": 0,
                 "skipped_folders": 0}

        # Tracks files we *know* still exist (via either re-stat or "folder
        # unchanged → all its files are implicitly seen"). Anything in DB
        # not in this set will be deleted at the end.
        seen_files: set[str] = set()
        seen_folders: set[str] = set()

        with self._lock, self._connect() as conn:
            existing_files: dict[str, tuple[int, int]] = {
                row["rel_path"]: (row["id"], row["mtime"])
                for row in conn.execute(
                    "SELECT id, rel_path, mtime FROM files WHERE source = ?", (source,)
                )
            }
            existing_folders: dict[str, int] = {
                row["rel_path"]: row["mtime"]
                for row in conn.execute(
                    "SELECT rel_path, mtime FROM folders WHERE source = ?", (source,)
                )
            }

            def process_file(fpath: Path, rel: str) -> None:
                try:
                    fstat = fpath.stat()
                except OSError:
                    stats["errors"] += 1
                    return
                seen_files.add(rel)
                stats["scanned"] += 1
                mtime = int(fstat.st_mtime)
                row = existing_files.get(rel)
                if not force and row is not None and row[1] == mtime:
                    return  # unchanged — skip tag read
                tags = self._read_tags(fpath)
                params = {
                    "source": source, "rel_path": rel, "filename": fpath.name,
                    "mtime": mtime, "size": fstat.st_size,
                    "artist": tags.get("artist"), "album": tags.get("album"),
                    "title": tags.get("title"), "year": tags.get("year"),
                    "genre": tags.get("genre"), "duration": tags.get("duration"),
                    "track": tags.get("track"),
                }
                if row is None:
                    conn.execute("""
                        INSERT INTO files (source, rel_path, filename, mtime, size,
                                           artist, album, title, year, genre, duration, track)
                        VALUES (:source, :rel_path, :filename, :mtime, :size,
                                :artist, :album, :title, :year, :genre, :duration, :track)
                    """, params)
                    stats["inserted"] += 1
                else:
                    params["id"] = row[0]
                    conn.execute("""
                        UPDATE files SET mtime=:mtime, size=:size,
                            artist=:artist, album=:album, title=:title,
                            year=:year, genre=:genre, duration=:duration, track=:track
                        WHERE id = :id
                    """, params)
                    stats["updated"] += 1
                if on_progress and stats["scanned"] % 50 == 0:
                    on_progress(stats["scanned"], stats["inserted"], stats["updated"])

            def mark_folder_files_seen(rel_folder: str) -> None:
                """Mark all DB files + sub-folder cache rows under this
                folder as seen, so the cleanup step at the end doesn't
                delete them."""
                if not rel_folder:
                    # Root: everything is "under" this folder
                    seen_files.update(existing_files.keys())
                    seen_folders.update(existing_folders.keys())
                    return
                prefix = rel_folder + "/"
                for db_rel in existing_files:
                    if db_rel.startswith(prefix):
                        seen_files.add(db_rel)
                for db_folder in existing_folders:
                    if db_folder == rel_folder or db_folder.startswith(prefix):
                        seen_folders.add(db_folder)

            def walk(folder_path: Path, rel_folder: str) -> None:
                try:
                    folder_stat = folder_path.stat()
                except OSError:
                    stats["errors"] += 1
                    return
                folder_mtime = int(folder_stat.st_mtime)
                seen_folders.add(rel_folder)

                # Fast-path: folder unchanged → skip recursion entirely
                cached_mtime = existing_folders.get(rel_folder)
                if not force and cached_mtime is not None and cached_mtime == folder_mtime:
                    mark_folder_files_seen(rel_folder)
                    stats["skipped_folders"] += 1
                    return

                # Folder changed (or first scan) — list children
                try:
                    children = list(folder_path.iterdir())
                except OSError:
                    stats["errors"] += 1
                    return
                for child in children:
                    try:
                        rel_child = str(child.relative_to(root))
                    except ValueError:
                        continue
                    if child.is_dir():
                        walk(child, rel_child)
                    elif child.is_file():
                        if child.suffix.lower() in AUDIO_EXTENSIONS:
                            process_file(child, rel_child)

                # Persist this folder's mtime for next-scan fast-path
                conn.execute("""
                    INSERT INTO folders (source, rel_path, mtime) VALUES (?, ?, ?)
                    ON CONFLICT(source, rel_path) DO UPDATE SET mtime = excluded.mtime
                """, (source, rel_folder, folder_mtime))

            walk(root, "")

            # Delete files no longer on disk
            stale_files = [rel for rel in existing_files if rel not in seen_files]
            for rel in stale_files:
                conn.execute("DELETE FROM files WHERE source = ? AND rel_path = ?",
                             (source, rel))
            deleted = len(stale_files)

            # Delete folder-cache rows for folders that no longer exist
            stale_folders = [rel for rel in existing_folders if rel not in seen_folders]
            for rel in stale_folders:
                conn.execute("DELETE FROM folders WHERE source = ? AND rel_path = ?",
                             (source, rel))

            conn.commit()

        elapsed = time.monotonic() - start
        log_message(
            f"AudioIndex[{source}]: scanned={stats['scanned']} "
            f"+{stats['inserted']} ~{stats['updated']} -{deleted} "
            f"skipped_folders={stats['skipped_folders']} "
            f"errors={stats['errors']} in {elapsed:.1f}s"
        )
        return ScanResult(
            source, stats["scanned"], stats["inserted"], stats["updated"],
            deleted, elapsed, stats["errors"],
        )

    def search(
        self,
        query: str,
        source: Optional[str] = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """FTS5 search ranked by BM25.

        query: free-text — tokens AND-combined. Examples:
            "lee dorsey"         → matches artist/album/title/path containing both
            "mozart sonate"      → matches Mozart pieces with 'sonate' in title
            "jazz misbehavin"    → cross-field: genre=Jazz + title contains

        limit=None (default) returns ALL matches — caller is responsible for
        budget. Use a positive int to cap.
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
        sql += " ORDER BY rank"
        if limit is not None and limit > 0:
            sql += " LIMIT ?"
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
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """List files under a relative subdirectory, recursively.

        subdir="" lists ALL files under the source root (recursive).
        limit=None (default) returns everything — caller is responsible
        for budget. Listings are sorted by rel_path for stable ordering.
        """
        prefix = subdir.rstrip("/") + "/" if subdir else ""
        if limit is None or limit <= 0:
            sql = """
                SELECT * FROM files
                WHERE source = ?
                  AND rel_path LIKE ? || '%'
                ORDER BY rel_path
            """
            params: tuple = (source, prefix)
        else:
            sql = """
                SELECT * FROM files
                WHERE source = ?
                  AND rel_path LIKE ? || '%'
                ORDER BY rel_path LIMIT ?
            """
            params = (source, prefix, limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_genre(self, source: str, rel_path: str) -> Optional[str]:
        """Quick lookup: read the indexed Genre tag for one item.

        Used by audio_type-resolution to derive music/speech/alarm from
        the original ID3/Vorbis/MP4 tag. Returns ``None`` if the item
        isn't indexed or has no genre.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT genre FROM files WHERE source = ? AND rel_path = ? LIMIT 1",
                (source, rel_path),
            ).fetchone()
        if row is None:
            return None
        genre = row["genre"]
        return str(genre) if genre else None

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
        """Delete all index entries for a source. Returns file rows affected.
        Also clears the folder-mtime cache for this source.
        """
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM files WHERE source = ?", (source,))
            conn.execute("DELETE FROM folders WHERE source = ?", (source,))
            conn.commit()
            return cur.rowcount

    def clear_all(self) -> int:
        """Wipe the entire index (all sources). Returns file rows deleted.

        Use when the index is suspected corrupt — schema is rebuilt from
        scratch on the next instance creation. Files-table rows + FTS
        entries are removed via cascading triggers, folder cache too.
        """
        with self._lock, self._connect() as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            conn.execute("DELETE FROM files")
            conn.execute("DELETE FROM folders")
            conn.commit()
            try:
                conn.execute("VACUUM")
            except sqlite3.OperationalError:
                pass
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
    from .audio_sources import build_source_map
    from .config import AUDIO_INDEX_SYNC_INTERVAL_HOURS, MEDIA_AUDIO_DIR

    log_message(
        f"🗂️ AudioIndex sync task started "
        f"(interval: {AUDIO_INDEX_SYNC_INTERVAL_HOURS}h)"
    )

    while True:
        try:
            await _asyncio.sleep(AUDIO_INDEX_SYNC_INTERVAL_HOURS * 3600)

            # Local folders are auto-discovered from MEDIA_AUDIO_DIR — the
            # plugin settings.json only holds http_streams (not indexable).
            # Same source map the rebuild tool and the UI use.
            sources = {
                k: v for k, v in build_source_map(MEDIA_AUDIO_DIR, {}).items()
                if v.get("type") == "local_folder"
            }

            stats = audio_index.stats()
            for label, src in sources.items():
                # Only sync sources that already have entries — never auto-
                # bootstrap (could take 10+ minutes on a fresh NAS mount).
                if stats["per_source"].get(label, 0) == 0:
                    continue
                path = src.get("path", "")
                if not path:
                    continue
                loop = _asyncio.get_running_loop()
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

