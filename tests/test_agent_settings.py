"""Tests for aifred.lib.agent_settings — the per-agent attr-addressing SSOT."""

import pytest

from aifred.lib.agent_settings import (
    CANONICAL_AGENTS,
    agent_attr,
    get_agent_base_model_id,
    get_agent_setting,
    set_agent_setting,
    settings_agent,
)


class _FakeState:
    """Bare attr container standing in for AIState."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


# ── settings_agent ────────────────────────────────────────────────


def test_canonical_agents_own_their_bucket():
    for agent in CANONICAL_AGENTS:
        assert settings_agent(agent) == agent


def test_registered_custom_agent_maps_to_aifred(monkeypatch):
    from aifred.lib import agent_config
    monkeypatch.setattr(
        agent_config, "get_agent_config", lambda aid: object() if aid == "codine" else None
    )
    assert settings_agent("codine") == "aifred"


def test_unregistered_agent_raises(monkeypatch):
    from aifred.lib import agent_config
    monkeypatch.setattr(agent_config, "get_agent_config", lambda aid: None)
    with pytest.raises(ValueError, match="Unknown agent"):
        settings_agent("tippfehler")


# ── agent_attr: regular scheme + asymmetries ─────────────────────


def test_regular_field_naming():
    assert agent_attr("sokrates", "top_k") == "sokrates_top_k"
    assert agent_attr("vision", "speed_mode") == "vision_speed_mode"
    assert agent_attr("aifred", "model_id") == "aifred_model_id"


def test_aifred_temperature_is_global():
    assert agent_attr("aifred", "temperature") == "temperature"
    assert agent_attr("salomo", "temperature") == "salomo_temperature"


def test_vision_manual_ctx_asymmetry():
    assert agent_attr("vision", "num_ctx_manual") == "vision_num_ctx"
    assert agent_attr("vision", "num_ctx_manual_enabled") == "vision_num_ctx_enabled"
    assert agent_attr("aifred", "num_ctx_manual") == "num_ctx_manual_aifred"
    assert agent_attr("salomo", "num_ctx_manual_enabled") == "num_ctx_manual_salomo_enabled"


def test_custom_agent_resolves_to_aifred_attrs(monkeypatch):
    from aifred.lib import agent_config
    monkeypatch.setattr(
        agent_config, "get_agent_config", lambda aid: object() if aid == "codine" else None
    )
    assert agent_attr("codine", "top_k") == "aifred_top_k"
    assert agent_attr("codine", "temperature") == "temperature"


# ── get/set ──────────────────────────────────────────────────────


def test_get_agent_setting_reads_mapped_attr():
    state = _FakeState(sokrates_top_k=42, temperature=0.7)
    assert get_agent_setting(state, "sokrates", "top_k") == 42
    assert get_agent_setting(state, "aifred", "temperature") == 0.7


def test_get_agent_setting_missing_raises_without_default():
    state = _FakeState()
    with pytest.raises(AttributeError):
        get_agent_setting(state, "salomo", "top_k")


def test_get_agent_setting_default():
    state = _FakeState()
    assert get_agent_setting(state, "salomo", "top_k", 40) == 40


def test_set_agent_setting_writes_mapped_attr():
    state = _FakeState(temperature=0.5)
    set_agent_setting(state, "aifred", "temperature", 0.9)
    assert state.temperature == 0.9
    set_agent_setting(state, "vision", "num_ctx_manual", 16384)
    assert state.vision_num_ctx == 16384


# ── model-id inheritance ─────────────────────────────────────────


def test_base_model_id_own_model():
    state = _FakeState(sokrates_model_id="qwen3:8b", aifred_model_id="qwen3:14b")
    assert get_agent_base_model_id(state, "sokrates") == "qwen3:8b"


def test_base_model_id_inherits_from_aifred():
    state = _FakeState(sokrates_model_id="", aifred_model_id="qwen3:14b")
    assert get_agent_base_model_id(state, "sokrates") == "qwen3:14b"


def test_base_model_id_custom_agent(monkeypatch):
    from aifred.lib import agent_config
    monkeypatch.setattr(
        agent_config, "get_agent_config", lambda aid: object() if aid == "codine" else None
    )
    state = _FakeState(aifred_model_id="qwen3:14b")
    assert get_agent_base_model_id(state, "codine") == "qwen3:14b"
