"""The vision's events (the Casus) for every interface (the vision page of Vue): the
filters a page offers, a page of the list (every event, or one row per occurrence), the choices of the filters, the
pictures of one occurrence in order (its film), a person assigned to an event, events deleted one by one or as
filtered; and the image model on events: one event analysed, every event without a description described. Both are
runs of the server, one at a time each for every interface; a page follows their state. The events live in vision.db
(``VisionStore``), their pictures on disk.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from typing import Any

from .user_error import Conflict, Invalid, Missing
from .vision_store import VisionStore

logger = logging.getLogger(__name__)

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
            raise Invalid("unknown_filter", value=self.type)
        face_id: int | None = None
        if self.face not in (FACE_ALL, FACE_UNCONFIRMED):
            if not self.face.isdigit():
                raise Invalid("unknown_filter", value=self.face)
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


def recent(window_minutes: int, newest: int) -> tuple[list[dict[str, Any]], int]:
    """The ``newest`` events (every type, newest first) and how many were stored in the last ``window_minutes`` —
    the vision's badge in the chat."""
    from datetime import datetime, timedelta

    store = VisionStore()
    since = datetime.now() - timedelta(minutes=window_minutes)
    return store.list_events_with_summary(limit=newest), store.count_events(since=since)


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
        raise Missing("no_event", event=event_id)
    ids = list(reversed(store.list_cluster_event_ids(str(event.get("cluster_id") or "")))) or [event_id]
    return [(each, bool(store.get_event_frame_path(each, zoom=True))) for each in ids]


def assign_face(event_id: int, face_id: int | None) -> None:
    """Assign the person ``face_id`` to a face event, or take the assignment back (None). KeyError for an event that
    does not exist, ValueError for a person that does not exist or an event without a face."""
    store = VisionStore()
    event = store.get_event(event_id)
    if event is None:
        raise Missing("no_event", event=event_id)
    if event["event_type"] not in TAGGABLE_TYPES:
        raise Invalid("event_without_face", event=event_id)
    if face_id is not None and store.get_face_by_id(face_id) is None:
        raise Invalid("no_person", person=face_id)
    store.set_event_face_id(event_id, face_id)


def delete_event(event_id: int) -> None:
    """Delete an event with its pictures. KeyError for an event that does not exist."""
    if not VisionStore().delete_event(event_id):
        raise Missing("no_event", event=event_id)


def delete_matching(events_filter: EventFilter) -> int:
    """Delete every event the filter lists (all of them without a filter), with their pictures; returns how many."""
    return VisionStore().delete_events_filtered(**events_filter.query())


# ── The image model on events: one event, or every event without a description ─────────────────────────────────


class RunInProgress(Conflict):
    """The run of this kind already goes on (one at a time)."""


@dataclass(frozen=True)
class Analysis:
    running: int = 0       # the event being analysed (0 = none)
    last: int = 0          # the event analysed last
    error: str = ""        # why that one failed ("" = it did not)


@dataclass(frozen=True)
class DescribeAll:
    running: bool = False
    phase: str = ""        # checking (does the model fit), grouping, describing; "" before the first run
    events: int = 0        # the events without a description it found
    clusters: int = 0      # the occurrences they fall into (described once each)
    processed: int = 0
    failed: int = 0
    outcome: str = ""      # after a run: done, cancelled, nothing, busy (another caller's run), no_room, error
    detail: str = ""       # no_room: what the model needs; error: the error
    cancelling: bool = False

    def message(self, lang: str) -> str:
        """The state in words ("" before the first run)."""
        from .formatting import format_number
        from .i18n import t

        if self.running:
            key = "casus_bulk_cancelling" if self.cancelling else f"casus_bulk_{self.phase}"
        elif self.outcome == "done" and self.failed:
            key = "casus_bulk_done_failed"
        elif self.outcome:
            key = f"casus_bulk_{self.outcome}"
        else:
            return ""
        return t(key, lang=lang, detail=self.detail, events=format_number(self.events, 0),
                 clusters=format_number(self.clusters, 0), processed=format_number(self.processed, 0),
                 failed=format_number(self.failed, 0))


_analysis = Analysis()
_describe_all = DescribeAll()
_runs: set[asyncio.Task[None]] = set()     # kept until they end (a task nobody holds may be collected)


def analysis() -> Analysis:
    return _analysis


def describe_all() -> DescribeAll:
    return _describe_all


def _keep(run: asyncio.Task[None]) -> asyncio.Task[None]:
    _runs.add(run)
    run.add_done_callback(_runs.discard)
    return run


def start_analysis(event_id: int) -> asyncio.Task[None]:
    """Let the image model describe one event (its tele picture first, the wide one as the scene); the description
    goes into the event. :class:`RunInProgress` while another event is analysed, KeyError for no event, ValueError
    for an event without a picture."""
    global _analysis
    if _analysis.running:
        raise RunInProgress("analysis_running", event=_analysis.running)
    event = VisionStore().get_event(event_id)
    if event is None:
        raise Missing("no_event", event=event_id)
    if not event.get("frame_path"):
        raise Invalid("event_without_picture", event=event_id)
    _analysis = Analysis(running=event_id, last=_analysis.last, error=_analysis.error)
    return _keep(asyncio.create_task(_analyse(event_id)))


async def _analyse(event_id: int) -> None:
    global _analysis
    from .vision_event_analysis import analyze_event_with_vlm

    error = ""
    try:
        await analyze_event_with_vlm(event_id)
    except Exception as failure:  # the page shows why the analysis failed (backend, picture gone, model)
        logger.warning("Casus analysis of event %d failed: %s", event_id, failure, exc_info=True)
        error = str(failure) or type(failure).__name__
    finally:
        _analysis = Analysis(running=0, last=event_id, error=error)


def start_describe_all() -> asyncio.Task[None]:
    """Describe every event without a description, once per occurrence (``vision_bulk.run_bulk_describe``, after
    checking that the image model fits on a card). :class:`RunInProgress` while one runs."""
    global _describe_all
    if _describe_all.running:
        raise RunInProgress("describe_all_running")
    _describe_all = DescribeAll(running=True, phase="checking")
    return _keep(asyncio.create_task(_describe_every_event()))


def cancel_describe_all() -> None:
    """Stop the run after the occurrence it describes now (its request to the model is cut off)."""
    global _describe_all
    if _describe_all.running:
        _describe_all = replace(_describe_all, cancelling=True)


async def _describe_every_event() -> None:
    global _describe_all
    from .vision_bulk import run_bulk_describe

    async def progress(events: int, clusters: int, processed: int) -> None:
        global _describe_all
        _describe_all = replace(_describe_all, phase="describing" if clusters else "grouping",
                                events=events, clusters=clusters, processed=processed)

    async def cancel() -> bool:
        return _describe_all.cancelling

    try:
        result = await run_bulk_describe(progress_cb=progress, cancel_cb=cancel, check_vram=True)
    except Exception as failure:  # the page shows why the run stopped
        logger.warning("Casus: describing every event failed: %s", failure, exc_info=True)
        _describe_all = replace(_describe_all, running=False, cancelling=False, outcome="error",
                                detail=str(failure) or type(failure).__name__)
        return
    if result.skipped:
        outcome = "busy"
    elif result.aborted_vram:
        outcome = "no_room"
    elif result.total_events == 0:
        outcome = "nothing"
    else:
        outcome = "cancelled" if result.cancelled else "done"
    _describe_all = replace(_describe_all, running=False, cancelling=False, outcome=outcome, detail=result.vram_message,
                            events=result.total_events, clusters=result.total_clusters, processed=result.processed,
                            failed=result.failed)
