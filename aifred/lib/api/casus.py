"""The vision's events (the Casus): a page of the list with its filters (every event, or one row per occurrence), the
choices of the filters, the pictures of one occurrence in order (its film), a stream that says when an event was
stored, a person assigned to a face event, events deleted one by one or as filtered. Thin over ``lib/casus.py``.

Under ``/vision/casus/…``: ``/vision/events/{source}`` is the live stream of a camera's image analysis."""

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import StreamingResponse

from ..casus import EventFilter
from ..config import VISION_CASUS_PAGE_SIZE
from ..logging_utils import log_message
from .app import api_app
from .schemas import ChoiceOption


class CasusEvent(BaseModel):
    id: int
    source_id: str
    source_name: str
    event_type: str
    timestamp: str = Field(description="ISO, local time")
    date_display: str
    time_display: str
    crop_url: str = Field(description="The face crop (face events), '' without")
    has_frame: bool = Field(description="The wide picture is kept: /vision/frame?id=")
    has_zoom: bool = Field(description="A tele picture of the same moment: /vision/frame?id=&zoom=1")
    face_id: int | None
    face_name: str = Field(description="The person assigned")
    matched_name: str = Field(description="The person the recognition guesses, while none is assigned")
    description: str = Field(description="What the image model said about the event")
    cluster_id: str = Field(description="Its occurrence ('' = on its own)")
    cluster_member_count: int = Field(description="Grouped: how many events the occurrence has (0 on its own)")


class CasusPage(BaseModel):
    events: list[CasusEvent]
    total: int = Field(description="How many events (grouped: occurrences) the filter lists in all")
    offset: int
    page_size: int


class CasusChoices(BaseModel):
    sources: list[ChoiceOption] = Field(description="Every source events were stored for")
    types: list[ChoiceOption]
    faces: list[ChoiceOption] = Field(description="Every person, by id")
    taggable_types: list[str] = Field(description="The event types a person can be assigned to")


class CasusFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = ""
    type: str = "all"
    face: str = Field("all", description="'all', 'unknown' (not confirmed by a person) or the id of a person")


class FaceAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    face_id: int | None = Field(description="null takes the assignment back")


class Deleted(BaseModel):
    deleted: int


@api_app.get("/vision/casus/events", response_model=CasusPage, tags=["Vision"])
async def casus_events(
    source: str = "", type: str = "all", face: str = "all", grouped: bool = False,
    offset: int = Query(0, ge=0), limit: int = Query(VISION_CASUS_PAGE_SIZE, ge=1, le=VISION_CASUS_PAGE_SIZE),
) -> CasusPage:
    """The events from ``offset`` on, newest first; ``grouped``: one row per occurrence (its newest event). With
    ``limit=1`` the picture at one position of the list (the slideshow over every page). 422 for an unknown filter."""
    from ..casus import page

    try:
        events, total = await asyncio.to_thread(
            page, EventFilter(source=source, type=type, face=face), grouped=grouped, offset=offset, limit=limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return CasusPage(
        events=[CasusEvent(**event, has_frame=bool(event["frame_path"])) for event in events],
        total=total, offset=offset, page_size=VISION_CASUS_PAGE_SIZE,
    )


@api_app.get("/vision/casus/choices", response_model=CasusChoices, tags=["Vision"])
async def casus_choices() -> CasusChoices:
    from ..casus import TAGGABLE_TYPES, choices
    from ..prompt_loader import get_language

    offered = await asyncio.to_thread(choices, get_language())
    return CasusChoices(
        sources=[ChoiceOption(id=source, label=label) for source, label in offered.sources],
        types=[ChoiceOption(id=key, label=label) for key, label in offered.types],
        faces=[ChoiceOption(id=str(face_id), label=name) for face_id, name in offered.faces],
        taggable_types=list(TAGGABLE_TYPES),
    )


class FilmPicture(BaseModel):
    id: int
    has_zoom: bool


@api_app.get("/vision/casus/events/{event_id}/film", response_model=list[FilmPicture], tags=["Vision"])
async def casus_film(event_id: int) -> list[FilmPicture]:
    """The pictures of the event's occurrence, oldest first (the event alone without one); 404 for no event."""
    from ..casus import film

    try:
        pictures = await asyncio.to_thread(film, event_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"no event {event_id}") from error
    return [FilmPicture(id=picture_id, has_zoom=has_zoom) for picture_id, has_zoom in pictures]


@api_app.get("/vision/casus/stream", tags=["Vision"])
async def casus_stream() -> StreamingResponse:
    """Server-sent events: ``stored`` with ``{latest}`` (the newest event id) once at the start and whenever an
    event was stored; the page loads its list again then."""
    from ..vision_store import VisionStore, stored_events

    async def stream() -> AsyncIterator[bytes]:
        yield f"event: stored\ndata: {json.dumps({'latest': await asyncio.to_thread(VisionStore().latest_event_id)})}\n\n".encode()
        async for _ in stored_events():
            latest = await asyncio.to_thread(VisionStore().latest_event_id)
            yield f"event: stored\ndata: {json.dumps({'latest': latest})}\n\n".encode()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@api_app.put("/vision/casus/events/{event_id}/face", status_code=204, tags=["Vision"])
async def casus_assign_face(event_id: int, assignment: FaceAssignment) -> None:
    """Assign a person to a face event, or take the assignment back; 404 for no event, 422 for no such person or an
    event without a face."""
    from ..casus import assign_face

    try:
        await asyncio.to_thread(assign_face, event_id, assignment.face_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"no event {event_id}") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    log_message(f"🗂️ Casus: event {event_id} → person {assignment.face_id}")


@api_app.delete("/vision/casus/events/{event_id}", status_code=204, tags=["Vision"])
async def casus_delete_event(event_id: int) -> None:
    """Delete the event with its pictures; 404 for no event."""
    from ..casus import delete_event

    try:
        await asyncio.to_thread(delete_event, event_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"no event {event_id}") from error
    log_message(f"🗂️ Casus: event {event_id} deleted")


@api_app.post("/vision/casus/events/delete", response_model=Deleted, tags=["Vision"])
async def casus_delete_matching(chosen: CasusFilter) -> Deleted:
    """Delete every event the filter lists (every event without a filter) with its pictures; 422 for an unknown
    filter."""
    from ..casus import delete_matching

    try:
        deleted = await asyncio.to_thread(delete_matching, EventFilter(**chosen.model_dump()))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    log_message(f"🗂️ Casus: {deleted} events deleted ({chosen.model_dump()})")
    return Deleted(deleted=deleted)
