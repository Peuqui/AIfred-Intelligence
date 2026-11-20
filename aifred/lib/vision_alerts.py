"""Vision → proactive alerts (the first producer for the alert pipeline).

When the watcher recognises a face while Vigilantia is armed, this emits a
neutral AlertEvent to the shared dispatcher (see alert_bus). The dispatcher's
central rules decide whether/where it actually goes. The dedup_key is the
event's cluster_id, so repeated frames of one happening collapse to one alert.

Kept out of the watcher core: the watcher just calls emit_face_alert().
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import Any

from .config import VISION_QUIET_HOURS_DEFAULT
from .i18n import t
from .vision_autostart import in_clock_window
from .vision_burst_media import BurstMedia, finish_burst_media
from .vision_settings import load_vision_settings

logger = logging.getLogger(__name__)

# Only these carry a "someone is here" meaning worth alerting on.
_ALERT_EVENT_TYPES = {"face_known", "face_unsure", "face_unknown"}


def _vigilantia_armed() -> bool:
    """Read the master arm flag (SSoT in the vision plugin settings)."""
    return bool(load_vision_settings().get("vigilantia_armed", False))


def _alerts_enabled(source_id: str, store: Any) -> bool:
    """Pro-Kamera Alert-Opt-out (SSoT: ``sources.settings.alerts_enabled``).

    Default an. Aus = die Kamera erkennt/speichert/chronisiert weiter, schickt
    aber KEINE proaktiven Push-Alerts — verhindert Dauer-Spam von Kameras, die
    permanent Bewegung sehen (z.B. der eigene Schreibtisch)."""
    rec = store.get_source(source_id) if store else None
    if rec:
        return bool((rec.get("settings") or {}).get("alerts_enabled", True))
    return True


def _alert_passes_filters(source_id: str, store: Any, alert_type: str) -> bool:
    """Pro-Kamera Feinfilter: welche Event-Typen alerten + Ruhezeiten.

    * ``settings.alert_types`` (Liste): nur enthaltene Typen alerten. Fehlt der
      Schlüssel → alle Typen erlaubt (Default).
    * ``settings.quiet_enabled`` + ``quiet_start``/``quiet_end`` ("HH:MM"):
      in diesem Zeitfenster keine Alerts. Über Mitternacht (z.B. 22:30→06:15)
      wird korrekt behandelt.
    ``alert_type`` ist einer von person/vehicle/animal/face."""
    rec = store.get_source(source_id) if store else None
    settings = (rec.get("settings") or {}) if rec else {}

    types = settings.get("alert_types")
    if isinstance(types, list) and alert_type not in types:
        return False

    quiet_now = bool(settings.get("quiet_enabled")) and in_clock_window(
        settings.get("quiet_start", VISION_QUIET_HOURS_DEFAULT[0]),
        settings.get("quiet_end", VISION_QUIET_HOURS_DEFAULT[1]),
        datetime.now(),
    )
    return not quiet_now


def _source_alias(source_id: str, store: Any) -> str:
    """Anzeigename der Kamera für den Alert. Geht über die SSoT
    :meth:`VisionStore.source_label` (Alias > display_name > source_id) —
    derselbe Name wie in den Event-Panels und im Zonen-Editor."""
    from .vision_store import VisionStore
    rec = store.get_source(source_id) if store else None
    if rec:
        return VisionStore.source_label(rec)
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
    rec = store.get_source(source_id) if store else None
    key = ((rec or {}).get("settings") or {}).get("session_group") or source_id
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
    return t("vision_alert_others", count=others)


# Zusatz in Meldungen, die die Kamera ausgelöst hat, ohne dass unser YOLO die Klasse im Bild
# fand (Nacht, Bewegungsunschärfe, weit weg): gilt trotzdem als echte Meldung, die Auswertung
# bekommt den Befund mit.
def yolo_unconfirmed_note() -> str:
    return t("vision_alert_yolo_unconfirmed")


def _source_line(alias: str, ts: datetime, end: datetime | None = None) -> str:
    """Kopfzeile jeder Meldung: „Hauseingang · 25.09.2026 - 18:49". Mit
    Datum, weil eine Vigilantia-Session über Tage wächst und die Bubbles
    sonst nicht mehr einzuordnen sind. ``ts`` ist der Moment des Vorkommnisses
    (nicht des Versands); mit ``end`` steht die Zeitspanne da: „… - 17:00 bis 17:06"
    (an einem anderen Tag mit Datum)."""
    line = f"{alias} · {ts.strftime('%d.%m.%Y - %H:%M')}"
    if end is None or end.strftime("%d.%m.%Y %H:%M") == ts.strftime("%d.%m.%Y %H:%M"):
        return line
    if end.date() == ts.date():
        return f"{line} bis {end.strftime('%H:%M')}"
    return f"{line} bis {end.strftime('%d.%m.%Y - %H:%M')}"


def _compose(
    event_type: str, alias: str, names: list[str], count: int, ts: datetime,
    similarities: list[float] | None = None, person_count: int = 0,
    ts_end: datetime | None = None,
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
            title = t("vision_alert_face_named", names=names_str, others=_others_suffix(names, person_count))
        else:
            title = t("vision_alert_face_known", count=count) + unnamed_suffix
    elif event_type == "face_unsure":
        if names_str:
            title = t("vision_alert_face_possible", names=names_str, others=_others_suffix(names, person_count))
        else:
            title = t("vision_alert_face_unsure", count=count) + unnamed_suffix
    else:  # face_unknown
        title = t("vision_alert_face_unknown", count=count)
    return title, _source_line(alias, ts, ts_end)


async def _emit(
    *,
    source_id: str,
    category: str,
    severity: str,
    title: str,
    body: str,
    dedup_key: str,
    frame_path: str = "",
    zoom_frame_path: str = "",
    crop_url: str = "",
    extra_crop_urls: list[str] | None = None,
    timestamp: datetime,
    metadata: dict[str, Any] | None = None,
    store: Any = None,
    describe: bool = True,
    media_album: list[str] | None = None,
    description: str = "",
) -> None:
    """Build + dispatch one AlertEvent. A busy event database costs this alert
    (logged); the dispatcher guards each rule's delivery itself. The shared
    dispatcher's rules decide where it goes.

    ``media`` (VLM description + single-image channels) is the ZOOM still when
    available — it shows the subject, while the wide-angle latest frame often
    misses it. ``media_gallery`` carries the wide + zoom views plus one crop
    per person seen, as URLs for the browser session.
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
        # Ausschnitte: der des besten Gesichts zuerst, dann die der übrigen
        # im Vorkommnis gesehenen Personen — bei mehreren Leuten zeigt die
        # Galerie so JEDE, nicht nur die am besten erkannte. Ungedeckelt:
        # wer zu sehen war, gehört in die Meldung. Sie stehen in der
        # Chat-Bubble nebeneinander und brechen um,
        # kosten also kaum Platz.
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
            media_album=list(media_album or []),
            describe=describe,
            description=description,
            media_gallery=gallery,
            timestamp=timestamp,
            metadata=metadata or {},
            session_key=session_key,
            session_title=session_title,
        )
        await get_default_dispatcher().emit(ev)
    except sqlite3.Error as e:  # busy database: this alert is lost, logged
        logger.warning("vision alert emit failed for %s: %s", source_id, e)


async def emit_face_alert(
    *,
    source_id: str,
    event_type: str,
    frame_path: str = "",
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
    describe: bool = True,
    media_album: list[str] | None = None,
    timestamp_end: datetime | None = None,
    description: str = "",
) -> None:
    """Emit an aggregated face-band detection as a proactive AlertEvent —
    one per band per happening, only while armed. ``names`` = alle erkannten
    Namen des Bands (ungedeckelt), ``count`` = Anzahl Gesichter des Bands,
    ``similarities`` = deren Match-Werte (Konfidenz-Klammer im Titel).

    ``person_count``: von der Objekterkennung gezählte Personen (Titel-Zusatz
    „+ N weitere" und Fakt fürs VLM). Ohne ``frame_path`` ist die Meldung reiner
    Text (Burst-Meldungen). ``describe=False``: kein VLM. ``media_album``: fertiges
    Medienpaket (Burst-Filme) für Album-Kanäle. ``timestamp`` ist der Beginn
    des Vorkommnisses, ``timestamp_end`` sein Ende (Zeitspanne in der Kopfzeile);
    ``description``: fertige Beschreibung (Zusammenfassung am Burst-Ende), der Alarmbus beschreibt dann nicht neu."""
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
        event_type, alias, names, count, ts, similarities, person_count, timestamp_end,
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
        timestamp=ts,
        metadata=meta or None,
        store=store,
        describe=describe,
        media_album=media_album,
        description=description,
    )


async def emit_person_alert(
    *,
    source_id: str,
    frame_path: str = "",
    zoom_frame_path: str = "",
    cluster_id: str,
    count: int = 1,
    timestamp: datetime | None = None,
    store: Any = None,
    dedup_key: str | None = None,
    extra_crop_urls: list[str] | None = None,
    yolo_unconfirmed: bool = False,
    describe: bool = True,
    media_album: list[str] | None = None,
    timestamp_end: datetime | None = None,
    description: str = "",
) -> None:
    """Emit a YOLO person detection (whole body) as a proactive AlertEvent —
    but only while armed. Coarser than faces: "a person is present", even
    with no recognisable face. ``extra_crop_urls``: Kopf-/Schulterausschnitte
    der gezählten Personen — hier die EINZIGE Nahaufnahme, weil ohne Gesicht
    auch kein Gesichts-Crop existiert.
"""
    if not _vigilantia_armed():
        return
    if not _alerts_enabled(source_id, store):
        return
    if not _alert_passes_filters(source_id, store, "person"):
        return
    ts = timestamp or datetime.now()
    alias = _source_alias(source_id, store)
    title = t("vision_alert_person", count=count)
    meta: dict[str, Any] = {}
    if cluster_id:
        meta["cluster_id"] = cluster_id
    if count > 0:
        meta["person_count"] = count
    body = _source_line(alias, ts, timestamp_end)
    if yolo_unconfirmed:
        meta["yolo_unconfirmed"] = True
        body = f"{body}\n{yolo_unconfirmed_note()}"
    await _emit(
        source_id=source_id,
        category="person",
        severity="warning",
        title=title,
        body=body,
        # One alert per happening; fall back to source if unclustered.
        dedup_key=dedup_key or cluster_id or f"{source_id}:person",
        frame_path=frame_path,
        zoom_frame_path=zoom_frame_path,
        extra_crop_urls=extra_crop_urls,
        timestamp=ts,
        metadata=meta or None,
        store=store,
        describe=describe,
        media_album=media_album,
        description=description,
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
    yolo_unconfirmed: bool = False,
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
    meta: dict[str, Any] = {"cluster_id": cluster_id} if cluster_id else {}
    body = _source_line(alias, ts)
    if yolo_unconfirmed:
        meta["yolo_unconfirmed"] = True
        body = f"{body}\n{yolo_unconfirmed_note()}"
    await _emit(
        source_id=source_id,
        category=object_type,
        severity="info",
        title=title,
        body=body,
        dedup_key=cluster_id or f"{source_id}:{object_type}",
        frame_path=frame_path,
        zoom_frame_path=zoom_frame_path,
        timestamp=ts,
        metadata=meta or None,
        store=store,
    )


# ── Burst-Meldungen ──────────────────────────────────────────────────
# Statt Sofort-Alert beim Kamera-Trigger sammelt der Face-Hunt-Burst seine
# Ticks in einem BurstReport. Während des Bursts gibt es nur Text: EINE
# Erstmeldung nach der Deadline mit dem Ergebnis der Gesichts- bzw.
# Personenerkennung, danach Hinweise nur bei echten Neuigkeiten (neuer Name,
# Band-Upgrade, mehr Personen). Bilder und die Beschreibung kommen erst mit
# der Zusammenfassung am Burst-Ende (Filme, eine Beschreibung).

_BAND_RANK = {"person": 0, "face_unknown": 1, "face_unsure": 2, "face_known": 3}


class BurstReport:
    """Sammelt die Beobachtungen eines Vorbeigangs (Trigger + Burst-Ticks)
    und verschickt daraus die Burst-Meldungen. Lebt für die Dauer EINES
    Bursts (vision_watcher hält ihn pro Quelle)."""

    def __init__(self, source_id: str, cluster_id: str, store: Any) -> None:
        self.source_id = source_id
        self.cluster_id = cluster_id
        self._store = store
        self.started = datetime.now()
        # Bestes Band über alle Gesichts-Beobachtungen; Namen mit bester
        # Similarity werden über den ganzen Burst gesammelt.
        self.known_named: dict[str, float] = {}
        self._best_face_rank = -1
        self._best_face_band = ""
        self._face_count_max = 0
        self._face_sims: list[float] = []
        self._person_count_max = 0
        self._person_seen = False  # eine Personen-Beobachtung ohne Gesicht liegt vor
        self._person_confirmed = False  # YOLO saw a person in at least one observation
        # Versand-Zustand für due()/has_news().
        self._sent_messages = 0
        self._sent_band_rank = -1
        self._sent_names: set[str] = set()
        self._sent_person_count = 0
        # Seit wann liegen unberichtete Neuigkeiten vor? (siehe ``news_due``)
        self._news_since: datetime | None = None
        # Läuft gerade ein Versand? Auch eine reine Text-Meldung kann dauern: Netzwerk-Kanäle
        # (Telegram, E-Mail) und der compose-Modus "llm" (das Modell formuliert den Text) brauchen
        # Sekunden, ein Tick des Bursts dauert weniger. Ohne diese Sperre liefen Meldungen
        # übereinander und kämen in falscher Reihenfolge an.
        self._sending = False
        # Wurde während eines Versands die nächste Meldung fällig? Dann wird sie NACHGEHOLT, nicht
        # verworfen. Ein einzelnes Nachhol-Flag statt einer Warteschlange: jede Meldung trägt ohnehin
        # den neuesten Stand bis zu ihrem Versandbeginn — aufgestaute wären inhaltlich dieselbe,
        # nur älter.
        self._catch_up = False

    # ── Beobachtungen ────────────────────────────────────────────────

    def observe_face(
        self, band: str, name: str, similarity: float, faces_in_tick: int = 1,
    ) -> None:
        rank = _BAND_RANK.get(band, 1)
        if band == "face_known" and name:
            prev = self.known_named.get(name, 0.0)
            self.known_named[name] = max(prev, float(similarity))
        self._face_sims.append(float(similarity))
        self._face_count_max = max(self._face_count_max, int(faces_in_tick))
        if rank > self._best_face_rank:
            self._best_face_rank = rank
            self._best_face_band = band

    def observe_headcount(self, count: int) -> None:
        """Personenzahl eines Ticks (Objekterkennung) — läuft in JEDEM Tick,
        auch wenn Gesichter gefunden wurden. Sonst bliebe die Meldung genau
        dort blind, wo sie am meisten weiß: Begleiter ohne erkanntes
        Gesicht tauchten weder im Titel noch im VLM-Prompt auf."""
        self._person_count_max = max(self._person_count_max, int(count))

    def observe_person(self, count: int, *, yolo_unconfirmed: bool = False) -> None:
        self._person_seen = True
        self._person_confirmed = self._person_confirmed or not yolo_unconfirmed
        self.observe_headcount(count)

    # ── Versand-Entscheidung ─────────────────────────────────────────

    def _current_band_rank(self) -> int:
        if self._best_face_rank >= 0:
            return self._best_face_rank
        if self._person_seen:
            return 0
        return -1

    def has_observations(self) -> bool:
        return self._current_band_rank() >= 0

    def due(self, deadline_sec: float) -> bool:
        """Erstmeldung fällig? Einmal, ``deadline_sec`` nach Burst-Beginn: ein reiner Text mit dem
        Ergebnis der Erkennung. Danach meldet der Burst nur noch Neuigkeiten (``has_news``) und zum
        Schluss die Zusammenfassung mit dem Film (``finish``): die Bilder selbst zeigen, was die
        Personen tun."""
        if self._sent_messages or not self.has_observations():
            return False
        return (datetime.now() - self.started).total_seconds() >= deadline_sec

    def news_due(self, settle_sec: float) -> bool:
        """Hinweis fällig? Neuigkeiten gelten erst als meldereif, wenn sie ``settle_sec``
        vorlagen — mehrere Erkennungen kurz nacheinander ergeben so eine Nachricht mit dem
        neuesten Stand statt einer Kette von Zwischenständen."""
        if not self.has_news():
            self._news_since = None
            return False
        if self._news_since is None:
            self._news_since = datetime.now()
        return (datetime.now() - self._news_since).total_seconds() >= settle_sec

    def has_news(self) -> bool:
        """Hinweis nötig? Neuer Name, Band-Upgrade (außer auf „unsicher“) oder
        mehr Personen gleichzeitig als bisher gemeldet. Eine unsichere Erkennung
        ist als reiner Text-Hinweis nichtssagend; sie steht in der Zusammenfassung."""
        if not self._sent_messages:
            return False
        if set(self.known_named) - self._sent_names:
            return True
        if self._current_band_rank() > self._sent_band_rank and self._best_face_band != "face_unsure":
            return True
        return self._person_count_max > max(self._sent_person_count, 1)

    # ── Versand ──────────────────────────────────────────────────────

    async def send(self) -> None:
        """Erstmeldung bzw. — bei Neuigkeiten — ein kurzer Hinweis. Beide sind reiner Text
        (Ergebnis der Erkennung): kein Bild, keine Galerie, keine Beschreibung.
        Läuft bereits ein Versand, wird die Meldung vorgemerkt und direkt im
        Anschluss nachgeholt — nie verworfen."""
        if not self.has_observations():
            return
        if self._sending:
            self._catch_up = True
            return
        self._sending = True
        try:
            while True:
                if self._sent_messages == 0 or self.has_news():
                    await self._send_once()
                if not self._catch_up:
                    break
                self._catch_up = False
        finally:
            self._sending = False
            self._catch_up = False

    def is_sending(self) -> bool:
        """Läuft gerade ein Versand?"""
        return self._sending

    def _span(self) -> tuple[datetime, datetime] | None:
        """Beginn und Ende des Vorkommnisses (erstes/letztes Ereignis im Cluster) —
        die Kopfzeile nennt den Moment des Vorkommnisses, nicht den des Versands."""
        if not self.cluster_id or self._store is None:
            return None
        span: tuple[datetime, datetime] | None = self._store.cluster_time_span(self.cluster_id)
        return span

    async def finish(self) -> None:
        """Abschluss des Bursts, immer am Ende eines normal beendeten Bursts: erst die Medien (Filme, EINE
        Beschreibung, Einzelbilder weg; ``finish_burst_media``), danach — nur mit Beobachtung — die
        Zusammenfassung mit Filmen und Beschreibung, ohne Standbild: die Einzelbilder sind dann gelöscht. Ist die
        Ereignisdatenbank belegt oder ein Bild nicht zu verkleinern, bleiben die Bilder liegen und die
        Zusammenfassung geht ohne Film raus."""
        media = BurstMedia()
        if self.cluster_id and self._store is not None:
            try:
                media = await finish_burst_media(self._store, self.cluster_id, self._person_count_max)
            # Busy database, a picture that cannot be read or a preview that cannot be written: the pictures stay
            # (drop_event_frames changes nothing then), the summary goes out without film.
            except (sqlite3.Error, OSError, ValueError) as e:
                logger.warning("burst media for %s failed: %s", self.cluster_id, e, exc_info=True)
        if self.has_observations():
            await self._send_once(films=media.films, description=media.description, summary=True)

    async def _send_once(
        self, *, films: list[str] | None = None, description: str = "", summary: bool = False,
    ) -> None:
        self._sent_messages += 1
        key = f"{self.cluster_id or self.source_id}:burst-report-{self._sent_messages}"
        band = self._best_face_band
        # Stand FESTHALTEN, bevor der Versand beginnt: der Burst tickt derweil weiter. Als „gemeldet"
        # darf nur gelten, was auch wirklich in dieser Meldung steht — sonst verschluckt die Meldung
        # einen Namen, der mitten im Versand dazukam, und meldet ihn nie nach.
        sent_band_rank = self._current_band_rank()
        sent_names = set(self.known_named)
        sent_person_count = self._person_count_max
        span = self._span()
        started = span[0] if span else None
        # Die Zusammenfassung nennt die Zeitspanne, Erstmeldung und Hinweise den Beginn.
        ended = span[1] if span and summary else None
        # Alle Burst-Meldungen sind Text ohne Bild und ohne eigene Beschreibung: die Zusammenfassung bringt ihre
        # Beschreibung fertig mit (``description``), der Alarmbus beschreibt nie selbst.
        if band:
            names = list(self.known_named)
            sims = (
                list(self.known_named.values()) if names else self._face_sims
            )
            await emit_face_alert(
                source_id=self.source_id,
                event_type="face_known" if names else band,
                cluster_id=self.cluster_id,
                names=names,
                count=max(self._face_count_max, len(names), 1),
                similarities=sims,
                person_count=self._person_count_max,
                store=self._store,
                dedup_key=key,
                describe=False,
                description=description,
                media_album=films,
                timestamp=started,
                timestamp_end=ended,
            )
        else:
            await emit_person_alert(
                source_id=self.source_id,
                cluster_id=self.cluster_id,
                count=max(self._person_count_max, 1),
                store=self._store,
                dedup_key=key,
                yolo_unconfirmed=not self._person_confirmed,
                describe=False,
                description=description,
                media_album=films,
                timestamp=started,
                timestamp_end=ended,
            )
        self._sent_band_rank = sent_band_rank
        self._sent_names = sent_names
        self._sent_person_count = sent_person_count
        self._news_since = None
