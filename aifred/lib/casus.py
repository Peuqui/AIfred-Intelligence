"""The vision's events (the Casus) for every interface (the vision page of Vue, the Reflex copy until it goes): the
filters a page offers, a page of the list (every event, or one row per occurrence), the choices of the filters, the
pictures of one occurrence in order (its film), a person assigned to an event, events deleted one by one or as
filtered. The events live in vision.db (``VisionStore``), their pictures on disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .vision_store import VisionStore

# The type filter: its key → the event types it lists (none = every type). The order is the order offered.
TYPE_FILTERS: dict[str, tuple[str, ...]] = {
    "all": (),
    "motion": ("motion",),
    "person": ("person",),
    "vehicle": ("vehicle",),
    "animal": ("animal",),
    "face": ("face_known", "face_unsure", "face_unknown"),
    "face_known": ("face_known",),
    "face_unsure": ("face_unsure",),
    "face_unknown": ("face_unknown",),
    "vlm": ("vlm_analysis",),
}
FACE_ALL = "all"
FACE_UNCONFIRMED = "unknown"   # not confirmed by a person: no face, or the face the recognition is unsure of
# The event types a person can be assigned to (the face events).
TAGGABLE_TYPES = TYPE_FILTERS["face"]


@dataclass(frozen=True)
class EventFilter:
    source: str = ""          # a source id; "" = every source
    type: str = "all"         # a key of TYPE_FILTERS
    face: str = FACE_ALL      # FACE_ALL, FACE_UNCONFIRMED or the id of a person

    def query(self) -> dict[str, Any]:
        """The filter as ``VisionStore`` takes it. ValueError for a type or person filter it does not know."""
        if self.type not in TYPE_FILTERS:
            raise ValueError(f"unknown event type filter {self.type!r}")
        face_id: int | None = None
        if self.face not in (FACE_ALL, FACE_UNCONFIRMED):
            if not self.face.isdigit():
                raise ValueError(f"unknown person filter {self.face!r}")
            face_id = int(self.face)
        return {
            "source_id": self.source or None,
            "event_types": list(TYPE_FILTERS[self.type]) or None,
            "face_id": face_id,
            "unknown_only": self.face == FACE_UNCONFIRMED,
        }


def page(events_filter: EventFilter, *, grouped: bool, offset: int, limit: int) -> tuple[list[dict[str, Any]], int]:
    """The events from ``offset`` on, newest first (``grouped``: one row per occurrence, its newest event), and how
    many the filter lists in all."""
    store = VisionStore()
    query = events_filter.query()
    return (store.list_events_with_summary(**query, grouped=grouped, limit=limit, offset=offset),
            store.count_events(**query, grouped=grouped))


@dataclass(frozen=True)
class Choices:
    sources: list[tuple[str, str]]    # (source id, name) of every source events were stored for
    types: list[tuple[str, str]]      # (key, label)
    faces: list[tuple[int, str]]      # (id, name) of every person


def choices(lang: str) -> Choices:
    from .i18n import t

    store = VisionStore()
    labels = store.source_labels()
    # Events without a camera (an upload analysed by hand) have no source to filter by.
    sources = [source for source in store.list_event_source_ids() if source.strip()]
    return Choices(
        sources=[(source, labels.get(source, source)) for source in sources],
        types=[(key, t(f"casus_type_{key}", lang=lang)) for key in TYPE_FILTERS],
        faces=[(int(face["id"]), str(face["name"])) for face in store.list_faces()],
    )


def film(event_id: int) -> list[tuple[int, bool]]:
    """The pictures of the occurrence of ``event_id``, oldest first, one per picture file (the series of a passing
    as a flip-book): ``(event id, has a tele picture)``. An event without a cluster is its own film. KeyError for an
    event that does not exist."""
    store = VisionStore()
    event = store.get_event(event_id)
    if event is None:
        raise KeyError(event_id)
    ids = list(reversed(store.list_cluster_event_ids(str(event.get("cluster_id") or "")))) or [event_id]
    return [(each, bool(store.get_event_frame_path(each, zoom=True))) for each in ids]


def assign_face(event_id: int, face_id: int | None) -> None:
    """Assign the person ``face_id`` to a face event, or take the assignment back (None). KeyError for an event that
    does not exist, ValueError for a person that does not exist or an event without a face."""
    store = VisionStore()
    event = store.get_event(event_id)
    if event is None:
        raise KeyError(event_id)
    if event["event_type"] not in TAGGABLE_TYPES:
        raise ValueError(f"event {event_id} shows no face ({event['event_type']})")
    if face_id is not None and store.get_face_by_id(face_id) is None:
        raise ValueError(f"no person with the id {face_id}")
    store.set_event_face_id(event_id, face_id)


def delete_event(event_id: int) -> None:
    """Delete an event with its pictures. KeyError for an event that does not exist."""
    if not VisionStore().delete_event(event_id):
        raise KeyError(event_id)


def delete_matching(events_filter: EventFilter) -> int:
    """Delete every event the filter lists (all of them without a filter), with their pictures; returns how many."""
    return VisionStore().delete_events_filtered(**events_filter.query())
