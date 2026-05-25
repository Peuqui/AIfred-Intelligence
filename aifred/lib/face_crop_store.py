"""Session-basierte Face-Crop-Speicherung.

Eine **Sichtung** (Session) ist eine kontinuierliche Anwesenheit
derselben Person + desselben Confidence-Bands vor der Kamera.
Solange in einer Session weitere Events derselben Identity kommen,
wird die existierende Datei überschrieben — am Ende hat man pro
Sichtung **genau eine** JPEG-Datei mit dem aktuellsten Crop.

Sobald ``SESSION_TIMEOUT_SEC`` Sekunden ohne Sichtung vergangen
sind, bricht die Session ab; das nächste Event derselben Person
startet eine **neue** Session und damit eine neue Datei mit neuem
Zeitstempel. Auch ein Wechsel des Confidence-Bands (z.B. known →
unsure) bricht die Session, weil unsichere und sichere Sichtungen
forensisch unterscheidbar bleiben sollen.

**Identity-Schlüssel:**

* ``face_known`` / ``face_unsure``: ``face_id`` aus dem
  ``vision_store`` ist stabil → eindeutiger Key.
* ``face_unknown``: kein face_id. Cluster-Matching über das
  InsightFace-Embedding (cosine sim > ``UNKNOWN_SIM``); ähnliches
  Embedding in einer aktiven Session → gleiche Session. Sonst
  → neue Session.

**Filename-Konvention:**

``data/vision/faces/<source-slug>/<yyyy-mm-dd>/<HH-MM-SS-mmm>_<tag>.jpg``

* ``<tag>`` = name-slug bei known (z.B. ``pequi``),
  ``<name>_unsure`` bei unsure, leer bei unknown
* Millisekunden im Namen vermeiden Kollisionen bei zwei
  gleichzeitigen Detections.

Cleanup: ``vision_cleanup_task`` löscht Tag-Ordner > N Tage TTL.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Eine Session bricht ab, sobald N Sekunden seit der letzten
# Sichtung dieser Identity vergangen sind. 10 s ist ein guter
# Kompromiss: kurze Lücken (Person dreht sich weg, Cam-Frame
# unscharf) zählen noch zur selben Sichtung; richtige Abwesenheit
# (Person geht raus) triggert eine neue Session.
SESSION_TIMEOUT_SEC = 10.0

# Cluster-Schwelle für unknowns — hoch genug, dass nur sehr
# ähnliche Embeddings als „selbe Person" gelten.
UNKNOWN_SIM = 0.85

# JPEG-Qualität für die Crops; ~5 KB pro Bild bei typischen
# 100–200 px Kantenlänge nach Bbox + Padding.
JPEG_QUALITY = 75

# Padding um die Bounding-Box relativ zur Box-Größe — sonst
# schneidet's am Augenrand ab.
BBOX_PADDING = 0.25


@dataclass
class _Session:
    """Eine aktive Sichtung (kontinuierliche Anwesenheit derselben
    Identity)."""
    identity_key: str          # ``face_<id>`` oder ``unknown_<n>``
    band: str                  # ``known`` / ``unsure`` / ``unknown``
    source_id: str
    started_at: datetime
    last_seen: datetime
    embedding: np.ndarray
    relative_path: str         # tagestäglicher Unterpfad inkl. Filename


@dataclass
class CropResult:
    """Was ``save()`` zurückgibt — URL fürs Frontend + Disk-Pfad."""
    url: str
    abs_path: Path
    identity_key: str
    band: str
    started_at: datetime

    @property
    def session_id(self) -> str:
        """Stabile Session-Kennung: ``identity_key + started_at``.
        Frontend dedupt anhand dieser ID — solange die Session läuft,
        wird dieselbe UI-Zeile aktualisiert; eine neue Sichtung
        derselben Person nach Pause bekommt eine neue ID."""
        return f"{self.identity_key}@{self.started_at.isoformat(timespec='seconds')}"


class FaceCropStore:
    """Singleton pro Prozess. Tests können eigene Instanzen mit
    Test-Verzeichnis bauen."""

    URL_PREFIX = "/_upload/face_crops"

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._sessions: list[_Session] = []
        self._unknown_counter = 0   # Zähler für unknown-Cluster-IDs
        self._lock = Lock()

    # ── Public API ───────────────────────────────────────────────

    def save(
        self,
        *,
        frame_bytes: bytes,
        bbox: tuple[int, int, int, int],
        source_id: str,
        event_type: str,
        face_id: int | None = None,
        name: str = "",
        embedding: np.ndarray | None = None,
    ) -> CropResult | None:
        """Crop aus dem Frame extrahieren + auf Disk schreiben.

        Wenn der Event in eine bereits aktive Session passt (gleiche
        Identity + gleiches Band, ``last_seen < SESSION_TIMEOUT_SEC``
        zurück), wird die bestehende Datei überschrieben — sonst
        startet eine neue Session mit eigenem Filename.
        """
        try:
            crop_bytes = _crop_from_jpeg(frame_bytes, bbox)
        except Exception as e:  # noqa: BLE001
            logger.warning("face crop decode failed for %s: %s", source_id, e)
            return None
        if not crop_bytes:
            return None

        # ``event_type`` ist ``face_known`` / ``face_unsure`` / ``face_unknown``
        band = event_type.replace("face_", "") or "unknown"
        if embedding is None:
            return None
        now = datetime.now()
        session = self._match_or_open_session(
            band=band,
            face_id=face_id,
            name=name,
            embedding=embedding,
            source_id=source_id,
            now=now,
        )
        # Filename ist relativ zur base_dir; absoluter Pfad wird hier
        # zusammengesetzt + Verzeichnis bei Bedarf angelegt.
        abs_path = self._base_dir / session.relative_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            abs_path.write_bytes(crop_bytes)
        except OSError as e:
            logger.warning("face crop write failed for %s: %s", abs_path, e)
            return None
        url = f"{self.URL_PREFIX}/{session.relative_path}"
        return CropResult(
            url=url,
            abs_path=abs_path,
            identity_key=session.identity_key,
            band=session.band,
            started_at=session.started_at,
        )

    # ── Session-Lookup / -Eröffnung ──────────────────────────────

    def _match_or_open_session(
        self,
        *,
        band: str,
        face_id: int | None,
        name: str,
        embedding: np.ndarray,
        source_id: str,
        now: datetime,
    ) -> _Session:
        """Sucht eine aktive Session für die Identity. Wenn gefunden:
        ``last_seen`` aktualisieren, Session zurückgeben (Caller
        überschreibt die existierende Datei). Sonst: neue Session
        anlegen, Filename mit aktuellem Zeitstempel."""
        with self._lock:
            self._evict_stale(now)
            identity_key = self._identity_key(
                band, face_id, embedding, source_id
            )
            # Lebenden Session-Match suchen — gleiche Identity UND
            # gleiches Band UND gleiche Source.
            for s in self._sessions:
                if (
                    s.identity_key == identity_key
                    and s.band == band
                    and s.source_id == source_id
                ):
                    s.last_seen = now
                    # Embedding der Session leicht aktualisieren —
                    # frischestes Bild wird oft beste Repräsentation.
                    s.embedding = embedding
                    return s

            # Neue Session
            slug = _slugify_source(source_id)
            day = now.strftime("%Y-%m-%d")
            ts = now.strftime("%H-%M-%S-") + f"{now.microsecond // 1000:03d}"
            tag = _name_to_tag(name, band)
            filename = f"{ts}_{tag}.jpg" if tag else f"{ts}.jpg"
            rel_path = f"{slug}/{day}/{filename}"
            session = _Session(
                identity_key=identity_key,
                band=band,
                source_id=source_id,
                started_at=now,
                last_seen=now,
                embedding=embedding,
                relative_path=rel_path,
            )
            self._sessions.append(session)
            return session

    def _identity_key(
        self,
        band: str,
        face_id: int | None,
        embedding: np.ndarray,
        source_id: str,
    ) -> str:
        """Eindeutiger Schlüssel pro Identity. Bei known/unsure aus
        ``face_id``, bei unknown via Embedding-Cluster (Match gegen
        bestehende unknown-Sessions, ggf. neuer Counter).
        """
        if band in ("known", "unsure") and face_id and face_id > 0:
            return f"face_{face_id}"
        # unknown — Cluster-Match gegen offene unknown-Sessions auf
        # derselben Source.
        emb_norm = _l2(embedding)
        best_key = ""
        best_sim = 0.0
        for s in self._sessions:
            if s.band != "unknown" or s.source_id != source_id:
                continue
            sim = float(np.dot(emb_norm, _l2(s.embedding)))
            if sim > best_sim:
                best_sim = sim
                best_key = s.identity_key
        if best_sim >= UNKNOWN_SIM and best_key:
            return best_key
        # Neue unknown-Identity
        self._unknown_counter += 1
        return f"unknown_{self._unknown_counter}"

    def _evict_stale(self, now: datetime) -> None:
        """Sessions, deren letzter Frame > SESSION_TIMEOUT_SEC her ist,
        gelten als beendet und werden aus der Liste entfernt — der
        nächste Event derselben Person eröffnet damit eine neue
        Session mit frischem Zeitstempel."""
        cutoff = now - timedelta(seconds=SESSION_TIMEOUT_SEC)
        self._sessions = [s for s in self._sessions if s.last_seen >= cutoff]


# ── Internals ───────────────────────────────────────────────────


_NAME_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _name_to_tag(name: str, band: str) -> str:
    """Erzeugt einen filename-tauglichen Tag aus dem Personennamen.
    Bei unsure wird ``_unsure`` angehängt. Bei unknown leer."""
    if band == "unknown":
        return ""
    clean = _NAME_SLUG_RE.sub("_", name.lower()).strip("_")
    if not clean:
        return "unsure" if band == "unsure" else "known"
    if band == "unsure":
        return f"{clean}_unsure"
    return clean


def _slugify_source(source_id: str) -> str:
    return source_id.replace("/", "_").replace("\\", "_")


def _l2(v: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(v))
    if norm < 1e-6:
        return v
    return v / norm


def _crop_from_jpeg(jpeg_bytes: bytes, bbox: tuple[int, int, int, int]) -> bytes:
    """Decode JPEG → bbox crop (mit Padding) → re-encode JPEG.
    Liefert ``b""`` bei ungültiger Box / Decode-Fehler."""
    arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        return b""
    h, w = frame.shape[:2]
    x, y, bw, bh = bbox
    pad_x = int(bw * BBOX_PADDING)
    pad_y = int(bh * BBOX_PADDING)
    x0 = max(0, x - pad_x)
    y0 = max(0, y - pad_y)
    x1 = min(w, x + bw + pad_x)
    y1 = min(h, y + bh + pad_y)
    if x1 <= x0 or y1 <= y0:
        return b""
    crop = frame[y0:y1, x0:x1]
    ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        return b""
    return bytes(buf)


# ── Singleton ───────────────────────────────────────────────────


_default_store: FaceCropStore | None = None
_default_lock = Lock()


def get_default_store() -> FaceCropStore:
    """Singleton-Store unter ``DATA_DIR/vision/faces/``."""
    global _default_store
    if _default_store is not None:
        return _default_store
    from .config import DATA_DIR
    with _default_lock:
        if _default_store is None:
            _default_store = FaceCropStore(DATA_DIR / "vision" / "faces")
        return _default_store
