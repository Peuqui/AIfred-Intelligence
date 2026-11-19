"""Burst-Abschluss, Teil Medien: die Bilder eines Bursts werden zu Filmen und zu EINER Beschreibung, danach
verschwinden die Einzelbilder.

Am Ende jedes normal beendeten Bursts (``BurstReport.finish``), unabhängig davon, ob eine Meldung rausgeht:
die Menge der Ereignisse mit Einzelbild einmal festhalten, daraus je Linse einen Film bauen, aus denselben
Bildern beschreiben (sie sind der unkomprimierte Inhalt des Films), die Beschreibung an alle diese Ereignisse
schreiben, und erst wenn alle Filme da sind, die Einzelbilder löschen. Frühere Bursts desselben Vorkommnisses
haben ihre Bilder dann schon abgegeben, die Menge enthält nur den eigenen Burst samt Vorlauf.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from .config import VISION_ALERT_ANALYZED_BY
from .vision_event_analysis import analyze_cluster_with_frames
from .vision_film import FilmEncodeError, make_cluster_films
from .vision_routing import NoVisionModelError
from .vision_store import VisionStore

logger = logging.getLogger(__name__)


@dataclass
class BurstMedia:
    """Was der Burst-Abschluss erzeugt hat: die Filme (Dateipfade) und die Beschreibung (leer, wenn
    keiner beschreiben konnte)."""

    films: list[str] = field(default_factory=list)
    description: str = ""


def _one_event_per_picture(events: list[dict[str, Any]]) -> list[int]:
    """Je Bilddatei EINE Ereignis-ID für Film und Beschreibung (ein Gesichts- und ein Personen-Ereignis eines
    Ticks teilen sich das Bild). Bevorzugt das Ereignis mit Identität: aus ihm kommen die Namen für den Prompt."""
    chosen: dict[str, dict[str, Any]] = {}
    for event in events:
        known = chosen.get(str(event["frame_path"]))
        if known is None or (known.get("face_id") is None and event.get("face_id") is not None):
            chosen[str(event["frame_path"])] = event
    return [int(event["id"]) for event in chosen.values()]


async def _describe(
    store: VisionStore, events: list[dict[str, Any]], headcount: int,
) -> str:
    """Die Bilder des Bursts einmal beschreiben (Zusammenfassungs-Prompt) und die Beschreibung an ALLE Ereignisse
    der Menge schreiben, damit Casus und das nächtliche Nachbeschreiben sie haben. Kann keiner beschreiben:
    Warnung, leere Beschreibung."""
    from .prompt_loader import get_vision_event_summary_prompt

    try:
        description, _keyframes = await analyze_cluster_with_frames(
            _one_event_per_picture(events),
            prompt=get_vision_event_summary_prompt(), store=store, headcount=headcount, explicit=False,
        )
    except NoVisionModelError:  # expected: nobody free to describe (an automatic describer displaces nothing)
        logger.info("burst: no describer free, burst without description")
        return ""
    except Exception as error:  # empty answer, backend error, unreadable frames: the burst ends without a description
        logger.warning("burst description failed: %s", error, exc_info=True)
        return ""
    await asyncio.to_thread(
        store.apply_description_to_events,
        [int(event["id"]) for event in events], description, VISION_ALERT_ANALYZED_BY, "burst",
    )
    return description


async def _make_films(cluster_id: str, events: list[dict[str, Any]]) -> list[str] | None:
    """Je Linse ein Film aus den Bildern der Menge. ``None`` bei einem Fehler (Warnung): ohne alle Filme
    dürfen die Einzelbilder nicht gelöscht werden."""
    try:
        return [str(path) for path in await make_cluster_films(cluster_id, events)]
    except (OSError, FilmEncodeError) as error:
        logger.warning("burst film for %s failed, pictures stay: %s", cluster_id, error, exc_info=True)
        return None


async def finish_burst_media(store: VisionStore, cluster_id: str, headcount: int) -> BurstMedia:
    """Der Medien-Abschluss eines Bursts in fester Reihenfolge: Menge festhalten, Filme, Beschreibung, Einzelbilder
    löschen (nur wenn alle Filme da sind; die Beschreibung braucht die Bilder noch). Wirft ``sqlite3.Error``,
    wenn die Ereignisdatenbank nicht lesbar ist."""
    events = await asyncio.to_thread(store.list_cluster_frame_events, cluster_id)
    if not events:
        return BurstMedia()
    films = await _make_films(cluster_id, events)
    description = await _describe(store, events, headcount)
    if films is not None:
        dropped = await asyncio.to_thread(store.drop_event_frames, [int(event["id"]) for event in events])
        logger.info("burst %s: %d film(s), %d picture file(s) removed", cluster_id, len(films), dropped)
    return BurstMedia(films=films or [], description=description)
