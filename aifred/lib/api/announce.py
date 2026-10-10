"""Announce endpoints: a spoken announcement to FreeEcho.2 pucks, no LLM.

For callers that already have the text (e.g. Agent-Orc reading out an agent's
answer). The text goes through the same delivery path as the proactive alerts
(announce_to_channel → TTS of the FreeEcho.2 plugin → alert queue), so a
running announcement is not interrupted: items of one room play one after
the other. Token-guarded like inject/webhook, with ``Authorization: Bearer``.
"""

from typing import Annotated, Dict, List

from fastapi import Header, HTTPException
from pydantic import BaseModel, Field, model_validator

from ..auth import require_service_token
from ..config import (
    ANNOUNCE_MAX_CHARS, ANNOUNCE_MAX_PAUSE_MS, ANNOUNCE_MAX_TOTAL_CHARS, ANNOUNCE_PAUSE_MS,
)
from ..logging_utils import log_message
from ..message_processor import announce_into_rooms, resolve_announce_targets
from .app import api_app
from .schemas import CallerName

# Audio type of the puck chime: always the gentle one, "alarm" is not offered here
ANNOUNCE_AUDIO_TYPE = "notification"


class AnnounceRequest(BaseModel):
    """Spoken announcement request: ONE text or several paragraphs (``texts``)"""
    room: str = Field(
        ..., min_length=1,
        description="Room name from GET /audio/announce/rooms, '@group' for a group, '*' for all connected rooms",
    )
    text: str | None = Field(None, min_length=1, description="Text to read aloud")
    texts: List[Annotated[str, Field(min_length=1)]] | None = Field(
        None, min_length=1,
        description="Several paragraphs, read as ONE announcement (one start tone before the "
        "first, one end tone after the last, silence between them)",
    )
    pause_ms: int | None = Field(
        None, ge=0, le=ANNOUNCE_MAX_PAUSE_MS,
        description="Silence between the paragraphs of texts in ms (default ANNOUNCE_PAUSE_MS)",
    )
    speaker: CallerName

    @model_validator(mode="after")
    def _exactly_one_of_text_and_texts(self) -> "AnnounceRequest":
        if (self.text is None) == (self.texts is None):
            raise ValueError("give exactly one of 'text' and 'texts'")
        return self

    @property
    def paragraphs(self) -> List[str]:
        return self.texts if self.texts is not None else [self.text or ""]


class AnnounceRoomsResponse(BaseModel):
    """Rooms that currently accept announcements"""
    rooms: List[str]


class AnnounceResponse(BaseModel):
    """Announcement response"""
    success: bool
    rooms: List[str]


def _require_announce_token(authorization: str | None) -> None:
    """Check the ``Authorization: Bearer <token>`` header against ANNOUNCE_API_TOKEN."""
    scheme, _, token = (authorization or "").partition(" ")
    require_service_token("announce", token if scheme.lower() == "bearer" else None)


@api_app.get("/audio/announce/rooms", response_model=AnnounceRoomsResponse, tags=["Audio"])
async def announce_rooms(authorization: str | None = Header(None)) -> Dict[str, List[str]]:
    """The FreeEcho.2 rooms connected right now — the valid values of ``room``."""
    _require_announce_token(authorization)
    return {"rooms": resolve_announce_targets("freeecho2", "*")}


@api_app.post("/audio/announce", response_model=AnnounceResponse, tags=["Audio"])
async def announce(request: AnnounceRequest, authorization: str | None = Header(None)) -> Dict[str, object]:
    """Read the text (or the paragraphs of ``texts`` as ONE announcement) aloud in ``room``
    (404 for an unknown or not connected room, 413 for a text longer than
    ``ANNOUNCE_MAX_CHARS`` per entry or ``ANNOUNCE_MAX_TOTAL_CHARS`` in total)."""
    _require_announce_token(authorization)
    paragraphs = request.paragraphs
    too_long = [len(p) for p in paragraphs if len(p) > ANNOUNCE_MAX_CHARS]
    if too_long:
        raise HTTPException(
            status_code=413,
            detail=f"an entry has {max(too_long)} characters, the limit is {ANNOUNCE_MAX_CHARS} per entry",
        )
    total = sum(len(p) for p in paragraphs)
    if total > ANNOUNCE_MAX_TOTAL_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"the texts have {total} characters in total, the limit is {ANNOUNCE_MAX_TOTAL_CHARS}",
        )
    rooms = resolve_announce_targets("freeecho2", request.room)
    if not rooms:
        raise HTTPException(status_code=404, detail=f"no connected FreeEcho.2 room '{request.room}'")

    pause_ms = ANNOUNCE_PAUSE_MS if request.pause_ms is None else request.pause_ms
    # One stream: whether a start tone before the first and an end tone after the last paragraph
    # are requested is a setting of the Echo plugin (which sound plays, the puck decides) —
    # no flags from the caller.
    metadata = {
        "audio_type": ANNOUNCE_AUDIO_TYPE, "proactive": True,
        "paragraphs": paragraphs, "pause_ms": pause_ms,
    }
    spoken = " ".join(paragraphs)
    reached = await announce_into_rooms("freeecho2", rooms, spoken, request.speaker, metadata)
    if not reached:
        raise HTTPException(status_code=502, detail="the announcement could not be delivered")
    log_message(f"Announce API: {len(paragraphs)} paragraph(s), {total} characters to {reached}")
    return {"success": True, "rooms": reached}
