"""Vision-Store — SQLite für Events, Faces, Embeddings und Source-Konfig.

Eine zentrale Datenbank pro AIfred-Installation unter
``data/vision/vision.db``. Das Schema ist rein deklarativ (``CREATE TABLE IF
NOT EXISTS``) — keine Migrationen; bei einer inkompatiblen Schema-Änderung
wird die DB neu aufgesetzt.

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
            metadata        TEXT NOT NULL DEFAULT '{}',
            cluster_id      TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_events_source_ts
            ON events(source_id, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_events_type_ts
            ON events(event_type, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_events_face_id
            ON events(face_id);
        CREATE INDEX IF NOT EXISTS idx_events_cluster_id
            ON events(cluster_id);

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
    # Additive Migration: crop_url pro Embedding (für den Embedding-Manager —
    # zeigt das Gesichts-Crop je Embedding). Alte Embeddings haben '' (kein
    # Crop), neue Anlagen speichern es mit.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(face_embeddings)")}
    if "crop_url" not in cols:
        conn.execute(
            "ALTER TABLE face_embeddings ADD COLUMN crop_url TEXT NOT NULL DEFAULT ''"
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

    def update_source_fields(
        self,
        source_id: str,
        *,
        fallback_display_name: str = "",
        fallback_kind: str = "webcam",
        **changes: Any,
    ) -> None:
        """Einzelne Top-Level-Felder einer Quelle ändern, alle übrigen
        erhalten (SSOT für das Load-Merge-Write-Muster der Mixins).

        ``changes`` akzeptiert die upsert_source-Felder (prompt_context,
        position, auto_start, sensitivity, settings). Existiert die Quelle
        noch nicht, wird sie mit den Fallbacks angelegt."""
        existing = self.get_source(source_id) or {}
        self.upsert_source(
            source_id=source_id,
            display_name=str(
                existing.get("display_name") or fallback_display_name or source_id
            ),
            kind=str(existing.get("kind") or fallback_kind or "webcam"),
            prompt_context=str(
                changes.get("prompt_context", existing.get("prompt_context", ""))
            ),
            position=str(changes.get("position", existing.get("position", ""))),
            auto_start=bool(changes.get("auto_start", existing.get("auto_start", False))),
            sensitivity=str(
                changes.get("sensitivity", existing.get("sensitivity", "medium"))
            ),
            settings=dict(changes.get("settings", existing.get("settings") or {})),
        )

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
        self.update_source_fields(source_id, settings=new_settings)

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

    @staticmethod
    def source_label(rec: dict[str, Any]) -> str:
        """SSoT für den Kamera-Anzeigenamen: User-Alias (settings.alias) vor
        Hardware-Name (display_name), sonst source_id. Erwartet ein Source-
        Record-Dict (settings bereits geparst, wie aus get_source/list_sources).
        ALLE Stellen, die einen Kamera-Namen anzeigen, gehen hierüber."""
        alias = str((rec.get("settings") or {}).get("alias") or "").strip()
        return alias or str(rec.get("display_name") or rec.get("source_id") or "")

    def source_labels(self) -> dict[str, str]:
        """Map ``source_id`` → Anzeigename (via :meth:`source_label`). Eine
        Query — zum Anreichern von Source-/Event-Listen ohne Per-Row-Lookup."""
        return {
            str(rec["source_id"]): self.source_label(rec)
            for rec in self.list_sources()
        }

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

    def get_or_create_face(self, name: str, *, notes: str = "", enrolled_by: str = "") -> int:
        """face_id zu ``name`` — legt die Identity an, falls sie fehlt.

        SSoT für alle Enroll-Pfade (Tool, Popup-API, Multipose). Das frühere
        check-then-insert an den Call-Sites hatte ein TOCTOU-Fenster: zwei
        parallele Enrolls desselben Namens → UNIQUE-Verletzung. Hier erledigt
        ``ON CONFLICT(name) DO NOTHING`` + Re-Select beides in EINER
        Transaktion. Bei bestehender Identity bleiben notes/enrolled_by
        unverändert (wie bisher)."""
        now = _now_iso()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO faces (name, notes, enrolled_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(name) DO NOTHING",
                (name, notes, enrolled_by, now, now),
            )
            row = conn.execute(
                "SELECT id FROM faces WHERE name = ?", (name,)
            ).fetchone()
            return int(row["id"])

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
                if not crop_url:
                    # Kein Live-Erkennungs-Event bisher (frisch angelegte
                    # Identität, noch keine Kamera-Sichtung) — Fallback auf
                    # das beste eigene Embedding-Crop, statt den Platzhalter
                    # zu zeigen obwohl schon Gesichtsbilder da sind.
                    best_emb = conn.execute(
                        "SELECT crop_url FROM face_embeddings WHERE face_id = ? "
                        "AND crop_url != '' ORDER BY quality_score DESC LIMIT 1",
                        (fid,),
                    ).fetchone()
                    if best_emb:
                        crop_url = str(best_emb["crop_url"])
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
        crop_url: str = "",
    ) -> int:
        now = _now_iso()
        blob = _embedding_to_blob(embedding)
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO face_embeddings (face_id, embedding, quality_score, "
                "source_event_id, crop_url, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (face_id, blob, quality_score, source_event_id, crop_url, now),
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
        cluster_id: str = "",
    ) -> int:
        ts = (timestamp or datetime.now()).isoformat(timespec="microseconds")
        cls_json = json.dumps(classification or {})
        meta_json = json.dumps(metadata or {})
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO events (source_id, timestamp, event_type, frame_path,
                                    classification, confidence, face_id, metadata,
                                    cluster_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (source_id, ts, event_type, frame_path, cls_json, confidence,
                 face_id, meta_json, cluster_id),
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
        # Kamera-Anzeigenamen über die SSoT-Map (source_label) anreichern —
        # eine Query für alle Quellen, dann Dict-Lookup pro Event.
        labels = self.source_labels()
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
                "source_name": labels.get(str(r["source_id"]), str(r["source_id"])),
                "event_type": str(r["event_type"]),
                "timestamp": ts_iso,
                "date_display": date_display,
                "time_display": time_display,
                "confidence": float(r["confidence"] or 0.0),
                "crop_url": str(cls.get("crop_url") or ""),
                "detection_score": float(cls.get("detection_score") or 0.0),
                "untagged_dismissed": bool(cls.get("untagged_dismissed")),
                "confidence_band": str(cls.get("confidence_band") or ""),
                "matched_name": str(cls.get("matched_name") or ""),
                "frame_path": str(r["frame_path"] or ""),
                "has_zoom": bool(cls.get("zoom_frame_path")),
                "face_id": int(r["face_id"]) if r["face_id"] is not None else None,
                "face_name": str(r["face_name"]) if r["face_name"] else "",
                "area_ratio": float(cls.get("area_ratio") or 0.0),
                "description": str(cls.get("description") or ""),
                "cluster_id": str(r["cluster_id"] or ""),
            })
        return result

    def recent_known_identity_names(
        self, source_id: str, *, since: datetime
    ) -> list[str]:
        """Namen der auf dieser Quelle seit ``since`` sicher erkannten
        Personen (``face_known``-Events, dedupliziert, jüngste zuerst).
        Für den Identitäts-Kontext in VLM-Prompts (Teleprompter/Alert)."""
        query = (
            "SELECT DISTINCT f.name FROM events e "
            "JOIN faces f ON f.id = e.face_id "
            "WHERE e.source_id = ? AND e.event_type = 'face_known' "
            "AND e.timestamp >= ? ORDER BY e.timestamp DESC"
        )
        with self._conn() as conn:
            rows = conn.execute(
                query,
                (source_id, since.isoformat(timespec="microseconds")),
            ).fetchall()
        return [str(r["name"]) for r in rows if r["name"]]

    def list_cluster_event_ids(self, cluster_id: str) -> list[int]:
        """Alle Event-IDs eines Vorkommnisses (Cluster), neueste zuerst —
        für die Film-Slideshow im Casus (Serie eines Vorbeigangs).

        Pro Bilddatei EINE ID: der Initial-Trigger schreibt Gesichts- und
        Personen-Event mit demselben Frame — ohne Dedupe zeigte der Film
        dasselbe Bild doppelt."""
        if not cluster_id:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, frame_path FROM events WHERE cluster_id = ? "
                "AND frame_path != '' ORDER BY timestamp DESC, id ASC",
                (cluster_id,),
            ).fetchall()
        seen: set[str] = set()
        out: list[int] = []
        for r in rows:
            fp = str(r["frame_path"])
            if fp in seen:
                continue
            seen.add(fp)
            out.append(int(r["id"]))
        return out

    def latest_event_id(self) -> int:
        """Höchste Event-ID (0 wenn leer) — billiger Änderungs-Marker für
        UI-Polling (Casus-Auto-Refresh): eine neue ID = neue Events da."""
        with self._conn() as conn:
            row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()
        return int(row[0])

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

    @staticmethod
    def _unlink_frame_files(rows: list[sqlite3.Row]) -> int:
        """Löscht die zu Events gehörenden Bilddateien von der Platte:
        ``frame_path`` (Vollbild) plus ``zoom_frame_path`` aus der
        ``classification``. Nur Dateien UNTERHALB von ``DATA_DIR`` werden
        angefasst (Schutz gegen Pfad-Ausbruch); fehlende Dateien werden
        still übersprungen. Returnt die Anzahl gelöschter Dateien.

        Wird von den Event-Löschpfaden aufgerufen, damit ein Casus-Delete
        die Frames mitnimmt statt sie als verwaiste 3-GB-Reste liegen zu
        lassen (DB-Zeile weg, Datei bleibt = sinnlos)."""
        root = DATA_DIR.resolve()
        paths: set[str] = set()
        for r in rows:
            keys = r.keys()
            fp = (r["frame_path"] if "frame_path" in keys else "") or ""
            if fp:
                paths.add(str(fp))
            cls = (r["classification"] if "classification" in keys else "") or ""
            if cls:
                try:
                    zfp = json.loads(cls).get("zoom_frame_path")
                    if zfp:
                        paths.add(str(zfp))
                except (json.JSONDecodeError, AttributeError, TypeError):
                    pass
        deleted = 0
        for p in paths:
            try:
                fpath = Path(p).resolve()
                fpath.relative_to(root)  # ValueError, wenn außerhalb DATA_DIR
            except (ValueError, OSError):
                continue
            try:
                fpath.unlink()
                deleted += 1
            except FileNotFoundError:
                pass
            except OSError:
                pass
        return deleted

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
        ``list_events_with_summary`` / ``count_events``. Löscht die
        zugehörigen Bilddateien (frame_path + zoom_frame_path) gleich mit.
        Returnt die Anzahl gelöschter Zeilen."""
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
            # Pfade VOR dem DELETE einsammeln, danach die Dateien löschen.
            rows = conn.execute(
                f"SELECT frame_path, classification FROM events{where}",
                tuple(params),
            ).fetchall()
            cur = conn.execute(f"DELETE FROM events{where}", tuple(params))
            count = int(cur.rowcount)
        self._unlink_frame_files(rows)
        return count

    def delete_event(self, event_id: int) -> bool:
        """Einzelnes Event löschen — inkl. der zugehörigen Bilddateien
        (frame_path + zoom_frame_path). Embeddings, die dieses Event als
        ``source_event_id`` referenzieren, bekommen NULL (ON DELETE SET
        NULL aus dem Schema)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT frame_path, classification FROM events WHERE id = ?",
                (event_id,),
            ).fetchall()
            cur = conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
            ok = cur.rowcount > 0
        if ok:
            self._unlink_frame_files(rows)
        return ok

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

    def list_untagged_face_events(
        self,
        cluster_id: str | None = None,
        *,
        exclude_id: int | None = None,
        include_dismissed: bool = True,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        """face_unknown/face_unsure-Events ohne face_id (mit geparster
        classification/metadata wie ``get_event``) — mit ``cluster_id``
        auf EIN Vorkommnis begrenzt (Enroll-Cluster-Sweep), ohne über
        den gesamten Bestand. ``include_dismissed=False`` blendet per ✕
        verworfene Aufnahmen aus (manueller Re-Match: dessen Bilanz muss
        zum sichtbaren Grid passen)."""
        query = (
            "SELECT * FROM events WHERE face_id IS NULL "
            "AND event_type IN ('face_unknown', 'face_unsure')"
        )
        params: list[Any] = []
        if cluster_id:
            query += " AND cluster_id = ?"
            params.append(cluster_id)
        if not include_dismissed:
            query += (
                " AND COALESCE(json_extract(classification, "
                "'$.untagged_dismissed'), 0) = 0"
            )
        if exclude_id is not None:
            query += " AND id != ?"
            params.append(int(exclude_id))
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(int(limit))
        with self._conn() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["classification"] = json.loads(d.get("classification") or "{}")
            d["metadata"] = json.loads(d.get("metadata") or "{}")
            out.append(d)
        return out

    def dismiss_untagged_event(self, event_id: int) -> bool:
        """GENAU DIESE Aufnahme als „nicht mehr vorschlagen" markieren
        (``classification.untagged_dismissed``) — das Personarium blendet
        sie dauerhaft aus, das Event selbst bleibt für Casus/Chronik
        unangetastet. Bewusst nur das einzelne Event: Im Grid ist jede
        Aufnahme eine eigene Karte, der Nutzer entscheidet pro Bild."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE events SET classification = "
                "json_set(COALESCE(classification, '{}'), "
                "'$.untagged_dismissed', 1) WHERE id = ?",
                (int(event_id),),
            )
            return cur.rowcount > 0

    def get_event_frame_path(self, event_id: int, *, zoom: bool = False) -> str:
        """Frame-Pfad eines Events per ID — das Weitwinkel-Vollbild oder mit
        ``zoom=True`` der Tele-Snap (``classification.zoom_frame_path``).

        Leerer String, wenn das Event nicht existiert oder der jeweilige
        Pfad fehlt. Für den Frame-Serving-Endpoint (Casus-Thumbnail
        + Bild-Modal)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT frame_path, classification FROM events WHERE id = ?",
                (int(event_id),),
            ).fetchone()
        if row is None:
            return ""
        if zoom:
            try:
                cls = json.loads(row["classification"] or "{}")
            except (TypeError, ValueError):
                return ""
            return str(cls.get("zoom_frame_path") or "")
        return str(row["frame_path"]) if row["frame_path"] else ""

    def prune_events(self, older_than: datetime) -> int:
        """Lösche Events älter als ``older_than``. Gibt Anzahl gelöschter
        Zeilen zurück. Für Retention-Cronjob (Schicht 6) gedacht."""
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM events WHERE timestamp < ?",
                (older_than.isoformat(timespec="microseconds"),),
            )
            return int(cur.rowcount)

