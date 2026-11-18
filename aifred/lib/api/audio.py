"""Audio Player API (file streaming + position sync)."""

import asyncio
from pathlib import Path

from fastapi import Request
from pydantic import BaseModel

from ..audio_player_settings import build_configured_resolver
from ..logging_utils import log_message
from ..user_error import BadRequest, Missing, UserError
from .app import audio_player_api


class UnsupportedAudio(UserError):
    """A file of a format the player does not stream (415)."""

    status = 415


class RangeNotSatisfiable(UserError):
    """A range outside the file (416). No ValueError: the parser below turns those into 400."""

    status = 416


@audio_player_api.get("/audio/file", tags=["Audio"])
async def audio_file(request: Request, key: str):
    """Stream an audio file by state_key. Supports HTTP Range for seeking.

    The key is resolved against the audio_player plugin's source map, so
    the LLM never sees raw paths. Path-traversal is rejected by the resolver.
    """
    from fastapi.responses import RedirectResponse, StreamingResponse

    from ..audio_sources import ALLOWED_EXTENSIONS

    # Built outside the try: a corrupt settings.json raises JSONDecodeError, a ValueError
    # that must not be mistaken for the resolver's "unknown key".
    resolver = build_configured_resolver()
    try:
        src = resolver.resolve(key)
    except ValueError as exc:  # the resolver's reasons are written for the model (English)
        raise Missing("no_audio", reason=str(exc)) from exc

    # HTTP streams: redirect the browser to the upstream URL — the browser
    # opens the connection itself, no proxying needed.
    if src.is_stream:
        return RedirectResponse(src.uri, status_code=302)

    file_path = Path(src.uri)
    if not file_path.is_file():
        raise Missing("audio_file_gone")
    if file_path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise UnsupportedAudio("unsupported_audio_extension", ext=file_path.suffix.lower())

    file_size = file_path.stat().st_size
    range_header = request.headers.get("range") or request.headers.get("Range")

    # Map extension → MIME type for HTML5 <audio>
    mime_map = {
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
        ".flac": "audio/flac", ".m4a": "audio/mp4", ".opus": "audio/ogg",
        ".aac": "audio/aac", ".mp4": "audio/mp4", ".webm": "audio/webm",
    }
    media_type = mime_map.get(file_path.suffix.lower(), "application/octet-stream")

    chunk_size = 64 * 1024

    if range_header:
        # Parse "bytes=start-end" (RFC 7233, incl. suffix form "bytes=-N")
        try:
            unit, _, ranges = range_header.partition("=")
            if unit.strip().lower() != "bytes":
                raise ValueError("only bytes ranges supported")
            start_str, _, end_str = ranges.partition("-")
            if start_str:
                start = int(start_str)
                # Oversized end clamps to EOF per RFC — no 416 for that.
                end = min(int(end_str), file_size - 1) if end_str else file_size - 1
            elif end_str:
                # Suffix range "bytes=-N" = the LAST N bytes. Clients probe
                # file tails with this (m4a/mp4 moov lookup) — treating it
                # as start=0 served the wrong bytes with a valid-looking 206.
                suffix_len = int(end_str)
                if suffix_len <= 0:
                    raise ValueError("invalid suffix length")
                start = max(0, file_size - suffix_len)
                end = file_size - 1
            else:
                raise ValueError("empty range")
            if start >= file_size or start > end:
                raise RangeNotSatisfiable("range_not_satisfiable")
        except (ValueError, AttributeError) as error:
            raise BadRequest("invalid_range") from error

        length = end - start + 1

        async def iter_range():
            # File I/O via to_thread — a stalling medium (NFS, USB) must not
            # block the whole event loop.
            def _open_seeked():
                f = open(file_path, "rb")  # noqa: SIM115 — closed in the finally below, also in a thread
                f.seek(start)
                return f

            f = await asyncio.to_thread(_open_seeked)
            try:
                remaining = length
                while remaining > 0:
                    data = await asyncio.to_thread(f.read, min(chunk_size, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data
            finally:
                await asyncio.to_thread(f.close)

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "Cache-Control": "no-cache",
        }
        return StreamingResponse(iter_range(), status_code=206, media_type=media_type, headers=headers)

    # Full file response (with Accept-Ranges so the browser can seek later)
    async def iter_full():
        f = await asyncio.to_thread(open, file_path, "rb")
        try:
            while True:
                data = await asyncio.to_thread(f.read, chunk_size)
                if not data:
                    break
                yield data
        finally:
            await asyncio.to_thread(f.close)

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(file_size),
        "Cache-Control": "no-cache",
    }
    return StreamingResponse(iter_full(), media_type=media_type, headers=headers)


class AudioPositionRequest(BaseModel):
    """Browser → server: persist current playback position for resume."""
    state_key: str
    pos_sec: float
    duration_sec: float | None = None
    completed: bool = False


@audio_player_api.post("/audio/position", tags=["Audio"])
async def audio_position(req: AudioPositionRequest):
    """Update audio_state.json from the browser's currentTime."""
    from ..audio_state import audio_state
    if req.completed:
        audio_state.mark_completed(req.state_key)
        return {"status": "ok", "completed": True}
    # The URI is informational: a key whose source was removed meanwhile may
    # still report its position. Only that resolver verdict is tolerated; an
    # unreadable configuration propagates.
    resolver = build_configured_resolver()
    try:
        uri = resolver.resolve(req.state_key).uri
    except ValueError as exc:
        log_message(f"audio position for unresolvable key {req.state_key!r}: {exc}", "warning")
        uri = ""
    audio_state.update(
        key=req.state_key,
        uri=uri,
        pos_sec=float(req.pos_sec),
        duration_sec=float(req.duration_sec) if req.duration_sec else None,
    )
    return {"status": "ok"}
