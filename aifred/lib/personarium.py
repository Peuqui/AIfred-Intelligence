"""The persons of the vision for every interface (the vision page of Vue, the Reflex copy until it goes): every person
with a picture, how many embeddings it has and when it was seen last; a person renamed or deleted; a person's
embeddings, one deleted; the faces no person is confirmed for yet, one card per crop file (the events of one sighting
share it), assigned (and learned) or dismissed; every open face recognised again against the embeddings, a run of
the server, one at a time. A change of what the recognition knows makes the running recognisers load again.

The face model runs on the CPU unless ``FACE_DETECT_USE_GPU`` says otherwise (``vision_filters.face_detect``).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from .user_error import Conflict, Invalid, Missing
from .vision_store import VisionStore

logger = logging.getLogger(__name__)

OPEN_FACE_TYPES = ("face_unknown", "face_unsure")


def _recognisers_reload() -> None:
    from .vision_filters.face_recognize import bump_enrollment_epoch

    bump_enrollment_epoch()


def persons() -> list[dict[str, Any]]:
    """Every person: ``id, name, notes, embedding_count, last_seen`` (ISO, '' = never), ``last_seen_display`` (as the
    lists show a moment) and ``crop_url`` (its picture), by name."""
    from datetime import datetime

    from .formatting import format_stamp

    return [{**row, "last_seen_display": format_stamp(datetime.fromisoformat(row["last_seen"]).timestamp()) if row["last_seen"] else ""}
            for row in VisionStore().list_faces_with_summary()]


def rename(face_id: int, name: str) -> None:
    """Missing for no such person, Invalid for an empty name, Conflict for one another person has (the store's
    check, in the same transaction as the change)."""
    clean = name.strip()
    if not clean:
        raise Invalid("person_needs_name")
    if not VisionStore().rename_face(face_id, clean):
        raise Missing("no_person", person=face_id)
    _recognisers_reload()


def delete(face_id: int) -> tuple[str, int]:
    """Delete a person with its embeddings (its events keep no person); returns its name and how many embeddings
    went. The crops stay on disk until the cleanup's retention. KeyError for no such person."""
    store = VisionStore()
    face = store.get_face_by_id(face_id)
    if face is None:
        raise Missing("no_person", person=face_id)
    deleted = store.delete_face_with_assets(face_id)
    _recognisers_reload()
    return str(face["name"]), int(deleted["embeddings_deleted"])


def embeddings(face_id: int) -> list[dict[str, Any]]:
    """A person's embeddings, newest first: ``id, quality, created_at, crop_url`` (not the vector). KeyError for no
    such person."""
    store = VisionStore()
    if store.get_face_by_id(face_id) is None:
        raise Missing("no_person", person=face_id)
    rows = sorted(store.list_embeddings(face_id), key=lambda row: int(row.get("id") or 0), reverse=True)
    return [{"id": int(row["id"]), "quality": float(row.get("quality_score") or 0.0),
             "created_at": str(row.get("created_at") or "")[:19].replace("T", " "),
             "crop_url": str(row.get("crop_url") or "")} for row in rows]


def delete_embedding(embedding_id: int) -> None:
    """KeyError for no such embedding."""
    if not VisionStore().delete_embedding(embedding_id):
        raise Missing("no_embedding", embedding=embedding_id)
    _recognisers_reload()


@dataclass(frozen=True)
class Card:
    """The open faces of one sighting: they share one crop file; the newest event leads."""
    id: int
    member_ids: list[int]
    crop_url: str
    event_type: str          # face_unsure: the recognition guesses ``matched_name``
    matched_name: str
    source_name: str
    date_display: str
    time_display: str


def open_faces() -> list[Card]:
    """Every face no person is confirmed for and nobody dismissed, newest first, one card per crop file (no cap)."""
    rows = VisionStore().list_events_with_summary(event_types=list(OPEN_FACE_TYPES), unknown_only=True, limit=None)
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["crop_url"] and not row["untagged_dismissed"] and not row["identity_confirmed"]:
            groups.setdefault(str(row["crop_url"]), []).append(row)
    return [Card(int(members[0]["id"]), [int(member["id"]) for member in members], str(members[0]["crop_url"]),
                 str(members[0]["event_type"]), str(members[0]["matched_name"]), str(members[0]["source_name"]),
                 str(members[0]["date_display"]), str(members[0]["time_display"]))
            for members in groups.values()]


def _members(card_ids: list[int]) -> dict[int, list[int]]:
    """The events behind the cards ``card_ids`` (a card that is no longer open: KeyError)."""
    cards = {card.id: card.member_ids for card in open_faces()}
    if any(card_id not in cards for card_id in card_ids):
        raise Missing("open_face_gone")
    return {card_id: cards[card_id] for card_id in card_ids}


def dismiss(card_ids: list[int]) -> int:
    """Take the cards out of the open faces (a crop too poor to learn from); their events stay in the Casus. Returns
    how many events. KeyError for a card that is no longer open."""
    return VisionStore().dismiss_untagged_events([event for members in _members(card_ids).values() for event in members])


@dataclass(frozen=True)
class Assigned:
    face_id: int
    name: str
    is_new: bool
    quality: float
    learned: bool               # False: the person had an embedding almost the same, the face was only assigned
    similarity: float
    resolved: int               # further events of the occurrence the recognition now assigns too

    def message(self, lang: str) -> str:
        from .formatting import format_number
        from .i18n import t

        if self.learned:
            text = t("personarium_learned", lang=lang, name=self.name, quality=format_number(self.quality, 2))
        else:
            text = t("personarium_known_already", lang=lang, name=self.name, similarity=format_number(self.similarity * 100, 0))
        if self.resolved:
            text += t("personarium_resolved_too", lang=lang, count=self.resolved)
        return text


async def assign(card_id: int, *, face_id: int | None = None, name: str | None = None) -> Assigned:
    """Assign the card's face to the person ``face_id`` or the person called ``name`` (made if new) and learn it
    (``face_enroll.enroll_face_from_event`` on its newest event; the other events of the card share its picture and
    are only assigned). KeyError for a card that is no longer open, ValueError for no such person, an empty name or
    a face the model no longer finds in the picture."""
    from .face_enroll import confirm_group_events, enroll_face_from_event

    members = (await asyncio.to_thread(_members, [card_id]))[card_id]
    if face_id is not None:
        result = await enroll_face_from_event(card_id, face_id=face_id)
    else:
        result = await enroll_face_from_event(card_id, name=name or "")
    await asyncio.to_thread(confirm_group_events, [event for event in members if event != card_id], int(result["face_id"]))
    return Assigned(face_id=int(result["face_id"]), name=str(result["name"]), is_new=bool(result["is_new"]),
                    quality=float(result["quality"]), learned=not result["embedding_skipped"],
                    similarity=float(result["similarity"]), resolved=int(result.get("siblings_resolved") or 0))


# ── Recognise every open face again (a run of the server, one at a time) ───────────────────────────────────────


@dataclass(frozen=True)
class Rematch:
    running: bool = False
    checked: int = 0
    resolved: int = 0
    by_name: dict[str, int] = field(default_factory=dict)
    error: str = ""
    ran: bool = False           # it ran at least once (so there is an outcome to tell)

    def message(self, lang: str) -> str:
        from .formatting import format_number
        from .i18n import t

        if self.running:
            return t("personarium_rematch_running", lang=lang)
        if self.error:
            return f"⚠️ {self.error}"
        if not self.ran:
            return ""
        text = t("personarium_rematch_done", lang=lang, resolved=format_number(self.resolved, 0),
                 checked=format_number(self.checked, 0))
        if self.by_name:
            text += f" ({', '.join(f'{name}: {format_number(count, 0)}' for name, count in sorted(self.by_name.items()))})"
        rest = self.checked - self.resolved
        if rest:
            text += t("personarium_rematch_rest", lang=lang, count=rest)
        return text


class RematchRunning(Conflict):
    """The faces are being recognised again already."""


_rematch = Rematch()
_runs: set[asyncio.Task[None]] = set()


def rematch() -> Rematch:
    return _rematch


def start_rematch() -> asyncio.Task[None]:
    """Recognise every open face again against the embeddings; sure matches are assigned (no new embeddings), the
    rest stays open. :class:`RematchRunning` while it runs."""
    global _rematch
    if _rematch.running:
        raise RematchRunning("rematch_running")
    _rematch = Rematch(running=True)
    run = asyncio.create_task(_recognise_again())
    _runs.add(run)
    run.add_done_callback(_runs.discard)
    return run


async def _recognise_again() -> None:
    global _rematch
    from .face_enroll import rematch_untagged_faces

    try:
        result = await rematch_untagged_faces()
    except Exception as failure:  # the page shows why (face model, picture files, store)
        logger.warning("Personarium: recognising the open faces again failed: %s", failure, exc_info=True)
        _rematch = Rematch(error=str(failure) or type(failure).__name__, ran=True)
        return
    _rematch = Rematch(checked=int(result["checked"]), resolved=int(result["resolved"]),
                       by_name={str(name): int(count) for name, count in result["by_name"].items()}, ran=True)
