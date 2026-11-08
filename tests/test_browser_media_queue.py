"""The browser channel of the audio player sends a folder's tracks with the "media" event (lib/audio_channels/browser.py,
lib/browser_push.py). 08.11.2026: the tracks after the first lived only in the Reflex state; the Vue page has none, so
it played the first track of a folder and stopped. The event also carries how the page plays: the pre-roll after the
spoken reply and how often it saves the position (config.py, before fixed in custom.js)."""

import asyncio

import pytest

from aifred.lib import session_events
from aifred.lib.audio_channels.browser import BrowserChannel
from aifred.lib.audio_sources import ResolvedSource
from aifred.lib.config import AUDIO_POSITION_SAVE_SEC, AUDIO_RESUME_PRE_ROLL_SEC
from aifred.lib.plugin_base import PluginContext


@pytest.fixture
def pushed(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(session_events, "publish", lambda session_id, kind, data, replaces=frozenset(): events.append((kind, data)))
    return events


def test_a_folder_sends_the_tracks_after_the_first_with_the_event(pushed: list[tuple[str, dict]]) -> None:
    context = PluginContext(agent_id="aifred", lang="de", session_id="s1")   # the Vue path: no Reflex state
    items = [{"state_key": f"Musik/{name}.mp3", "uri": f"/m/{name}.mp3"} for name in ("a", "b", "c")]
    result = asyncio.run(BrowserChannel().play_queue(items, "browser:s1", context))
    assert result["queued_count"] == 3
    [(kind, data)] = pushed
    assert (kind, data["url"], data["state_key"]) == ("media", "/api/audio/file?key=Musik/a.mp3", "Musik/a.mp3")
    assert data["queue"] == [{"audio_url": "/api/audio/file?key=Musik/b.mp3", "state_key": "Musik/b.mp3"},
                             {"audio_url": "/api/audio/file?key=Musik/c.mp3", "state_key": "Musik/c.mp3"}]
    assert (data["pre_roll_sec"], data["position_save_sec"]) == (AUDIO_RESUME_PRE_ROLL_SEC, AUDIO_POSITION_SAVE_SEC)


def test_one_track_has_no_queue(pushed: list[tuple[str, dict]]) -> None:
    context = PluginContext(agent_id="aifred", lang="de", session_id="s1")
    track = ResolvedSource(uri="/m/x.mp3", state_key="Hörbuch/x.mp3", is_stream=False, label="Hörbuch", item="x.mp3")
    asyncio.run(BrowserChannel().play(track, "browser:s1", 42.0, context))
    [(kind, data)] = pushed
    assert (kind, data["start_pos_sec"], data["queue"]) == ("media", 42.0, [])
