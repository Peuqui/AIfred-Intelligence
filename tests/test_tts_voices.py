"""Stimmen der Agenten je Engine: Auflösung (Einstellung, Agent, aifred) und das Dict für SpeakRequest.voices."""

import pytest

import aifred.lib.agent_config as agent_config
import aifred.lib.settings as settings_module
from aifred.lib.tts_client import EngineRef
from aifred.lib.tts_voices import (
    NoVoiceConfigured,
    configured_voice,
    parse_speed_factor,
    resolve_voice,
    voices_for,
)


@pytest.fixture
def voices(monkeypatch):
    """User settings and the defaults of ``agents.json`` set per test."""
    state = {"user": {}, "defaults": {}}
    monkeypatch.setattr(
        settings_module, "persisted_settings", lambda: {"tts_agent_voices_per_engine": state["user"]},
    )
    monkeypatch.setattr(
        agent_config, "get_tts_voice_default", lambda agent, engine: state["defaults"].get((agent, engine), {}),
    )
    return state


class TestResolveVoice:
    def test_the_users_setting_for_agent_and_engine_wins(self, voices):
        voices["user"] = {"piper": {"sokrates": {"voice": "Florian", "speed": "1.25x"}}}
        voices["defaults"] = {("sokrates", "piper"): {"voice": "Standard"}}
        assert resolve_voice("piper", "sokrates") == ("Florian", 1.25, 1.0)

    def test_the_agents_default_when_the_user_set_nothing(self, voices):
        voices["defaults"] = {("codine", "edge"): {"voice": "Conrad", "pitch": "0.9"}}
        assert resolve_voice("edge", "codine") == ("Conrad", 1.0, 0.9)

    def test_the_user_setting_of_aifred_stands_in_for_an_agent_without_one(self, voices):
        voices["user"] = {"edge": {"aifred": {"voice": "Katja"}}}
        assert resolve_voice("edge", "salomo") == ("Katja", 1.0, 1.0)

    def test_the_default_of_aifred_stands_in_for_an_agent_without_one(self, voices):
        voices["defaults"] = {("aifred", "edge"): {"voice": "Conrad"}}
        assert resolve_voice("edge", "salomo")[0] == "Conrad"

    def test_no_voice_at_all_is_an_error_and_no_default_of_aifred(self, voices):
        with pytest.raises(NoVoiceConfigured, match="no voice configured"):
            resolve_voice("xtts", "aifred")
        assert configured_voice("xtts", "aifred")[0] == ""

    def test_an_invalid_speed_is_a_configuration_error(self, voices):
        voices["user"] = {"edge": {"aifred": {"voice": "Katja", "speed": "schnell"}}}
        with pytest.raises(ValueError, match="invalid speed/pitch"):
            resolve_voice("edge", "aifred")


class TestVoicesFor:
    LIST = [EngineRef("xtts", "sentence", "Aragon"), EngineRef("edge", "sentence", None), EngineRef("piper", "whole", None)]

    def test_voice_speed_and_pitch_per_engine_of_the_list(self, voices):
        voices["user"] = {"xtts": {"aifred": {"voice": "AIfred", "speed": "1.1"}}, "edge": {"aifred": {"voice": "Katja"}}}
        assert voices_for(self.LIST, "aifred") == {
            "xtts": {"voice": "AIfred", "speed": 1.1, "pitch": 1.0},
            "edge": {"voice": "Katja", "speed": 1.0, "pitch": 1.0},
        }

    def test_an_engine_without_a_voice_stays_out(self, voices):
        voices["user"] = {"edge": {"aifred": {"voice": "Katja"}}}
        assert list(voices_for(self.LIST, "aifred")) == ["edge"]

    def test_the_agent_decides(self, voices):
        voices["user"] = {"edge": {"aifred": {"voice": "Katja"}, "sokrates": {"voice": "Conrad"}}}
        assert voices_for(self.LIST, "sokrates")["edge"]["voice"] == "Conrad"

    def test_an_engine_on_two_hosts_is_listed_once(self, voices):
        voices["user"] = {"xtts": {"aifred": {"voice": "AIfred"}}}
        both = [EngineRef("xtts", "sentence", None), EngineRef("xtts", "paragraph", "Aragon")]
        assert list(voices_for(both, "aifred")) == ["xtts"]


def test_speed_parser():
    assert [parse_speed_factor(x) for x in ("1.25x", "1.25", 1.25, None, "", "x?")] == [1.25, 1.25, 1.25, None, None, None]
