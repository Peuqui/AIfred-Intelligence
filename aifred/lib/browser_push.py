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


def browser_push(session_id: str, kind: str, **fields: Any) -> None:
    """Push ``kind`` with its own ``fields`` to the open tabs of the session (the page reads them in
    stores/chat.ts, lib/speech.ts, lib/media.ts).

    Kinds and their fields:
      - ``"tts"``         : an audio file of the reading. {url}
      - ``"tts_speak"``   : a part the browser's own voice speaks. {utterance: {text, language, voice, rate, pitch}}
      - ``"tts_pause"``   : silence between two parts of the reading. {ms}
      - ``"media"``       : one track, its position saved. {url, state_key, start_pos_sec, is_stream, audio_type,
                            queue (the folder's tracks {audio_url, state_key} after this one), pre_roll_sec,
                            position_save_sec}
      - ``"stop"``        : halt, clear the source, save the final position. {}
      - ``"pause"`` / ``"resume"`` : halt keeping source and position / play on. {}
      - ``"seek"``        : jump to ``position_sec`` (``relative``: by it). {position_sec, relative}
      - ``"speed"``       : the player's playbackRate (0.25–4.0). {factor}
      - ``"service_status"`` : model, STT and TTS state for the status line (``ServiceSnapshot.as_json``); the newest
                            replaces the older ones.
    """
    replaces = frozenset({kind}) if kind in LATEST_WINS_KINDS else frozenset()
    session_events.publish(session_id, kind, fields, replaces=replaces)
