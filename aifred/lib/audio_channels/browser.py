"""BrowserChannel — HTML5 audio in the session's browser tabs (frontend/src/lib/media.ts).

No PCM streaming: the browser loads the file itself by HTTP range from ``/api/audio/file?key=<state_key>``. The
channel pushes what to play and how (``media``, ``pause``, ``resume``, ``stop``, ``seek``, ``speed``) into the
session's event stream; every open tab of the session follows. What plays in a session (started, paused) the
channel keeps itself (``_playing``): the agents' tools and the player's buttons in the page ask it.

``target_id``: ``"browser:<session_id>"`` or ``"browser"`` (the session of ``ctx``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .base import AudioFormat, AudioOutputChannel, TargetInfo

if TYPE_CHECKING:
    from ..audio_sources import ResolvedSource
    from ..plugin_base import PluginContext

logger = logging.getLogger(__name__)

# Speeds an HTML5 audio element plays (playbackRate).
SPEED_RANGE = (0.25, 4.0)


@dataclass
class _Playing:
    """What the channel started in a session: the first track (a folder's further ones the page plays on)."""

    label: str
    state_key: str
    is_stream: bool
    queue_length: int
    paused: bool = False


# Session id → what plays there.
_playing: dict[str, _Playing] = {}


def _session_of(target_id: str, ctx: PluginContext | None) -> str:
    """The session a target names: ``browser:<id>``, else the session of the call."""
    if target_id.startswith("browser:"):
        return target_id.split(":", 1)[1]
    return getattr(ctx, "session_id", "") if ctx else ""


def audio_url(state_key: str) -> str:
    """Where the page loads a source from (the REST endpoint, not the file's path)."""
    return f"/api/audio/file?key={quote(state_key)}"


def ended(session_id: str) -> None:
    """The page played the last track to its end: nothing plays in the session any more."""
    _playing.pop(session_id, None)


class BrowserChannel(AudioOutputChannel):
    """HTML5 audio in the session's tabs, steered over its event stream."""

    name = "browser"
    # The browser decodes itself: no input format required
    required_format = AudioFormat()

    def can_handle(self, target_id: str) -> bool:
        return target_id == "browser" or target_id.startswith("browser:")

    def list_targets(self, ctx: PluginContext) -> list[TargetInfo]:
        """The session of the call: every open tab of it plays."""
        from ..i18n import t

        session_id = getattr(ctx, "session_id", "")
        if not session_id:
            return []
        return [TargetInfo(id=f"browser:{session_id}", label=t("audio_target_browser", lang=ctx.lang), ready=True)]

    async def play(
        self,
        src: ResolvedSource,
        target_id: str,
        start_pos_sec: float | None,
        ctx: PluginContext,
    ) -> dict[str, Any]:
        from ..browser_push import browser_push

        url = audio_url(src.state_key)
        # Streams have no meaningful position
        seek_to = float(start_pos_sec) if not src.is_stream and start_pos_sec else 0.0
        session_id = _session_of(target_id, ctx)
        if session_id:
            _playing[session_id] = _Playing(src.label, src.state_key, bool(src.is_stream), 1)
            browser_push(
                session_id, "media", url,
                state_key=src.state_key,
                start_pos_sec=seek_to,
                is_stream=bool(src.is_stream),
                audio_type=getattr(src, "audio_type", "music"),
            )
        return {
            "success": bool(session_id),
            "label": src.label,
            "item": src.item,
            "state_key": src.state_key,
            "is_stream": src.is_stream,
            "target": target_id,
            "audio_url": url,
            "resumed_at_sec": seek_to,
        }

    async def pause(self, target_id: str, ctx: PluginContext | None = None) -> bool:
        return self._push_to_playing(target_id, ctx, "pause", paused=True)

    async def resume(self, target_id: str, ctx: PluginContext | None = None) -> bool:
        return self._push_to_playing(target_id, ctx, "resume", paused=False)

    async def stop(self, target_id: str, ctx: PluginContext | None = None) -> bool:
        from ..browser_push import browser_push

        session_id = _session_of(target_id, ctx)
        if _playing.pop(session_id, None) is None:
            return False
        browser_push(session_id, "stop", "")
        return True

    async def seek(
        self,
        target_id: str,
        position_sec: float,
        relative: bool = False,
        ctx: PluginContext | None = None,
    ) -> bool:
        # The page sets currentTime (relative: added to it) and keeps it within the track.
        return self._push_to_playing(target_id, ctx, "seek", position_sec=float(position_sec), relative=bool(relative))

    async def set_speed(
        self, target_id: str, factor: float, ctx: PluginContext | None = None
    ) -> bool:
        if not SPEED_RANGE[0] <= factor <= SPEED_RANGE[1]:
            return False
        # HTML5 audio changes playbackRate without changing the pitch.
        return self._push_to_playing(target_id, ctx, "speed", factor=float(factor))

    def _push_to_playing(self, target_id: str, ctx: PluginContext | None, kind: str, *,
                         paused: bool | None = None, **details: Any) -> bool:
        """``kind`` to the session's tabs, when something plays there; False otherwise."""
        from ..browser_push import browser_push

        session_id = _session_of(target_id, ctx)
        playing = _playing.get(session_id)
        if playing is None:
            return False
        if paused is not None:
            playing.paused = paused
        browser_push(session_id, kind, "", **details)
        return True

    async def play_queue(
        self,
        items: list[dict[str, str]],
        target_id: str,
        ctx: PluginContext,
        audio_type: str = "music",
        shuffle: bool = False,
    ) -> dict[str, Any]:
        """A folder: the first track goes out with the ones after it; the page plays on by itself at each end, no
        round trip per track. ``items`` carry the source's ``state_key`` (the page loads them from the REST
        endpoint, not from their path)."""
        import random

        from ..browser_push import browser_push

        if not items:
            return {"success": False, "error": "empty queue"}
        ordered = list(items)
        if shuffle:
            random.shuffle(ordered)
        first_key = ordered[0]["state_key"]
        session_id = _session_of(target_id, ctx)
        if session_id:
            _playing[session_id] = _Playing(ordered[0].get("label", first_key), first_key, False, len(ordered))
            browser_push(
                session_id, "media", audio_url(first_key),
                state_key=first_key,
                start_pos_sec=0.0,
                is_stream=False,
                audio_type=audio_type,
                queue=[{"audio_url": audio_url(it["state_key"]), "state_key": it["state_key"]} for it in ordered[1:]],
            )
        return {
            "success": bool(session_id),
            "target": target_id,
            "queued_count": len(ordered),
            "shuffle": shuffle,
            "first": {"state_key": first_key, "uri": audio_url(first_key)},
        }

    async def status(self, target_id: str, ctx: PluginContext | None = None) -> dict[str, Any]:
        playing = _playing.get(_session_of(target_id, ctx))
        if playing is None:
            return {"running": False, "playing": False, "paused": False}
        return {
            "running": True,
            "playing": not playing.paused,
            "paused": playing.paused,
            "label": playing.label,
            "state_key": playing.state_key,
            "is_stream": playing.is_stream,
            "queue_length": playing.queue_length,
        }
