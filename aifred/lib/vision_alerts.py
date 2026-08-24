"""Vision → proactive alerts (the first producer for the alert pipeline).

When the watcher recognises a face while Vigilantia is armed, this emits a
neutral AlertEvent to the shared dispatcher (see alert_bus). The dispatcher's
central rules decide whether/where it actually goes. The dedup_key is the
event's cluster_id, so repeated frames of one happening collapse to one alert.

Kept out of the watcher core: the watcher just calls emit_face_alert().
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_VISION_SETTINGS_PATH = (
    Path(__file__).parent.parent / "plugins" / "tools" / "vision" / "settings.json"
)

# Only these carry a "someone is here" meaning worth alerting on.
_ALERT_EVENT_TYPES = {"face_known", "face_unsure", "face_unknown"}


def _vigilantia_armed() -> bool:
    """Read the master arm flag (SSoT in the vision plugin settings)."""
    try:
        cfg = json.loads(_VISION_SETTINGS_PATH.read_text(encoding="utf-8"))
        return bool(cfg.get("vigilantia_armed", False))
    except (OSError, json.JSONDecodeError):
        return False


def _alerts_enabled(source_id: str, store: Any) -> bool:
    """Pro-Kamera Alert-Opt-out (SSoT: ``sources.settings.alerts_enabled``).

    Default an. Aus = die Kamera erkennt/speichert/chronisiert weiter, schickt
    aber KEINE proaktiven Push-Alerts — verhindert Dauer-Spam von Kameras, die
    permanent Bewegung sehen (z.B. der eigene Schreibtisch)."""
    try:
        rec = store.get_source(source_id) if store else None
        if rec:
            return bool((rec.get("settings") or {}).get("alerts_enabled", True))
    except Exception:  # noqa: BLE001
        pass
    return True


def _alert_passes_filters(source_id: str, store: Any, alert_type: str) -> bool:
    """Pro-Kamera Feinfilter: welche Event-Typen alerten + Ruhezeiten.

    * ``settings.alert_types`` (Liste): nur enthaltene Typen alerten. Fehlt der
      Schlüssel → alle Typen erlaubt (Default).
    * ``settings.quiet_enabled`` + ``quiet_start``/``quiet_end`` (Stunde 0–23):
      in diesem Zeitfenster keine Alerts. Über Mitternacht (z.B. 22→6) wird
      korrekt behandelt.
    ``alert_type`` ist einer von person/vehicle/animal/face."""
    try:
        rec = store.get_source(source_id) if store else None
    except Exception:  # noqa: BLE001
        rec = None
    settings = (rec.get("settings") or {}) if rec else {}

    types = settings.get("alert_types")
    if isinstance(types, list) and alert_type not in types:
        return False

    if settings.get("quiet_enabled"):
        try:
            start = int(settings.get("quiet_start", 22)) % 24
            end = int(settings.get("quiet_end", 6)) % 24
        except (TypeError, ValueError):
            return True
        hour = datetime.now().hour
        in_quiet = (start <= hour < end) if start < end else (hour >= start or hour < end)
        if in_quiet:
            return False
    return True


def _source_alias(source_id: str, store: Any) -> str:
    """Anzeigename der Kamera für den Alert. Geht über die SSoT
    :meth:`VisionStore.source_label` (Alias > display_name > source_id) —
    derselbe Name wie in den Event-Panels und im Zonen-Editor."""
    try:
        from .vision_store import VisionStore
        rec = store.get_source(source_id) if store else None
        if rec:
            return VisionStore.source_label(rec)
    except Exception:  # noqa: BLE001
        pass
    return source_id


def _session_routing(source_id: str, store: Any) -> tuple[str, str]:
    """Browser-Session-Routing für die Alerts dieser Quelle.

    ``session_group`` in den Quellen-Settings bündelt mehrere Streams
    desselben physischen Geräts (Weitwinkel + Zoom einer Dual-Lens-
    Kamera) in EINE Session: der Wert ist die source_id der Hauptquelle.
    Ohne Gruppe behält jede Quelle ihre eigene Session. Der Titel kommt
    fest aus dem Alias der routenden Quelle ("Vigilantia: Hauseingang")
    — autonome Turns bekommen keinen LLM-Titel, ohne festen Titel
    erschienen die Alert-Sessions als namenlose Chats."""
    key = source_id
    try:
        rec = store.get_source(source_id) if store else None
        group = ((rec or {}).get("settings") or {}).get("session_group") or ""
        if group:
            key = group
    except Exception:  # noqa: BLE001
        pass
    return key, f"Vigilantia: {_source_alias(key, store)}"


def _range_suffix(similarities: list[float]) -> str:
    """Konfidenz-Spanne für NAMENLOSE Titel: "(62 %)" bzw. "(55–60 %)"."""
    if not similarities:
        return ""
    pcts = sorted(round(s * 100) for s in similarities)
    pct_str = f"{pcts[0]} %" if pcts[0] == pcts[-1] else f"{pcts[0]}–{pcts[-1]} %"
    return f" ({pct_str})"


def _others_suffix(names: list[str], person_count: int) -> str:
    """Zusatz für benannte Titel, wenn die Objekterkennung MEHR Personen
    gezählt hat als Gesichter erkannt wurden: „+ 1 weitere Person". Ohne
    das verschwiegen die Titel jeden Begleiter, dessen Gesicht nie
    gematcht wurde (Hand vorm Gesicht, abgewandt, verdeckt)."""
    others = max(0, int(person_count) - len(names))
    if not names or others <= 0:
        return ""
    return (f" + {others} weitere Person" if others == 1
            else f" + {others} weitere Personen")


def _compose(
    event_type: str, alias: str, names: list[str], count: int, ts: datetime,
    similarities: list[float] | None = None, person_count: int = 0,
) -> tuple[str, str]:
    """User-facing alert text (German — goes to the user's phone).

    ``names``: alle erkannten Namen des Bands (ungedeckelt, ein Vorkommnis
    kann eine ganze Menschenmenge nennen). ``count``: Anzahl Gesichter des
    Bands — bei Unbekannten/Unsicheren ohne Namen die einzige Information.
    ``similarities``: korrespondiert bei benannten Titeln PRO NAME (gleiche
    Reihenfolge → "Peuqui (91 %), Anna (87 %)"); bei namenlosen Titeln sind
    es alle Band-Werte → Spanne. face_unknown bekommt KEINE Zahl — dort
    wäre die Best-Match-Similarity zum nächsten bekannten Gesicht
    irreführend (der Titel sagt bereits "Unbekannte")."""
    when = ts.strftime("%H:%M")
    sims = similarities or []
    if names and len(sims) == len(names):
        names_str = ", ".join(
            f"{n} ({round(s * 100)} %)" for n, s in zip(names, sims)
        )
        unnamed_suffix = ""
    else:
        names_str = ", ".join(names)
        unnamed_suffix = _range_suffix(sims)
    if event_type == "face_known":
        if names_str:
            title = f"👤 {names_str} erkannt{_others_suffix(names, person_count)}"
        else:
            title = (f"👤 {count} bekannte Personen erkannt" if count > 1
                     else "👤 Bekannte Person erkannt") + unnamed_suffix
    elif event_type == "face_unsure":
        if names_str:
            title = (f"👤 Mögliche Person(en): {names_str}"
                     f"{_others_suffix(names, person_count)}")
        else:
            title = (f"👤 {count} unsichere Erkennungen" if count > 1
                     else "👤 Unsichere Erkennung") + unnamed_suffix
    else:  # face_unknown
        title = (f"🚨 {count} unbekannte Personen erkannt" if count > 1
                 else "🚨 Unbekannte Person erkannt")
    return title, f"{alias} · {when}"


async def _emit(
    *,
    source_id: str,
    category: str,
    severity: str,
    title: str,
    body: str,
    dedup_key: str,
    frame_path: str,
    zoom_frame_path: str = "",
    crop_url: str = "",
    extra_crop_urls: list[str] | None = None,
    timestamp: datetime,
    metadata: dict[str, Any] | None = None,
    store: Any = None,
) -> None:
    """Build + dispatch one AlertEvent. Best-effort — never raises into the
    watcher's hot path. The shared dispatcher's rules decide where it goes.

    ``media`` (VLM description + single-image channels) is the ZOOM still when
    available — it shows the subject, while the wide-angle latest frame often
    misses it. ``media_gallery`` carries all three views (wide + zoom + crop)
    as URLs for the browser session.
    """
    try:
        from pathlib import Path as _Path

        from .alert_bus import AlertEvent, get_default_dispatcher
        from .vision_utils import get_image_url

        primary = zoom_frame_path or frame_path
        # Wide-Kontext fürs VLM nur, wenn die Subjekt-Ansicht der Zoom ist —
        # sonst IST primary bereits das Weitwinkel (kein separater Kontext).
        context = (
            frame_path if (zoom_frame_path and frame_path and frame_path != primary)
            else None
        )
        gallery: list[str] = []
        for p in (frame_path, zoom_frame_path):
            if p:
                u = get_image_url(_Path(p))
                if u and u not in gallery:
                    gallery.append(u)
        # Kopfausschnitte: der des besten Gesichts zuerst, dann die der
        # übrigen im Vorkommnis gesehenen Personen — bei mehreren Leuten
        # zeigt die Galerie so JEDE, nicht nur die am besten erkannte.
        for u in [crop_url, *(extra_crop_urls or [])]:
            if u and u not in gallery:
                gallery.append(u)

        session_key, session_title = _session_routing(source_id, store)
        ev = AlertEvent(
            producer="vision",
            category=category,
            source_id=source_id,
            severity=severity,
            title=title,
            body=body,
            dedup_key=dedup_key,
            media=primary or None,
            media_context=context,
            media_gallery=gallery,
            timestamp=timestamp,
            metadata=metadata or {},
            session_key=session_key,
            session_title=session_title,
        )
        await get_default_dispatcher().emit(ev)
    except Exception as e:  # noqa: BLE001
        logger.warning("vision alert emit failed for %s: %s", source_id, e)


async def emit_face_alert(
    *,
    source_id: str,
    event_type: str,
    frame_path: str,
    zoom_frame_path: str = "",
    crop_url: str = "",
    cluster_id: str,
    names: list[str] | None = None,
    count: int = 1,
    similarities: list[float] | None = None,
    timestamp: datetime | None = None,
    store: Any = None,
    dedup_key: str | None = None,
    person_count: int = 0,
    extra_crop_urls: list[str] | None = None,
    previous_description: str = "",
) -> None:
    """Emit an aggregated face-band detection as a proactive AlertEvent —
    one per band per happening, only while armed. ``names`` = alle erkannten
    Namen des Bands (ungedeckelt), ``count`` = Anzahl Gesichter des Bands,
    ``similarities`` = deren Match-Werte (Konfidenz-Klammer im Titel).

    ``person_count``: von der Objekterkennung gezählte Personen (Titel-Zusatz
    „+ N weitere" und Fakt fürs VLM). ``extra_crop_urls``: Kopfausschnitte
    ALLER im Vorkommnis gesehenen Personen für die Galerie.
    ``previous_description``: vorherige Bilanz desselben laufenden
    Vorkommnisses, an die das VLM anknüpft."""
    if event_type not in _ALERT_EVENT_TYPES:
        return
    if not _vigilantia_armed():
        return
    if not _alerts_enabled(source_id, store):
        return
    if not _alert_passes_filters(source_id, store, "face"):
        return
    names = names or []
    ts = timestamp or datetime.now()
    alias = _source_alias(source_id, store)
    title, body = _compose(
        event_type, alias, names, count, ts, similarities, person_count,
    )
    severity = "warning" if event_type in ("face_unknown", "face_unsure") else "info"
    # Personalisierung: NUR beim sicheren Match (face_known) ALLE Namen
    # an die VLM-Beschreibung durchreichen — dann nennt das VLM jede
    # erkannte Person beim Namen statt "ein Mann mit Brille". Bei unsure/
    # unknown bewusst NICHT: ein eingeflüsterter Name würde das VLM zu
    # einer Falschbehauptung verleiten (Suggestiv-Falle).
    meta: dict[str, Any] = {}
    if event_type == "face_known" and names:
        meta["identity_names"] = names
    # Der Cluster als eigenes Feld: der Alert-Pfad beschreibt darüber die
    # GANZE Bildserie und persistiert die Beschreibung zurück. NICHT über
    # den dedup_key ableitbar — Burst-Bilanzen hängen dort einen Laufindex an.
    if cluster_id:
        meta["cluster_id"] = cluster_id
    if person_count > 0:
        meta["person_count"] = person_count
    if previous_description.strip():
        meta["previous_description"] = previous_description.strip()
    await _emit(
        source_id=source_id,
        category=event_type,
        severity=severity,
        title=title,
        body=body,
        # One alert per happening; fall back to source+type if unclustered.
        # Der Burst-Report übergibt einen eigenen Key (mehrere gewollte
        # Meldungen pro Vorkommnis: Bilanz + Follow-ups).
        dedup_key=dedup_key or cluster_id or f"{source_id}:{event_type}",
        frame_path=frame_path,
        zoom_frame_path=zoom_frame_path,
        crop_url=crop_url,
        extra_crop_urls=extra_crop_urls,
        timestamp=ts,
        metadata=meta or None,
        store=store,
    )


async def emit_person_alert(
    *,
    source_id: str,
    frame_path: str,
    zoom_frame_path: str = "",
    cluster_id: str,
    count: int = 1,
    timestamp: datetime | None = None,
    store: Any = None,
    dedup_key: str | None = None,
    previous_description: str = "",
) -> None:
    """Emit a YOLO person detection (whole body) as a proactive AlertEvent —
    but only while armed. Coarser than faces: "a person is present", even
    with no recognisable face.

    ``previous_description``: vorherige Bilanz desselben laufenden
    Vorkommnisses, an die das VLM anknüpft."""
    if not _vigilantia_armed():
        return
    if not _alerts_enabled(source_id, store):
        return
    if not _alert_passes_filters(source_id, store, "person"):
        return
    ts = timestamp or datetime.now()
    alias = _source_alias(source_id, store)
    when = ts.strftime("%H:%M")
    title = "🚶 Person erkannt" if count == 1 else f"🚶 {count} Personen erkannt"
    meta: dict[str, Any] = {}
    if cluster_id:
        meta["cluster_id"] = cluster_id
    if count > 0:
        meta["person_count"] = count
    if previous_description.strip():
        meta["previous_description"] = previous_description.strip()
    await _emit(
        source_id=source_id,
        category="person",
        severity="warning",
        title=title,
        body=f"{alias} · {when}",
        # One alert per happening; fall back to source if unclustered.
        dedup_key=dedup_key or cluster_id or f"{source_id}:person",
        frame_path=frame_path,
        zoom_frame_path=zoom_frame_path,
        timestamp=ts,
        metadata=meta or None,
        store=store,
    )


# Emoji + Singular/Plural je Objektklasse (SSoT für die Edge-AI-Objekt-
# Alerts). ``count`` > 0 (YOLO-bestätigt) → mit Zahl/Plural; count == 0
# (vertraute Klasse wie animal, Kamera liefert keine Stückzahl) → Singular.
_OBJECT_ALERT_WORDS = {
    "vehicle": ("🚗", "Fahrzeug", "Fahrzeuge"),
    "animal": ("🐾", "Tier", "Tiere"),
}


async def emit_object_alert(
    *,
    source_id: str,
    object_type: str,
    frame_path: str,
    zoom_frame_path: str = "",
    cluster_id: str,
    count: int = 0,
    timestamp: datetime | None = None,
    store: Any = None,
) -> None:
    """Emit an edge-AI object detection (vehicle/animal) as a proactive
    AlertEvent — but only while armed. ``count`` > 0 = YOLO-bestätigte
    Stückzahl (Plural/Zahl im Titel); 0 = der Kamera vertraut, keine Zahl."""
    if not _vigilantia_armed():
        return
    if not _alerts_enabled(source_id, store):
        return
    if not _alert_passes_filters(source_id, store, object_type):
        return
    words = _OBJECT_ALERT_WORDS.get(object_type)
    if words is None:
        return
    emoji, singular, plural = words
    if count > 1:
        title = f"{emoji} {count} {plural} erkannt"
    else:
        title = f"{emoji} {singular} erkannt"
    ts = timestamp or datetime.now()
    alias = _source_alias(source_id, store)
    when = ts.strftime("%H:%M")
    await _emit(
        source_id=source_id,
        category=object_type,
        severity="info",
        title=title,
        body=f"{alias} · {when}",
        dedup_key=cluster_id or f"{source_id}:{object_type}",
        frame_path=frame_path,
        zoom_frame_path=zoom_frame_path,
        timestamp=ts,
        metadata={"cluster_id": cluster_id} if cluster_id else None,
        store=store,
    )


# ── Burst-Bilanz ─────────────────────────────────────────────────────
# Statt Sofort-Alert beim Kamera-Trigger sammelt der Face-Hunt-Burst
# seine Ticks in einem BurstReport und meldet gebündelt: EINE Bilanz
# beim Burst-Ende oder spätestens nach der Deadline (bestes Bild des
# Vorbeigangs, alle erkannten Namen), danach Follow-up-Meldungen nur
# bei echten Neuigkeiten (neuer Name, Band-Upgrade, mehr Personen).

_BAND_RANK = {"person": 0, "face_unknown": 1, "face_unsure": 2, "face_known": 3}


class BurstReport:
    """Sammelt die Beobachtungen eines Vorbeigangs (Trigger + Burst-Ticks)
    und verschickt daraus Telegram-Bilanzen. Lebt für die Dauer EINES
    Bursts (vision_watcher hält ihn pro Quelle)."""

    def __init__(self, source_id: str, cluster_id: str, store: Any) -> None:
        self.source_id = source_id
        self.cluster_id = cluster_id
        self._store = store
        self.started = datetime.now()
        # Bester Gesichts-Tick: (band_rank, score) entscheidet; Namen mit
        # bester Similarity werden über den ganzen Burst gesammelt.
        self.known_named: dict[str, float] = {}
        self._best_face: tuple[int, float] = (-1, -1.0)
        self._best_face_paths: tuple[str, str, str] = ("", "", "")  # frame, zoom, crop
        self._best_face_band = ""
        self._face_count_max = 0
        self._face_sims: list[float] = []
        # Bester Kopfausschnitt PRO PERSON: identity_key → (score, crop_url).
        # So zeigt die Galerie jede gesehene Person, nicht nur die am besten
        # erkannte — auch die, deren Gesicht nie einem Namen zugeordnet wurde.
        self._crops_by_identity: dict[str, tuple[float, str]] = {}
        # Bester Personen-Tick (kein Gesicht): höchster YOLO-Score.
        self._best_person: float = -1.0
        self._best_person_paths: tuple[str, str] = ("", "")
        self._person_count_max = 0
        # Versand-Zustand für due()/has_news().
        self._sent_messages = 0
        self._sent_band_rank = -1
        self._sent_names: set[str] = set()
        self._sent_person_count = 0
        self._last_sent_at: datetime | None = None
        # Läuft gerade ein Versand? Eine Bilanz umfasst die VLM-Beschreibung
        # der ganzen Serie und dauert damit länger als das Bilanz-Intervall
        # (gemessen 17,4 s bei 8 Keyframes auf dem 4B-Describer, Deadline
        # 15 s). Ohne diese Sperre liefen Bilanzen übereinander.
        self._sending = False
        # Wurde während eines Versands die nächste Bilanz fällig? Dann wird
        # sie NACHGEHOLT, nicht verworfen. Ein einzelnes Nachhol-Flag statt
        # einer Warteschlange: jede Bilanz beschreibt ohnehin den kompletten
        # Cluster bis zu ihrem Versandbeginn — aufgestaute Meldungen wären
        # inhaltlich dieselbe, nur älter.
        self._catch_up = False
        # Zuletzt gemeldete VLM-Beschreibung — die Folge-Bilanz reicht sie
        # als Historie weiter, damit das VLM an den bisherigen Ablauf
        # anknüpft statt jedes Mal bei null anzufangen.
        self._last_description = ""

    # ── Beobachtungen ────────────────────────────────────────────────

    def observe_face(
        self, band: str, name: str, similarity: float, det_score: float,
        frame_path: str, zoom_frame_path: str, crop_url: str,
        faces_in_tick: int = 1, identity_key: str = "",
    ) -> None:
        rank = _BAND_RANK.get(band, 1)
        if band == "face_known" and name:
            prev = self.known_named.get(name, 0.0)
            self.known_named[name] = max(prev, float(similarity))
        self._face_sims.append(float(similarity))
        self._face_count_max = max(self._face_count_max, int(faces_in_tick))
        # Bester Ausschnitt je Person — Schlüssel ist die Identität des
        # Crop-Stores (Name bzw. unknown_N), gewichtet nach Detektionsgüte.
        key = identity_key or name or crop_url
        if crop_url and key:
            prev_crop = self._crops_by_identity.get(key)
            if prev_crop is None or float(det_score) > prev_crop[0]:
                self._crops_by_identity[key] = (float(det_score), crop_url)
        score = float(similarity) if rank > 1 else float(det_score)
        if (rank, score) > self._best_face:
            self._best_face = (rank, score)
            self._best_face_paths = (frame_path, zoom_frame_path, crop_url)
            self._best_face_band = band

    def observe_headcount(self, count: int) -> None:
        """Personenzahl eines Ticks (Objekterkennung) — läuft in JEDEM Tick,
        auch wenn Gesichter gefunden wurden. Sonst bliebe die Bilanz genau
        dort blind, wo sie am meisten weiß: Begleiter ohne erkanntes
        Gesicht tauchten weder im Titel noch im VLM-Prompt auf."""
        self._person_count_max = max(self._person_count_max, int(count))

    def observe_person(
        self, count: int, score: float, frame_path: str, zoom_frame_path: str,
    ) -> None:
        self.observe_headcount(count)
        if float(score) > self._best_person:
            self._best_person = float(score)
            self._best_person_paths = (frame_path, zoom_frame_path)

    # ── Versand-Entscheidung ─────────────────────────────────────────

    def _current_band_rank(self) -> int:
        if self._best_face[0] >= 0:
            return self._best_face[0]
        if self._best_person >= 0:
            return 0
        return -1

    def has_observations(self) -> bool:
        return self._current_band_rank() >= 0

    def due(self, deadline_sec: float) -> bool:
        """Bilanz fällig? Die erste nach ``deadline_sec`` ab Burst-Beginn,
        danach fortlaufend im selben Takt, solange der Burst läuft — ein
        langer Vorbeigang wird so als Serie von Zwischenständen
        dokumentiert statt in einer Meldung am Ende."""
        if not self.has_observations():
            return False
        since = self._last_sent_at or self.started
        return (datetime.now() - since).total_seconds() >= deadline_sec

    def pending_final(self) -> bool:
        """Beim Burst-Ende: muss noch eine (letzte) Meldung raus?
        Ja, wenn nie gemeldet wurde oder seit der letzten Meldung
        Neuigkeiten aufgelaufen sind."""
        if not self.has_observations():
            return False
        return self._sent_messages == 0 or self.has_news()

    def has_news(self) -> bool:
        """Follow-up nötig? Neuer Name, Band-Upgrade oder mehr Personen
        gleichzeitig als bisher gemeldet."""
        if not self._sent_messages:
            return False
        if set(self.known_named) - self._sent_names:
            return True
        if self._current_band_rank() > self._sent_band_rank:
            return True
        return self._person_count_max > max(self._sent_person_count, 1)

    # ── Versand ──────────────────────────────────────────────────────

    async def send(self) -> None:
        """Bilanz/Follow-up verschicken: bestes Bild + alle Namen. Der
        dedup_key trägt einen Laufindex — mehrere Meldungen pro
        Vorkommnis sind hier GEWOLLT (Bilanz + Neuigkeiten).

        Läuft bereits ein Versand, wird die Bilanz vorgemerkt und direkt
        im Anschluss nachgeholt — nie verworfen."""
        if not self.has_observations():
            return
        if self._sending:
            self._catch_up = True
            return
        self._sending = True
        try:
            while True:
                # Takt ab VERSANDBEGINN: die Beschreibung dauert länger als
                # das Intervall; ab Versandende gemessen käme die nächste
                # Bilanz sofort.
                self._last_sent_at = datetime.now()
                await self._send_once()
                if not self._catch_up:
                    break
                self._catch_up = False
        finally:
            self._sending = False
            self._catch_up = False

    def is_sending(self) -> bool:
        """Läuft gerade ein Versand (inkl. VLM-Beschreibung)?"""
        return self._sending

    async def _send_once(self) -> None:
        self._sent_messages += 1
        key = f"{self.cluster_id or self.source_id}:burst-report-{self._sent_messages}"
        band = self._best_face_band
        # Stand FESTHALTEN, bevor der Versand (mit VLM-Beschreibung, ~17 s)
        # beginnt: der Burst tickt derweil weiter. Als „gemeldet" darf nur
        # gelten, was auch wirklich in dieser Meldung steht — sonst
        # verschluckt die Bilanz einen Namen, der mitten im Versand
        # dazukam, und meldet ihn nie nach.
        sent_band_rank = self._current_band_rank()
        sent_names = set(self.known_named)
        sent_person_count = self._person_count_max
        if band:
            names = list(self.known_named)
            sims = (
                list(self.known_named.values()) if names else self._face_sims
            )
            frame_path, zoom_path, crop_url = self._best_face_paths
            await emit_face_alert(
                source_id=self.source_id,
                event_type="face_known" if names else band,
                frame_path=frame_path,
                zoom_frame_path=zoom_path,
                crop_url=crop_url,
                extra_crop_urls=self._identity_crop_urls(),
                cluster_id=self.cluster_id,
                names=names,
                count=max(self._face_count_max, len(names), 1),
                similarities=sims,
                person_count=self._person_count_max,
                previous_description=self._last_description,
                store=self._store,
                dedup_key=key,
            )
        else:
            frame_path, zoom_path = self._best_person_paths
            await emit_person_alert(
                source_id=self.source_id,
                frame_path=frame_path,
                zoom_frame_path=zoom_path,
                cluster_id=self.cluster_id,
                count=max(self._person_count_max, 1),
                previous_description=self._last_description,
                store=self._store,
                dedup_key=key,
            )
        self._sent_band_rank = sent_band_rank
        self._sent_names = sent_names
        self._sent_person_count = sent_person_count
        # Beschreibung dieser Bilanz als Historie für die nächste merken.
        # Der Alert-Pfad schreibt sie in die Cluster-Events zurück — von
        # dort holen wir sie, statt einen zweiten Rückkanal zu bauen.
        self._last_description = self._cluster_description()

    def _identity_crop_urls(self) -> list[str]:
        """Kopfausschnitte aller im Burst gesehenen Personen, beste zuerst.
        Gedeckelt (``VISION_ALERT_MAX_CROPS``) — bei einem Gesicht, das über
        viele Ticks nie sicher gematcht wird, legt der Crop-Store mehrere
        Identitäten an; die Galerie soll davon nicht überlaufen."""
        from .config import VISION_ALERT_MAX_CROPS
        ranked = sorted(
            self._crops_by_identity.values(), key=lambda it: it[0], reverse=True,
        )
        return [url for _score, url in ranked[:VISION_ALERT_MAX_CROPS]]

    def _cluster_description(self) -> str:
        """Die vom Alert-Pfad persistierte VLM-Beschreibung des Clusters."""
        if not self.cluster_id or self._store is None:
            return ""
        try:
            for eid in self._store.list_cluster_event_ids(self.cluster_id):
                ev = self._store.get_event(int(eid))
                desc = str((ev or {}).get("classification", {}).get("description") or "")
                if desc:
                    return desc
        except Exception as e:  # noqa: BLE001
            logger.warning("burst: reading cluster description failed: %s", e)
        return ""
