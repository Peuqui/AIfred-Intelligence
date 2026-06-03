"""Vision-Store — SQLite für Events, Faces, Embeddings und Source-Konfig.

Eine zentrale Datenbank pro AIfred-Installation unter
``data/vision/vision.db``. Schema-Versionierung über ``schema_version``-
Tabelle erlaubt spätere Migrationen ohne Daten-Verlust.

Public API — eine ``VisionStore``-Klasse mit thread-safe Connection-pro-
Operation. Embeddings werden als ``float32``-Blob gespeichert; Bulk-
Matching gegen alle Embeddings ist über ``all_embeddings_with_face()``
effizient möglich (Cosine-Similarity berechnet der Caller mit numpy).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from .config import DATA_DIR

# Default-Speicherort. Tests übergeben einen tmp-Path im Konstruktor.
DEFAULT_DB_PATH = DATA_DIR / "vision" / "vision.db"

SCHEMA_VERSION = 2


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    """Erstelle Tabellen, falls noch nicht da. Reihenfolge wichtig wegen
    Foreign-Key-Constraints (faces ← events ← face_embeddings)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sources (
            source_id      TEXT PRIMARY KEY,
            display_name   TEXT NOT NULL,
            kind           TEXT NOT NULL,
            prompt_context TEXT NOT NULL DEFAULT '',
            position       TEXT NOT NULL DEFAULT '',
            auto_start     INTEGER NOT NULL DEFAULT 0,
            sensitivity    TEXT NOT NULL DEFAULT 'medium',
            settings_json  TEXT NOT NULL DEFAULT '{}',
            created_at     TEXT NOT NULL,
            updated_at     TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS faces (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            notes       TEXT NOT NULL DEFAULT '',
            enrolled_by TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id       TEXT NOT NULL,
            timestamp       TEXT NOT NULL,
            event_type      TEXT NOT NULL,
            frame_path      TEXT NOT NULL DEFAULT '',
            classification  TEXT NOT NULL DEFAULT '{}',
            confidence      REAL NOT NULL DEFAULT 0.0,
            face_id         INTEGER REFERENCES faces(id) ON DELETE SET NULL,
            metadata        TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_events_source_ts
            ON events(source_id, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_events_type_ts
            ON events(event_type, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_events_face_id
            ON events(face_id);

        CREATE TABLE IF NOT EXISTS face_embeddings (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            face_id         INTEGER NOT NULL REFERENCES faces(id) ON DELETE CASCADE,
            embedding       BLOB NOT NULL,
            quality_score   REAL NOT NULL DEFAULT 0.0,
            source_event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
            created_at      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_face_embeddings_face_id
            ON face_embeddings(face_id);
    """)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    current = int(row["version"]) if row else 0
    if row is None:
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)",
            (SCHEMA_VERSION,),
        )
        current = SCHEMA_VERSION
    # Migrationen — idempotent, step-by-step von current → SCHEMA_VERSION.
    # Step v1 → v2: cluster_id für pHash-basierte Bulk-Analyse-Dedup.
    if current < 2:
        cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(events)").fetchall()
        }
        if "cluster_id" not in cols:
            conn.execute(
                "ALTER TABLE events ADD COLUMN cluster_id TEXT NOT NULL DEFAULT ''"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_cluster_id ON events(cluster_id)"
        )
        conn.execute(
            "UPDATE schema_version SET version = ?", (2,)
        )


def _embedding_to_blob(emb: np.ndarray) -> bytes:
    """Pack float32 numpy array to compact bytes."""
    arr = np.ascontiguousarray(emb, dtype=np.float32)
    return arr.tobytes()


def _blob_to_embedding(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="microseconds")


class VisionStore:
    """SQLite-backed Storage für die Vision-Pipeline.

    Eine Instanz pro Prozess, thread-safe pro Operation (jede Methode
    öffnet/schließt eine Connection). Bei sehr hochfrequenten Writes
    wäre eine pooled Connection schneller, aber für Vision-Events
    (~10/s im Worst Case) ist die Per-Operation-Connection ausreichend
    und der Code-Aufwand niedriger.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        conn = _connect(self.db_path)
        try:
            _init_schema(conn)
        finally:
            conn.close()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = _connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────
    # Sources
    # ─────────────────────────────────────────────────────────────

    def upsert_source(
        self,
        source_id: str,
        display_name: str,
        kind: str,
        *,
        prompt_context: str = "",
        position: str = "",
        auto_start: bool = False,
        sensitivity: str = "medium",
        settings: dict[str, Any] | None = None,
    ) -> None:
        now = _now_iso()
        settings_json = json.dumps(settings or {})
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO sources (source_id, display_name, kind, prompt_context,
                                     position, auto_start, sensitivity, settings_json,
                                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    display_name   = excluded.display_name,
                    kind           = excluded.kind,
                    prompt_context = excluded.prompt_context,
                    position       = excluded.position,
                    auto_start     = excluded.auto_start,
                    sensitivity    = excluded.sensitivity,
                    settings_json  = excluded.settings_json,
                    updated_at     = excluded.updated_at
                """,
                (source_id, display_name, kind, prompt_context, position,
                 1 if auto_start else 0, sensitivity, settings_json, now, now),
            )

    def get_source(self, source_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM sources WHERE source_id = ?", (source_id,)
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["auto_start"] = bool(d["auto_start"])
        d["settings"] = json.loads(d.pop("settings_json"))
        return d

    def patch_source_settings(
        self, source_id: str, patch: dict[str, Any]
    ) -> None:
        """Nur einzelne ``settings``-Felder einer Quelle ändern, alle
        anderen Felder (display_name, kind, auto_start …) bleiben erhalten.

        No-op-sicher für unbekannte Quellen: legt sie mit Defaults an, falls
        sie noch nicht existiert (z.B. erste Konfig einer frisch entdeckten
        Cam). Gemeinsamer Persist-Pfad für State und API."""
        existing = self.get_source(source_id) or {}
        new_settings = dict(existing.get("settings") or {})
        new_settings.update(patch)
        self.upsert_source(
            source_id=source_id,
            display_name=str(existing.get("display_name") or source_id),
            kind=str(existing.get("kind") or "webcam"),
            prompt_context=str(existing.get("prompt_context", "")),
            position=str(existing.get("position", "")),
            auto_start=bool(existing.get("auto_start", False)),
            sensitivity=str(existing.get("sensitivity", "medium")),
            settings=new_settings,
        )

    def list_sources(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM sources ORDER BY source_id").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["auto_start"] = bool(d["auto_start"])
            d["settings"] = json.loads(d.pop("settings_json"))
            result.append(d)
        return result

    def delete_source(self, source_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM sources WHERE source_id = ?", (source_id,)
            )
            return cur.rowcount > 0

    # ─────────────────────────────────────────────────────────────
    # Faces (Person-Identitäten)
    # ─────────────────────────────────────────────────────────────

    def add_face(self, name: str, *, notes: str = "", enrolled_by: str = "") -> int:
        now = _now_iso()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO faces (name, notes, enrolled_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, notes, enrolled_by, now, now),
            )
            return int(cur.lastrowid or 0)

    def get_face_by_id(self, face_id: int) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM faces WHERE id = ?", (face_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_face_by_name(self, name: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM faces WHERE name = ?", (name,)
            ).fetchone()
        return dict(row) if row else None

    def list_faces(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM faces ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def delete_face(self, face_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM faces WHERE id = ?", (face_id,))
            return cur.rowcount > 0

    def rename_face(self, face_id: int, new_name: str) -> bool:
        """Umbenennen einer Identity. ``new_name`` muss nicht-leer und
        bisher nicht von einer anderen Identity belegt sein."""
        new_name = new_name.strip()
        if not new_name:
            return False
        now = _now_iso()
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT id FROM faces WHERE name = ? AND id != ?",
                (new_name, face_id),
            ).fetchone()
            if existing:
                raise ValueError(f"face name already taken: {new_name}")
            cur = conn.execute(
                "UPDATE faces SET name = ?, updated_at = ? WHERE id = ?",
                (new_name, now, face_id),
            )
            return cur.rowcount > 0

    def list_faces_with_summary(self) -> list[dict[str, Any]]:
        """Für das Personarium-Modal: pro Face-Identity ein Dict mit
        Name, Anzahl Embeddings, letzter Sichtungs-Zeitpunkt und URL
        des aktuellsten Crops.

        Crop kommt aus dem ``classification.crop_url``-Feld des letzten
        face_known/face_unsure-Events derselben face_id.
        """
        with self._conn() as conn:
            face_rows = conn.execute(
                "SELECT id, name, notes, enrolled_by, created_at, updated_at "
                "FROM faces ORDER BY name"
            ).fetchall()
            result: list[dict[str, Any]] = []
            for f in face_rows:
                fid = int(f["id"])
                # Anzahl Embeddings
                emb_count_row = conn.execute(
                    "SELECT COUNT(*) AS n FROM face_embeddings WHERE face_id = ?",
                    (fid,),
                ).fetchone()
                emb_count = int(emb_count_row["n"]) if emb_count_row else 0
                # Letztes face-Event (für letzte Sichtung + Crop-URL)
                evt = conn.execute(
                    "SELECT timestamp, classification FROM events "
                    "WHERE face_id = ? AND event_type IN "
                    "('face_known','face_unsure') "
                    "ORDER BY timestamp DESC LIMIT 1",
                    (fid,),
                ).fetchone()
                last_seen = str(evt["timestamp"]) if evt else ""
                crop_url = ""
                if evt:
                    try:
                        import json
                        cls = json.loads(evt["classification"] or "{}")
                        crop_url = str(cls.get("crop_url") or "")
                    except Exception:  # noqa: BLE001
                        crop_url = ""
                result.append({
                    "id": fid,
                    "name": str(f["name"]),
                    "notes": str(f["notes"] or ""),
                    "enrolled_by": str(f["enrolled_by"] or ""),
                    "created_at": str(f["created_at"]),
                    "updated_at": str(f["updated_at"]),
                    "embedding_count": emb_count,
                    "last_seen": last_seen,
                    "crop_url": crop_url,
                })
            return result

    def list_face_events(self, face_id: int, limit: int = 50) -> list[dict[str, Any]]:
        """Alle face-Events einer Identity, chronologisch absteigend.
        Liefert die ``classification`` als geparstes Dict mit, damit
        der Caller direkt auf ``crop_url`` und ``confidence_band``
        zugreifen kann."""
        import json
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, source_id, event_type, timestamp, confidence, "
                "classification, metadata FROM events "
                "WHERE face_id = ? AND event_type IN "
                "('face_known','face_unsure') "
                "ORDER BY timestamp DESC LIMIT ?",
                (face_id, limit),
            ).fetchall()
        result = []
        for r in rows:
            try:
                cls = json.loads(r["classification"] or "{}")
            except Exception:  # noqa: BLE001
                cls = {}
            result.append({
                "id": int(r["id"]),
                "source_id": str(r["source_id"]),
                "event_type": str(r["event_type"]),
                "timestamp": str(r["timestamp"]),
                "confidence": float(r["confidence"] or 0.0),
                "crop_url": str(cls.get("crop_url") or ""),
                "confidence_band": str(cls.get("confidence_band") or ""),
            })
        return result

    def delete_face_with_assets(self, face_id: int) -> dict[str, int]:
        """Löscht eine Identity vollständig: Face-Row, alle Embeddings,
        face_id-Referenz in events (auf NULL setzen, damit die Events
        als historischer Datensatz erhalten bleiben — wer das nicht
        will, kann events mit dem Cleanup-Task per TTL räumen).

        Crop-Dateien werden nicht gelöscht — die liegen unter
        ``data/vision/faces/`` und werden vom vision_cleanup-Task per
        TTL aufgeräumt. So bleibt der Rückgängig-Pfad offen (wer die
        Person versehentlich gelöscht hat, kann den Crop noch sehen,
        bis das TTL-Fenster zuschnappt).
        """
        deleted_embeddings = 0
        with self._conn() as conn:
            emb_count = conn.execute(
                "SELECT COUNT(*) AS n FROM face_embeddings WHERE face_id = ?",
                (face_id,),
            ).fetchone()
            if emb_count:
                deleted_embeddings = int(emb_count["n"])
            conn.execute(
                "DELETE FROM face_embeddings WHERE face_id = ?",
                (face_id,),
            )
            conn.execute(
                "UPDATE events SET face_id = NULL WHERE face_id = ?",
                (face_id,),
            )
            conn.execute("DELETE FROM faces WHERE id = ?", (face_id,))
        return {
            "embeddings_deleted": deleted_embeddings,
        }

    # ─────────────────────────────────────────────────────────────
    # Face-Embeddings
    # ─────────────────────────────────────────────────────────────

    def add_embedding(
        self,
        face_id: int,
        embedding: np.ndarray,
        *,
        quality_score: float = 0.0,
        source_event_id: int | None = None,
    ) -> int:
        now = _now_iso()
        blob = _embedding_to_blob(embedding)
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO face_embeddings (face_id, embedding, quality_score, "
                "source_event_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (face_id, blob, quality_score, source_event_id, now),
            )
            return int(cur.lastrowid or 0)

    def list_embeddings(self, face_id: int | None = None) -> list[dict[str, Any]]:
        """Liste aller Embeddings, optional nach ``face_id`` gefiltert.
        Embedding wird in ein numpy-Array dekodiert."""
        query = "SELECT * FROM face_embeddings"
        params: tuple[Any, ...] = ()
        if face_id is not None:
            query += " WHERE face_id = ?"
            params = (face_id,)
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {**dict(r), "embedding": _blob_to_embedding(r["embedding"])}
            for r in rows
        ]

    def all_embeddings_with_face(self) -> list[tuple[int, str, np.ndarray]]:
        """Bulk-Lookup für Face-Match: ``(face_id, name, embedding)`` für alle
        registrierten Embeddings. Caller berechnet Cosine-Similarity selbst
        (numpy, vektorisiert) — die Datenmenge ist klein genug (typisch
        < 10k Embeddings), dass kein VSS-Extension nötig ist."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT fe.face_id, f.name, fe.embedding
                FROM face_embeddings fe
                JOIN faces f ON f.id = fe.face_id
                """
            ).fetchall()
        return [
            (int(r["face_id"]), str(r["name"]), _blob_to_embedding(r["embedding"]))
            for r in rows
        ]

    def delete_embedding(self, embedding_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM face_embeddings WHERE id = ?", (embedding_id,)
            )
            return cur.rowcount > 0

    # ─────────────────────────────────────────────────────────────
    # Events
    # ─────────────────────────────────────────────────────────────

    def add_event(
        self,
        source_id: str,
        event_type: str,
        *,
        timestamp: datetime | None = None,
        frame_path: str = "",
        classification: dict[str, Any] | None = None,
        confidence: float = 0.0,
        face_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        ts = (timestamp or datetime.now()).isoformat(timespec="microseconds")
        cls_json = json.dumps(classification or {})
        meta_json = json.dumps(metadata or {})
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO events (source_id, timestamp, event_type, frame_path,
                                    classification, confidence, face_id, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (source_id, ts, event_type, frame_path, cls_json, confidence,
                 face_id, meta_json),
            )
            return int(cur.lastrowid or 0)

    def query_events(
        self,
        *,
        source_id: str | None = None,
        event_type: str | None = None,
        face_id: int | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = 100,
    ) -> list[dict[str, Any]]:
        """``limit=None`` → kein LIMIT (alle passenden Events). Genutzt vom
        Cluster-Dedup, das das gesamte Zeitfenster sehen muss, damit kein
        Vorkommnis durch eine künstliche Obergrenze durchrutscht."""
        clauses: list[str] = []
        params: list[Any] = []
        if source_id is not None:
            clauses.append("source_id = ?")
            params.append(source_id)
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        if face_id is not None:
            clauses.append("face_id = ?")
            params.append(face_id)
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since.isoformat(timespec="microseconds"))
        if until is not None:
            clauses.append("timestamp <= ?")
            params.append(until.isoformat(timespec="microseconds"))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        query = f"SELECT * FROM events{where} ORDER BY timestamp DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [
            {
                **dict(r),
                "classification": json.loads(r["classification"]),
                "metadata": json.loads(r["metadata"]),
            }
            for r in rows
        ]

    def get_event(self, event_id: int) -> dict[str, Any] | None:
        """Ein einzelnes Event per ID (mit geparster classification/metadata),
        oder ``None``. Direkter Primary-Key-Lookup — anders als ein Scan der
        jüngsten N Events findet das auch alte Events zuverlässig."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM events WHERE id = ?", (int(event_id),)
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["classification"] = json.loads(d.get("classification") or "{}")
        d["metadata"] = json.loads(d.get("metadata") or "{}")
        return d

    def list_events_with_summary(
        self,
        *,
        source_id: str | None = None,
        event_types: list[str] | None = None,
        face_id: int | None = None,
        unknown_only: bool = False,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Listet Events für die Casus-Ereignisverwaltung. Pro Event ein
        Dict mit den UI-relevanten Feldern (Zeit, Quelle, Typ, Confidence,
        Crop-URL aus classification, Face-Name per JOIN, Confidence-Band).

        ``unknown_only=True`` filtert auf ``face_id IS NULL`` — nützlich
        für den "nachtaggen"-Workflow im Casus-Modal.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if source_id:
            clauses.append("e.source_id = ?")
            params.append(source_id)
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            clauses.append(f"e.event_type IN ({placeholders})")
            params.extend(event_types)
        if face_id is not None:
            clauses.append("e.face_id = ?")
            params.append(face_id)
        if unknown_only:
            clauses.append("e.face_id IS NULL")
        if since is not None:
            clauses.append("e.timestamp >= ?")
            params.append(since.isoformat(timespec="microseconds"))
        if until is not None:
            clauses.append("e.timestamp <= ?")
            params.append(until.isoformat(timespec="microseconds"))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        query = (
            "SELECT e.id, e.source_id, e.event_type, e.timestamp, e.confidence, "
            "e.classification, e.frame_path, e.face_id, e.cluster_id, "
            "f.name AS face_name "
            "FROM events e "
            "LEFT JOIN faces f ON f.id = e.face_id"
            f"{where} "
            "ORDER BY e.timestamp DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        with self._conn() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        result: list[dict[str, Any]] = []
        for r in rows:
            try:
                cls = json.loads(r["classification"] or "{}")
            except Exception:  # noqa: BLE001
                cls = {}
            ts_iso = str(r["timestamp"])
            # Pre-render date + time strings so the UI doesn't have to
            # juggle Reflex Var string ops. ISO timestamps look like
            # "2026-05-28T19:31:12.123456" — split on T, reorder the
            # date to DE format DD.MM.YYYY, trim microseconds off time.
            date_display = ""
            time_display = ""
            try:
                date_part, time_part = ts_iso.split("T", 1)
                y, m, d = date_part.split("-")
                date_display = f"{d}.{m}.{y}"
                time_display = time_part.split(".", 1)[0]
            except (ValueError, IndexError):
                # Malformed timestamp — fall back to raw, UI shows what's there
                date_display = ts_iso[:10]
                time_display = ts_iso[11:19]
            result.append({
                "id": int(r["id"]),
                "source_id": str(r["source_id"]),
                "event_type": str(r["event_type"]),
                "timestamp": ts_iso,
                "date_display": date_display,
                "time_display": time_display,
                "confidence": float(r["confidence"] or 0.0),
                "crop_url": str(cls.get("crop_url") or ""),
                "confidence_band": str(cls.get("confidence_band") or ""),
                "matched_name": str(cls.get("matched_name") or ""),
                "frame_path": str(r["frame_path"] or ""),
                "face_id": int(r["face_id"]) if r["face_id"] is not None else None,
                "face_name": str(r["face_name"]) if r["face_name"] else "",
                "area_ratio": float(cls.get("area_ratio") or 0.0),
                "description": str(cls.get("description") or ""),
                "cluster_id": str(r["cluster_id"] or ""),
            })
        return result

    def count_events(
        self,
        *,
        source_id: str | None = None,
        event_types: list[str] | None = None,
        face_id: int | None = None,
        unknown_only: bool = False,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> int:
        """Wie ``list_events_with_summary``, gibt aber nur Anzahl zurück —
        für Paginierung im Casus-Modal."""
        clauses: list[str] = []
        params: list[Any] = []
        if source_id:
            clauses.append("source_id = ?")
            params.append(source_id)
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            clauses.append(f"event_type IN ({placeholders})")
            params.extend(event_types)
        if face_id is not None:
            clauses.append("face_id = ?")
            params.append(face_id)
        if unknown_only:
            clauses.append("face_id IS NULL")
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since.isoformat(timespec="microseconds"))
        if until is not None:
            clauses.append("timestamp <= ?")
            params.append(until.isoformat(timespec="microseconds"))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM events{where}", tuple(params)
            ).fetchone()
        return int(row["n"]) if row else 0

    def list_event_ids(
        self,
        *,
        source_id: str | None = None,
        event_types: list[str] | None = None,
        face_id: int | None = None,
        unknown_only: bool = False,
        limit: int = 10000,
    ) -> list[int]:
        """Nur die Event-IDs der gefilterten Menge, timestamp DESC —
        identische Filter-/Sortierlogik wie ``list_events_with_summary``,
        aber leichtgewichtig (keine Joins, keine Klassifikation). Treibt
        die seitenübergreifende Vollbild-Slideshow im Casus, ohne dass
        alle Frames/Metadaten geladen werden müssen."""
        clauses: list[str] = []
        params: list[Any] = []
        if source_id:
            clauses.append("source_id = ?")
            params.append(source_id)
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            clauses.append(f"event_type IN ({placeholders})")
            params.extend(event_types)
        if face_id is not None:
            clauses.append("face_id = ?")
            params.append(face_id)
        if unknown_only:
            clauses.append("face_id IS NULL")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT id FROM events{where} ORDER BY timestamp DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [int(r["id"]) for r in rows]

    def list_event_source_ids(self) -> list[str]:
        """Distinct ``source_id`` aller je gespeicherten Events. Für
        das Casus-Filter-Dropdown — zeigt nur Quellen, von denen
        überhaupt Events vorliegen."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT source_id FROM events ORDER BY source_id"
            ).fetchall()
        return [str(r["source_id"]) for r in rows]

    def set_event_cluster(self, event_id: int, cluster_id: str) -> bool:
        """cluster_id eines Events setzen — vom Bulk-Worker (Story 4)
        nach pHash-Cluster-Berechnung gerufen."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE events SET cluster_id = ? WHERE id = ?",
                (cluster_id, event_id),
            )
            return cur.rowcount > 0

    def list_events_without_description(
        self,
        *,
        source_id: str | None = None,
        event_types: list[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = 5000,
    ) -> list[dict[str, Any]]:
        """Events die noch keine VLM-Beschreibung haben. Genau das,
        was der Bulk-Worker braucht — sortiert chronologisch (älteste
        zuerst, weil sie sich für Time-Bucket-Clustering eignen).
        ``limit=None`` → kein LIMIT (alle unbeschriebenen im Fenster).

        ``since`` / ``until`` grenzen das Zeitfenster ein — vom On-demand-
        Chat-Hook genutzt, der nur die gerade abgefragte Spanne beschreibt
        statt des ganzen Backlogs."""
        clauses: list[str] = ["(json_extract(classification, '$.description') IS NULL"
                              " OR json_extract(classification, '$.description') = '')"]
        params: list[Any] = []
        if source_id:
            clauses.append("source_id = ?")
            params.append(source_id)
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            clauses.append(f"event_type IN ({placeholders})")
            params.extend(event_types)
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since.isoformat(timespec="microseconds"))
        if until is not None:
            clauses.append("timestamp <= ?")
            params.append(until.isoformat(timespec="microseconds"))
        # Nur Events mit Frame-Pfad — sonst kein Bild zum Analysieren.
        clauses.append("frame_path != ''")
        where = " WHERE " + " AND ".join(clauses)
        query = (
            "SELECT id, source_id, event_type, timestamp, frame_path, "
            "classification, cluster_id "
            f"FROM events{where} ORDER BY timestamp ASC"
        )
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        result: list[dict[str, Any]] = []
        for r in rows:
            try:
                cls = json.loads(r["classification"] or "{}")
            except Exception:  # noqa: BLE001
                cls = {}
            result.append({
                "id": int(r["id"]),
                "source_id": str(r["source_id"]),
                "event_type": str(r["event_type"]),
                "timestamp": str(r["timestamp"]),
                "frame_path": str(r["frame_path"]),
                "cluster_id": str(r["cluster_id"] or ""),
                "classification": cls,
            })
        return result

    def apply_cluster_description(
        self, cluster_id: str, description: str, analyzed_by: str,
    ) -> int:
        """Beschreibung an alle Events eines Clusters anwenden — wird
        vom Bulk-Worker nach VLM-Call auf den Repräsentanten gerufen,
        damit auch die anderen Cluster-Mitglieder als „analysiert"
        zählen, ohne dass VLM jedes einzeln durchlief. Returnt Anzahl
        der aktualisierten Zeilen."""
        from datetime import datetime
        analyzed_at = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, classification FROM events WHERE cluster_id = ?",
                (cluster_id,),
            ).fetchall()
            updated = 0
            for r in rows:
                try:
                    cls = json.loads(r["classification"] or "{}")
                except Exception:  # noqa: BLE001
                    cls = {}
                cls["description"] = description
                cls["analyzed_at"] = analyzed_at
                cls["analyzed_by"] = analyzed_by
                cls["analyzed_via"] = "cluster"
                conn.execute(
                    "UPDATE events SET classification = ? WHERE id = ?",
                    (json.dumps(cls), int(r["id"])),
                )
                updated += 1
            return updated

    def delete_events_filtered(
        self,
        *,
        source_id: str | None = None,
        event_types: list[str] | None = None,
        face_id: int | None = None,
        unknown_only: bool = False,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> int:
        """Bulk-Delete mit denselben Filter-Parametern wie
        ``list_events_with_summary`` / ``count_events``. Returnt die
        Anzahl gelöschter Zeilen."""
        clauses: list[str] = []
        params: list[Any] = []
        if source_id:
            clauses.append("source_id = ?")
            params.append(source_id)
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            clauses.append(f"event_type IN ({placeholders})")
            params.extend(event_types)
        if face_id is not None:
            clauses.append("face_id = ?")
            params.append(face_id)
        if unknown_only:
            clauses.append("face_id IS NULL")
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since.isoformat(timespec="microseconds"))
        if until is not None:
            clauses.append("timestamp <= ?")
            params.append(until.isoformat(timespec="microseconds"))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._conn() as conn:
            cur = conn.execute(f"DELETE FROM events{where}", tuple(params))
            return int(cur.rowcount)

    def delete_event(self, event_id: int) -> bool:
        """Einzelnes Event löschen. Embeddings, die dieses Event als
        ``source_event_id`` referenzieren, bekommen NULL (ON DELETE SET
        NULL aus dem Schema)."""
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
            return cur.rowcount > 0

    def set_event_face_id(self, event_id: int, face_id: int | None) -> bool:
        """Tagging-Workflow: ein unknown-/unsure-Event nachträglich einer
        Identity zuordnen (oder die Zuordnung lösen). Embedding wird
        nicht automatisch erstellt — das macht das Personarium beim
        Multi-Pose-Lernen über die Embedding-Pipeline."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE events SET face_id = ? WHERE id = ?",
                (face_id, event_id),
            )
            return cur.rowcount > 0

    def get_event_frame_path(self, event_id: int) -> str:
        """Frame-Pfad (gespeichertes Vollbild) eines Events per ID.

        Leerer String, wenn das Event nicht existiert oder kein Frame
        gespeichert wurde. Für den Frame-Serving-Endpoint (Casus-Thumbnail
        + Bild-Modal)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT frame_path FROM events WHERE id = ?", (int(event_id),)
            ).fetchone()
        return str(row["frame_path"]) if row and row["frame_path"] else ""

    def prune_events(self, older_than: datetime) -> int:
        """Lösche Events älter als ``older_than``. Gibt Anzahl gelöschter
        Zeilen zurück. Für Retention-Cronjob (Schicht 6) gedacht."""
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM events WHERE timestamp < ?",
                (older_than.isoformat(timespec="microseconds"),),
            )
            return int(cur.rowcount)

    def schema_version(self) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT version FROM schema_version").fetchone()
        return int(row["version"]) if row else 0
