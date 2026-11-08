"""What the server pushes to the open tabs of a session besides the turn's own events: the speech output and the
music of the browser's audio channel, the service status. Every event goes through the session event hub
(``lib/session_events.py``: one id counter per session, one queue per listening tab, the replay buffer for
reconnects); the tabs read it from the session's stream (``GET /api/sessions/{id}/events``).
"""

from typing import Any

from . import session_events

TTS_CHUNK_KINDS = frozenset({"tts", "tts_speak", "tts_pause"})
# Kinds that carry a state, not an event: the newest replaces its predecessors in the
# replay buffer (a page that reconnects needs the latest, not the history).
LATEST_WINS_KINDS = frozenset({"service_status"})


def browser_push(
    session_id: str,
    kind: str,
    url: str,
    *,
    playback_rate: str = "1.0x",
    state_key: str = "",
    start_pos_sec: float = 0.0,
    is_stream: bool = False,
    audio_type: str = "music",
    position_sec: float = 0.0,
    relative: bool = False,
    factor: float = 1.0,
    queue: list[dict[str, str]] | None = None,
) -> None:
    """Push an event to the open tabs of the session.

    The ``url`` field is the generic payload slot: for audio kinds it is an
    audio URL, for ``service_status`` it carries the snapshot.

    Kinds and their metadata:
      - ``"tts"``    : chunk-stream, gapless. {url, playback_rate}
      - ``"tts_speak"`` : a part the browser's own voice (Web Speech API) speaks, same
                       queue; ``url`` holds the JSON of the utterance.
      - ``"tts_pause"`` : silence between two parts of the TTS queue; ``url`` holds the
                       pause in milliseconds.
      - ``"media"``  : single-track, position-saved. {url, state_key,
                       start_pos_sec, is_stream, audio_type, playback_rate, queue (the
                       folder's tracks {audio_url, state_key} after this one, [] for one
                       track), pre_roll_sec, position_save_sec}
      - ``"stop"``   : halt + clear src + final-position-save. (no metadata)
      - ``"pause"``  : halt, keep src + position. (no metadata)
      - ``"resume"`` : continue from current position. (no metadata)
      - ``"seek"``   : jump to ``position_sec`` (or ±N sec when ``relative=True``).
      - ``"speed"``  : set ``audio.playbackRate`` to ``factor`` (0.25–4.0).
      - ``"bubble_audio"`` : streaming-TTS finalize announces the combined
                       replay URL for the speaker button of the latest bubble. {url}
      - ``"bubble_tts_note"`` : follows ``bubble_audio`` — ``url`` holds who
                       spoke (escalation list). {url}
      - ``"service_status"`` : model / STT / TTS state for the status line; ``url``
                       holds the snapshot as JSON, the newest replaces the older ones.
    """
    data: dict[str, Any] = {"url": url, "playback_rate": playback_rate}
    if kind == "media":
        from .config import AUDIO_POSITION_SAVE_SEC, AUDIO_RESUME_PRE_ROLL_SEC

        data.update(
            state_key=state_key,
            start_pos_sec=float(start_pos_sec),
            is_stream=bool(is_stream),
            audio_type=audio_type,
            queue=list(queue or []),
            pre_roll_sec=AUDIO_RESUME_PRE_ROLL_SEC,
            position_save_sec=AUDIO_POSITION_SAVE_SEC,
        )
    elif kind == "seek":
        data.update(position_sec=float(position_sec), relative=bool(relative))
    elif kind == "speed":
        data["factor"] = float(factor)
    # The combined replay file replaces the reply's chunks (their files are deleted at
    # finalize): a page opened later must not get them replayed into its queue.
    replaces = TTS_CHUNK_KINDS if kind == "bubble_audio" else frozenset()
    if kind in LATEST_WINS_KINDS:
        replaces = frozenset({kind})
    session_events.publish(session_id, kind, data, replaces=replaces)
