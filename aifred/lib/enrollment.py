"""Learning a person from several poses in front of a camera, for every interface (the vision page of Vue, the Reflex
copy until it goes): a session in the server, one at a time (AIfred has one user; a new one replaces the old). Per pose
the freshest picture of the camera (the tele lens where it has one), the largest face in it found by the face model,
its crop framed like every face crop (``face_crop_store.crop_face_from_jpeg``) and its embedding; both stay here until
the session is finished (the person made if new, the embeddings and crops stored, the recognisers load again) or
cancelled. A page gets the pose, the crop's address and the detection score, never the embedding.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from typing import Any

import numpy as np

POSES = ("frontal", "left", "right", "up", "down")   # the order the session asks for them; i18n multipose_pose_<key>


class SessionGone(LookupError):
    """The session the page names is no longer the current one (finished, cancelled, or another one started)."""


class NoFace(ValueError):
    """The face model found no face in the camera's picture (the interfaces say it: i18n ``multipose_no_face``)."""


@dataclass
class Capture:
    pose: str
    crop: bytes                 # JPEG
    embedding: np.ndarray
    score: float


@dataclass
class Session:
    token: str
    face_id: int                # 0: a new person
    name: str
    step: int = 0               # the pose asked for next (len(POSES) = every pose done; more captures still go)
    captures: list[Capture] = field(default_factory=list)

    def pose(self) -> str:
        """The pose asked for now ("" once every pose is done)."""
        return POSES[self.step] if self.step < len(POSES) else ""


_session: Session | None = None


def start(face_id: int = 0) -> Session:
    """A new session for the person ``face_id`` (more poses) or a new person (0); it replaces any other. KeyError
    for no such person."""
    global _session
    name = ""
    if face_id:
        from .vision_store import VisionStore

        face = VisionStore().get_face_by_id(face_id)
        if face is None:
            raise KeyError(face_id)
        name = str(face["name"])
    _session = Session(token=secrets.token_urlsafe(12), face_id=face_id, name=name)
    return _session


def current(token: str) -> Session:
    if _session is None or _session.token != token:
        raise SessionGone(token)
    return _session


async def fresh_face_frame(source_id: str) -> Any:
    """The freshest picture of a camera for a face: over the camera's snapshot API with its tele lens where it has
    them (no stream lag), else the frame hub's snapshot. KeyError for no such camera."""
    from .frame_hub import get_default_hub
    from .frame_sources import get as get_source
    from .vision_snap import snap_frames

    frames = await snap_frames(source_id, 1, prefer_face=True)
    if frames:
        return frames[0]
    source = get_source(source_id)
    if source is None:
        raise KeyError(source_id)
    return await get_default_hub().snapshot(source)


async def capture(token: str, source_id: str) -> Session:
    """Take the pose asked for now (after the last pose: one more) from the camera: the largest face of its freshest
    picture. :class:`SessionGone`, :class:`NoFace`, KeyError for no such camera."""
    from .face_crop_store import crop_face_from_jpeg
    from .vision_filters.face_detect import get_default_detector

    session = current(token)
    frame = await fresh_face_frame(source_id)
    faces = await asyncio.to_thread(get_default_detector().detect, frame)
    if not faces:
        raise NoFace(source_id)
    # The largest face: with several people in front of the camera, the one who stepped up for it.
    face = max(faces, key=lambda found: found.bbox[2] * found.bbox[3])
    picture = bytes(frame.image_bytes)
    crop = await asyncio.to_thread(crop_face_from_jpeg, picture, face.bbox)
    # An empty crop (a box the picture does not hold): the whole picture, as the old dialog showed it.
    session.captures.append(Capture(pose=session.pose() or "extra", crop=crop or picture,
                                    embedding=face.embedding.astype(np.float32), score=float(face.detection_score)))
    session.step += 1
    return session


def skip(token: str) -> Session:
    """Go on to the next pose without a capture. :class:`SessionGone`."""
    session = current(token)
    if session.step < len(POSES):
        session.step += 1
    return session


def undo(token: str) -> Session:
    """Take the last capture back and ask for its pose again. :class:`SessionGone`."""
    session = current(token)
    if session.captures:
        session.captures.pop()
        session.step = max(0, session.step - 1)
    return session


def crop(token: str, index: int) -> bytes:
    """The crop of one capture (JPEG). :class:`SessionGone`, IndexError."""
    return current(token).captures[index].crop


def finish(token: str, name: str = "") -> tuple[int, str, int]:
    """Store the captures: the person made if new (``name``), each embedding with its crop; the recognisers load
    again and the session ends. Returns ``(face_id, name, captures)``. :class:`SessionGone`, ValueError without a
    capture or (new person) without a name."""
    global _session
    from .face_crop_store import get_default_store
    from .vision_filters.face_recognize import bump_enrollment_epoch
    from .vision_store import VisionStore

    session = current(token)
    if not session.captures:
        raise ValueError("no pose was captured")
    store = VisionStore()
    face_id, person = session.face_id, session.name
    if not face_id:
        person = name.strip()
        if not person:
            raise ValueError("a new person needs a name")
        face_id = store.get_or_create_face(person, enrolled_by="multipose")
    crops = get_default_store()
    for taken in session.captures:
        store.add_embedding(face_id, taken.embedding, quality_score=taken.score, crop_url=crops.save_raw(taken.crop, face_id))
    bump_enrollment_epoch()
    _session = None
    return face_id, person, len(session.captures)


def cancel(token: str) -> None:
    """End the session without storing anything (a session no longer current: nothing to do)."""
    global _session
    if _session is not None and _session.token == token:
        _session = None
