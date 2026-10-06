"""Sprach-Weiche: Routentabelle, Weitergabe an den externen Dienst, Dokumentation in der Sitzung."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from aiohttp import web

import aifred.lib.message_processor as mp
import aifred.plugins.channels.freeecho2_channel.pipeline as pipeline
from aifred.plugins.channels.freeecho2_channel import FreeEchoChannel
from aifred.plugins.channels.freeecho2_channel.voice_routes import forward_voice, load_voice_routes


def run(coro):
    return asyncio.run(coro)


# ── Routentabelle ────────────────────────────────────────────────────

def test_missing_file_means_no_routes(tmp_path: Path):
    assert load_voice_routes(tmp_path / "nope.json") == {}


def test_valid_table_is_loaded(tmp_path: Path):
    table = {"orc": {"url": "http://x/api/voice", "token_file": "/t", "timeout_seconds": 5}}
    path = tmp_path / "routes.json"
    path.write_text(json.dumps(table))
    assert load_voice_routes(path) == table


def test_incomplete_route_is_refused_loudly(tmp_path: Path):
    path = tmp_path / "routes.json"
    path.write_text(json.dumps({"orc": {"url": "http://x"}}))
    with pytest.raises(ValueError, match="token_file"):
        load_voice_routes(path)


# ── Weitergabe (echter HTTP-Server als Gegenstelle) ─────────────────

def _serve(handler) -> tuple[web.AppRunner, int]:
    async def start():
        app = web.Application()
        app.router.add_post("/api/voice", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        return runner, site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    return run_with_loop(start())


_LOOP = asyncio.new_event_loop()


def run_with_loop(coro):
    return _LOOP.run_until_complete(coro)


@pytest.fixture
def orc_endpoint(tmp_path: Path):
    seen: dict = {}

    async def handler(request: web.Request) -> web.Response:
        seen["auth"] = request.headers.get("Authorization")
        form = await request.post()
        seen["fields"] = {k: v for k, v in form.items() if k != "audio"}
        seen["audio_type"] = form["audio"].content_type
        seen["body"] = form["audio"].file.read()
        if seen["auth"] != "Bearer geheim":
            return web.json_response({"detail": "no"}, status=401)
        return web.json_response({"action": "asked", "agent": "Whisper"})

    runner, port = _serve(handler)
    token = tmp_path / "voice-token"
    token.write_text("geheim\n")
    route = {"url": f"http://127.0.0.1:{port}/api/voice", "token_file": str(token), "timeout_seconds": 5}
    yield route, seen, token
    run_with_loop(runner.cleanup())


@pytest.fixture
def recording(tmp_path: Path) -> str:
    path = tmp_path / "aufnahme.wav"
    path.write_bytes(b"RIFF....WAVEdata")
    return str(path)


def test_the_utterance_goes_to_the_route_with_the_bearer_token(orc_endpoint, recording):
    route, seen, _ = orc_endpoint
    outcome = run_with_loop(forward_voice(route, "Buero-Puck-2", "Whisper committe bitte äöü", recording))
    assert outcome == {"action": "asked", "agent": "Whisper"}
    assert seen["auth"] == "Bearer geheim"
    assert seen["fields"] == {"room": "Buero-Puck-2", "text": "Whisper committe bitte äöü"}
    assert seen["audio_type"] == "audio/wav"
    assert seen["body"] == b"RIFF....WAVEdata"       # the recording, unchanged


def test_a_wrong_token_is_an_error(orc_endpoint, recording):
    route, _, token = orc_endpoint
    token.write_text("falsch")
    with pytest.raises(httpx.HTTPStatusError):
        run_with_loop(forward_voice(route, "buero", "x", recording))


def test_an_empty_token_file_is_an_error(orc_endpoint, recording):
    route, _, token = orc_endpoint
    token.write_text("  \n")
    with pytest.raises(ValueError, match="empty"):
        run_with_loop(forward_voice(route, "buero", "x", recording))


def test_an_unreachable_route_is_an_error(orc_endpoint, recording):
    route, _, _ = orc_endpoint
    route = {**route, "url": "http://127.0.0.1:1/api/voice"}
    with pytest.raises(httpx.HTTPError):
        run_with_loop(forward_voice(route, "buero", "x", recording))


# ── Die Weiche im Kanal ──────────────────────────────────────────────

ROUTE = {"url": "http://x/api/voice", "token_file": "/t", "timeout_seconds": 5}


@pytest.fixture
def routed(monkeypatch):
    recorded: list[dict] = []
    monkeypatch.setattr(mp, "record_routed_voice_turn", lambda *a, **k: recorded.append(k))
    channel = FreeEchoChannel()
    channel.send_done = AsyncMock(return_value=True)  # type: ignore[method-assign]
    return channel, recorded


def test_success_closes_the_round_and_documents_it(routed, monkeypatch):
    channel, recorded = routed
    monkeypatch.setattr(pipeline, "forward_voice", AsyncMock(return_value={"action": "asked", "agent": "Whisper"}))
    run(channel._route_voice("buero", "orc", ROUTE, "Whisper, committe bitte", "/tmp/a.wav"))
    channel.send_done.assert_awaited_once_with("buero", reason="routed_to_orc")
    assert recorded == [{
        "sender": "buero", "text": "Whisper, committe bitte", "route_name": "orc",
        "outcome": "asked (Whisper)",
    }]


@pytest.mark.parametrize("error", [
    httpx.ConnectError("refused"), httpx.HTTPStatusError("502", request=None, response=None), ValueError("empty"),
])
def test_failure_is_loud_closes_with_route_failed_and_does_not_fall_back(routed, monkeypatch, error):
    channel, recorded = routed
    monkeypatch.setattr(pipeline, "forward_voice", AsyncMock(side_effect=error))
    run(channel._route_voice("buero", "orc", ROUTE, "hallo", "/tmp/a.wav"))
    channel.send_done.assert_awaited_once_with("buero", reason="route_failed")
    assert recorded[0]["outcome"].startswith("failed (")


# ── Dokumentation in der Sitzung (Chat- UND LLM-History) ─────────────

class _FakeRoutingTable:
    def get_route(self, channel, channel_id):
        return None

    def set_route(self, channel, channel_id, session_id):
        return None


@pytest.fixture
def session_store(monkeypatch):
    store: dict = {"chat_history": [], "llm_history": []}

    def fake_update(session_id, chat_history, *args, llm_history=None, **kwargs):
        store["chat_history"] = chat_history
        if llm_history is not None:
            store["llm_history"] = llm_history

    monkeypatch.setattr(mp, "routing_table", _FakeRoutingTable())
    monkeypatch.setattr(mp, "create_empty_session", lambda *a, **k: None)
    monkeypatch.setattr(mp, "write_hub_notification", lambda *a, **k: None)
    monkeypatch.setattr(mp, "update_chat_data", fake_update)
    import aifred.lib.session_storage as ss
    monkeypatch.setattr(ss, "load_session", lambda sid: {"data": store})
    return store


def test_the_routed_exchange_lands_in_chat_and_llm_history(session_store):
    mp.record_routed_voice_turn(
        "freeecho2", "Buero-Puck-2", sender="Buero-Puck-2",
        text="Whisper, committe bitte", route_name="orc", outcome="asked (Whisper)",
    )
    chat = session_store["chat_history"]
    # chat: who said it (user via the puck), where it went, then what came back
    assert chat[0]["role"] == "user"
    assert "Buero-Puck-2" in chat[0]["content"] and "→ orc" in chat[0]["content"]
    assert "Whisper, committe bitte" in chat[0]["content"]
    assert chat[1]["role"] == "assistant" and "orc: asked (Whisper)" in chat[1]["content"]

    llm = session_store["llm_history"]
    assert llm[0]["role"] == "user"
    assert "<external_message" in llm[0]["content"] and "Whisper, committe bitte" in llm[0]["content"]
    assert llm[1]["role"] == "assistant" and "orc: asked (Whisper)" in llm[1]["content"]


def test_a_second_exchange_is_appended_not_replaced(session_store):
    for text in ("erstens", "zweitens"):
        mp.record_routed_voice_turn(
            "freeecho2", "buero", sender="buero", text=text, route_name="orc", outcome="sent (Hal)",
        )
    assert len(session_store["chat_history"]) == 4 and len(session_store["llm_history"]) == 4
