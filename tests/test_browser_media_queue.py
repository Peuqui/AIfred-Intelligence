"""The browser channel of the audio player (lib/audio_channels/browser.py, lib/browser_push.py): it sends a folder's
tracks with the "media" event, and it knows itself what plays in a session. 08.11.2026: the tracks after the first
and what played lived only in the Reflex state; on the Vue path a folder played its first track and stopped, and
pause, resume, stop, seek and status never found anything playing. The event also carries how the page plays: the
pre-roll after the spoken reply and how often it saves the position (config.py, before fixed in custom.js)."""

import asyncio

import pytest

from aifred.lib import session_events
from aifred.lib.audio_channels import browser
from aifred.lib.audio_channels.browser import BrowserChannel
from aifred.lib.audio_sources import ResolvedSource
from aifred.lib.config import AUDIO_POSITION_SAVE_SEC, AUDIO_RESUME_PRE_ROLL_SEC
from aifred.lib.plugin_base import PluginContext


@pytest.fixture
def pushed(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(session_events, "publish", lambda session_id, kind, data, replaces=frozenset(): events.append((kind, data)))
    monkeypatch.setattr(browser, "_playing", {})
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


def test_what_plays_is_paused_resumed_and_stopped_and_the_status_says_so(pushed: list[tuple[str, dict]]) -> None:
    channel, context = BrowserChannel(), PluginContext(agent_id="aifred", lang="de", session_id="s1")
    track = ResolvedSource(uri="/m/x.mp3", state_key="Musik/x.mp3", is_stream=False, label="Musik", item="x.mp3")

    async def scenario() -> list[object]:
        seen: list[object] = [await channel.pause("browser:s1", context)]          # nothing plays yet
        await channel.play(track, "browser:s1", None, context)
        seen += [await channel.pause("browser", context), (await channel.status("browser:s1"))["paused"]]
        seen += [await channel.resume("browser:s1"), (await channel.status("browser:s1"))["playing"]]
        seen += [await channel.seek("browser:s1", 30.0, ctx=context), await channel.set_speed("browser:s1", 9.0)]
        seen += [await channel.stop("browser:s1"), await channel.stop("browser:s1"), (await channel.status("browser:s1"))["running"]]
        return seen

    assert asyncio.run(scenario()) == [False, True, True, True, True, True, False, True, False, False]
    assert [kind for kind, _ in pushed] == ["media", "pause", "resume", "seek", "stop"]


def test_the_end_of_the_last_track_leaves_nothing_playing(pushed: list[tuple[str, dict]]) -> None:
    channel, context = BrowserChannel(), PluginContext(agent_id="aifred", lang="de", session_id="s1")
    asyncio.run(channel.play_queue([{"state_key": "a.mp3", "uri": "/a"}, {"state_key": "b.mp3", "uri": "/b"}], "browser:s1", context))
    assert asyncio.run(channel.status("browser:s1"))["queue_length"] == 2
    browser.ended("s1")
    assert asyncio.run(channel.status("browser:s1"))["running"] is False


def test_the_players_buttons_steer_what_the_session_plays(pushed: list[tuple[str, dict]], tmp_path, monkeypatch) -> None:
    from starlette.testclient import TestClient

    from aifred.lib import session_storage
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import USERNAME_COOKIE_NAME, sign_username

    session = "f" * 32
    monkeypatch.setattr(session_storage, "SESSION_DIR", tmp_path)
    session_storage._session_meta_cache.clear()
    session_storage.create_empty_session(session, owner="ki")
    client = TestClient(api_app)
    client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
    assert client.post(f"/sessions/{session}/media", json={"action": "pause"}).json() == {"done": False}   # nothing plays
    track = ResolvedSource(uri="/m/x.mp3", state_key="Musik/x.mp3", is_stream=False, label="Musik", item="x.mp3")
    asyncio.run(BrowserChannel().play(track, f"browser:{session}", None, PluginContext(agent_id="aifred", lang="de", session_id=session)))
    assert client.post(f"/sessions/{session}/media", json={"action": "pause"}).json() == {"done": True}
    assert asyncio.run(BrowserChannel().status(f"browser:{session}"))["paused"] is True    # what an agent's tool reads
    assert client.post(f"/sessions/{session}/media", json={"action": "seek", "position_sec": 12}).json() == {"done": True}
    assert client.post(f"/sessions/{session}/media", json={"action": "ended"}).json() == {"done": True}
    assert client.post(f"/sessions/{session}/media", json={"action": "stop"}).json() == {"done": False}
    assert [kind for kind, _ in pushed] == ["media", "pause", "seek"]
    assert client.post(f"/sessions/{'a' * 32}/media", json={"action": "stop"}).status_code == 404
    session_storage._session_meta_cache.clear()
