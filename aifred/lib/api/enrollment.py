"""Learning a person from several poses (``lib/enrollment.py``): a session in the server, one at a time; the page
gets each capture's pose, crop address and detection score, never the embedding. The camera's live picture for it
comes over ``/vision/enroll/preview/{source}`` (the tele lens where the camera has one)."""

import asyncio

from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from ..enrollment import NoFace, Session, SessionGone
from ..logging_utils import log_message
from .app import api_app
from .schemas import ChoiceOption


class EnrollCapture(BaseModel):
    pose: str
    url: str = Field(description="The crop of the capture")
    score: float


class EnrollSession(BaseModel):
    token: str
    face_id: int = Field(description="0: a new person")
    name: str
    poses: list[ChoiceOption] = Field(description="Each pose with its instruction, in the order asked")
    step: int = Field(description="The pose asked for next; len(poses) once every pose is done")
    captures: list[EnrollCapture]
    preview_interval_ms: int = Field(description="How long the live picture waits after one arrived before the next")


class EnrollStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    face_id: int = Field(0, description="More poses for this person; 0 = a new person")


class EnrollCaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str


class EnrollFinish(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field("", description="The new person's name (not for more poses of a person)")


class EnrollFinished(BaseModel):
    face_id: int
    message: str


def _session(session: Session) -> EnrollSession:
    from ..config import VISION_ENROLL_PREVIEW_MS
    from ..enrollment import POSES
    from ..i18n import t
    from ..prompt_loader import get_language

    lang = get_language()
    return EnrollSession(
        token=session.token, face_id=session.face_id, name=session.name, step=session.step,
        poses=[ChoiceOption(id=pose, label=t(f"multipose_pose_{pose}", lang=lang)) for pose in POSES],
        captures=[EnrollCapture(pose=taken.pose, url=f"vision/enroll/{session.token}/captures/{index}", score=taken.score)
                  for index, taken in enumerate(session.captures)],
        preview_interval_ms=VISION_ENROLL_PREVIEW_MS,
    )


def _gone(error: SessionGone) -> HTTPException:
    return HTTPException(status_code=409, detail=f"the enrollment session {error.args[0]} is no longer the current one")


@api_app.post("/vision/enroll", response_model=EnrollSession, tags=["Vision"])
async def enroll_start(start: EnrollStart) -> EnrollSession:
    """A new session (it replaces any other): more poses for a person, or a new one. 404 for no such person."""
    from ..enrollment import start as start_session

    try:
        return _session(await asyncio.to_thread(start_session, start.face_id))
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"face {start.face_id} not found") from error


@api_app.get("/vision/enroll/preview/{source_id:path}", tags=["Vision"])
async def enroll_preview(source_id: str) -> Response:
    """The camera's freshest picture for a face (JPEG; the tele lens where it has one). 404 for no such camera."""
    from ..enrollment import fresh_face_frame

    try:
        frame = await fresh_face_frame(source_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"unknown camera: {source_id}") from error
    return Response(content=bytes(frame.image_bytes), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@api_app.post("/vision/enroll/{token}/capture", response_model=EnrollSession, tags=["Vision"])
async def enroll_capture(token: str, request: EnrollCaptureRequest) -> EnrollSession:
    """Take the pose asked for now from the camera (the face model finds the largest face, on the CPU unless set
    otherwise). 409 for a session no longer current, 404 for no such camera, 422 when no face is in the picture."""
    from ..enrollment import capture

    try:
        return _session(await capture(token, request.source_id))
    except SessionGone as error:
        raise _gone(error) from error
    except NoFace as error:
        from ..i18n import t
        from ..prompt_loader import get_language

        raise HTTPException(status_code=422, detail=t("multipose_no_face", lang=get_language())) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"unknown camera: {request.source_id}") from error


@api_app.post("/vision/enroll/{token}/skip", response_model=EnrollSession, tags=["Vision"])
async def enroll_skip(token: str) -> EnrollSession:
    from ..enrollment import skip

    try:
        return _session(skip(token))
    except SessionGone as error:
        raise _gone(error) from error


@api_app.delete("/vision/enroll/{token}/captures/last", response_model=EnrollSession, tags=["Vision"])
async def enroll_undo(token: str) -> EnrollSession:
    """Take the last capture back; its pose is asked for again."""
    from ..enrollment import undo

    try:
        return _session(undo(token))
    except SessionGone as error:
        raise _gone(error) from error


@api_app.get("/vision/enroll/{token}/captures/{index}", tags=["Vision"])
async def enroll_capture_crop(token: str, index: int) -> Response:
    from ..enrollment import crop

    try:
        return Response(content=crop(token, index), media_type="image/jpeg", headers={"Cache-Control": "no-store"})
    except SessionGone as error:
        raise _gone(error) from error
    except IndexError as error:
        raise HTTPException(status_code=404, detail=f"no capture {index}") from error


@api_app.post("/vision/enroll/{token}/finish", response_model=EnrollFinished, tags=["Vision"])
async def enroll_finish(token: str, request: EnrollFinish) -> EnrollFinished:
    """Store the captures (the person made if new) and end the session; the recognition knows the poses at once. 409
    for a session no longer current, 422 without a capture or a new person without a name."""
    from ..enrollment import finish
    from ..i18n import t
    from ..prompt_loader import get_language

    try:
        face_id, name, count = await asyncio.to_thread(finish, token, request.name)
    except SessionGone as error:
        raise _gone(error) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    log_message(f"👤 {count} poses of {name} learned")
    return EnrollFinished(face_id=face_id, message=t("multipose_saved", lang=get_language(), name=name, count=count))


@api_app.delete("/vision/enroll/{token}", status_code=204, tags=["Vision"])
async def enroll_cancel(token: str) -> None:
    """End the session without storing anything."""
    from ..enrollment import cancel

    cancel(token)
