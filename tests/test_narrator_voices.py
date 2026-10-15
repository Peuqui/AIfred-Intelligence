"""Narrator über den TTS-Dienst (Fake-Dienst): Engine/Stimme wählen, Stimmen listen, Datei vertonen."""

from __future__ import annotations

import asyncio
import io
import json
import wave
from types import SimpleNamespace

import pytest
from fake_tts_service import (
    FakeTTSService,
    audio_frame,
    catalog_row,
    error_frame,
    install_fake_service,
    keepalive_frame,
    summary_frame,
)

import aifred.lib.settings as settings_module
from aifred.lib import file_manager
from aifred.lib.plugin_base import PluginContext
from aifred.lib.tts_client import EngineRef, TTSSuiteUnreachable
from aifred.plugins.tools.narrator import _choose_engine_and_voice, plugin


def wav_bytes(seconds: float = 0.1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(bytes(int(16000 * seconds) * 2))
    return buffer.getvalue()


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Service with piper and edge (and a browser entry), AIfred's list: piper first. Narrator settings in ``saved``."""
    fake = FakeTTSService(
        [catalog_row("piper", needs_gpu=False), catalog_row("edge", needs_gpu=False, cloud=True),
         catalog_row("browser", needs_gpu=False, browser=True)],
        voices=[{"name": "Thorsten", "label": "Thorsten"}, {"name": "Ramona", "label": "Ramona"}],
    )
    install_fake_service(monkeypatch, tmp_path, fake, listed=["browser", "piper", "edge"])
    saved: dict = {}
    monkeypatch.setattr(settings_module, "load_settings", lambda: saved)
    return SimpleNamespace(fake=fake, saved=saved, tmp=tmp_path)


def choose(engine: str = "", voice: str = ""):
    return asyncio.run(_choose_engine_and_voice(engine, voice))


class TestChooseEngineAndVoice:
    def test_without_engine_the_topmost_active_entry_of_the_list_decides_but_never_the_browser(self, env):
        ref, voice, names = choose()
        assert ref == EngineRef("piper", "whole", None) and voice == "Thorsten" and names == ["Thorsten", "Ramona"]

    def test_the_service_gets_exactly_one_named_engine_with_the_narrator_unit(self, env):
        assert choose("edge")[0] == EngineRef("edge", "whole", None)

    def test_explicit_engine_uses_the_saved_voice_of_that_engine(self, env):
        env.saved["narrator_voices"] = {"edge": "Katja"}
        assert choose("edge")[1] == "Katja"

    def test_a_saved_voice_is_engine_bound(self, env):
        env.saved["narrator_voices"] = {"edge": "Katja"}
        assert choose("piper")[1] == "Thorsten"

    def test_explicit_voice_wins(self, env):
        env.saved["narrator_voices"] = {"edge": "Katja"}
        assert choose("edge", "Conrad")[1] == "Conrad"

    def test_the_browser_cannot_narrate(self, env):
        with pytest.raises(ValueError, match="browser"):
            choose("browser")

    def test_an_engine_the_service_does_not_offer_is_refused(self, env):
        with pytest.raises(ValueError, match="nope"):
            choose("nope")

    def test_an_empty_list_is_refused(self, env, monkeypatch):
        from aifred.lib import tts_service

        monkeypatch.setattr(tts_service, "persisted_settings", lambda: {"tts_escalation": [], "tts_toggles_per_engine": {}})
        with pytest.raises(ValueError, match="no active TTS entry"):
            choose()


def _tool(name: str):
    ctx = PluginContext(agent_id="aifred", lang="de", session_id="test")
    return {t.name: t for t in plugin.get_tools(ctx)}[name]


class TestListNarratorVoicesTool:
    def test_lists_the_voices_of_the_service_for_the_engine_the_list_picks(self, env):
        env.saved["narrator_voices"] = {"piper": "Ramona"}
        res = json.loads(asyncio.run(_tool("list_narrator_voices").executor()))
        assert res == {"engine": "piper", "default_voice": "Ramona", "voices": ["Thorsten", "Ramona"]}

    def test_an_engine_that_cannot_be_used_is_a_clear_error(self, env):
        res = json.loads(asyncio.run(_tool("list_narrator_voices").executor(engine="nope")))
        assert "error" in res and "nope" in res["error"]

    def test_a_service_that_does_not_answer_is_a_clear_error(self, env, monkeypatch):
        from aifred.lib import tts_client

        def down(self):
            raise TTSSuiteUnreachable("TTS service not reachable")

        monkeypatch.setattr(tts_client.TTSClient, "ensure_ready", down)
        res = json.loads(asyncio.run(_tool("list_narrator_voices").executor()))
        assert "not reachable" in res["error"]


class TestNarrateFile:
    @pytest.fixture
    def files(self, env, monkeypatch):
        """A workspace with one text file; the output lands in tmp."""
        source = {"text": "Absatz eins.\n\nAbsatz zwei."}
        monkeypatch.setattr(
            file_manager, "read_file",
            lambda name: SimpleNamespace(success=True, detail="", metadata={"content": source["text"]}),
        )
        monkeypatch.setattr(file_manager, "safe_resolve", lambda name: (env.tmp / "out" / name, None))
        monkeypatch.setattr("aifred.lib.config.NARRATE_CHUNK_LIMIT_CHARS", 15)   # one chunk per paragraph
        env.source = source
        return env

    def narrate(self, **args):
        return json.loads(asyncio.run(_tool("narrate_file").executor(filename="buch.txt", output_filename="buch.wav", **args)))

    def test_every_chunk_is_spoken_with_the_whole_list_and_the_pieces_become_one_file(self, files):
        files.fake.script = [audio_frame(wav_bytes(), "piper") + keepalive_frame("piper") + summary_frame()]
        result = self.narrate()
        assert result["written"] == "buch.wav" and result["chunks"] == 2
        assert result["engine"] == "piper" and result["voice"] == "Thorsten"
        assert [s["text"] for s in files.fake.speaks] == ["Absatz eins.", "Absatz zwei."]
        for sent in files.fake.speaks:
            assert sent["engines"] == [{"engine": "piper", "unit": "whole", "host": None},
                                       {"engine": "edge", "unit": "whole", "host": None}]
            assert sent["voices"] == {"piper": {"voice": "Thorsten"}, "edge": {"voice": "Thorsten"}}
        assert (files.tmp / "out" / "buch.wav").stat().st_size > 44

    def test_an_explicit_engine_is_pinned_without_the_rest_of_the_list(self, files):
        files.fake.script = [audio_frame(wav_bytes(), "edge") + summary_frame()]
        assert self.narrate(engine="edge")["engine"] == "edge"
        for sent in files.fake.speaks:
            assert sent["engines"] == [{"engine": "edge", "unit": "whole", "host": None}]

    def test_speaker_voices_pick_the_voice_per_chunk_and_unknown_voices_are_refused(self, files):
        files.source["text"] = "[A]: Hallo du.\n[B]: Hallo ich."
        files.fake.script = [audio_frame(wav_bytes(), "piper") + summary_frame()]
        assert "Unknown voice" in self.narrate(speaker_voices={"A": "Thorsten", "B": "Gibtsnicht"})["error"]
        self.narrate(speaker_voices={"A": "Thorsten", "B": "Ramona"})
        assert [s["voices"]["piper"]["voice"] for s in files.fake.speaks] == ["Thorsten", "Ramona"]

    def test_an_error_of_the_service_names_the_chunk_and_what_was_done(self, files):
        files.fake.script = [audio_frame(wav_bytes(), "piper") + summary_frame(), 503]
        result = self.narrate()
        assert result["chunks_done"] == 1 and "chunk 2/2" in result["error"]

    def test_an_error_frame_in_the_middle_of_a_chunk_is_reported_too(self, files):
        files.fake.script = [audio_frame(wav_bytes(), "piper") + error_frame("engine_control_failed", "died")]
        result = self.narrate()
        assert "died" in result["error"] and result["chunks_done"] == 0

    def test_a_reading_without_audio_is_an_error(self, files):
        files.fake.script = [summary_frame()]
        assert "no audio" in self.narrate()["error"]
