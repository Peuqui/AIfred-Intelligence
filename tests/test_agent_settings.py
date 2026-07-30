"""Tests for aifred.lib.agent_settings — the per-agent dict-bucket SSOT."""

import pytest

from aifred.lib.agent_settings import (
    CANONICAL_AGENTS,
    get_agent_base_model_id,
    get_agent_setting,
    get_persisted_tuning,
    set_agent_setting,
    settings_agent,
)
from aifred.lib.agent_tuning import default_agent_tuning, default_tuning


class _FakeState:
    """Bare container standing in for AIState (agent_tuning + globals)."""

    def __init__(self, **kwargs):
        self.agent_tuning = default_agent_tuning()
        for key, value in kwargs.items():
            setattr(self, key, value)


def _mock_codine(monkeypatch):
    from aifred.lib import agent_config
    monkeypatch.setattr(
        agent_config, "get_agent_config", lambda aid: object() if aid == "codine" else None
    )


# ── settings_agent ────────────────────────────────────────────────


def test_canonical_agents_own_their_bucket():
    for agent in CANONICAL_AGENTS:
        assert settings_agent(agent) == agent


def test_registered_custom_agent_owns_bucket(monkeypatch):
    _mock_codine(monkeypatch)
    assert settings_agent("codine") == "codine"


def test_unregistered_agent_raises(monkeypatch):
    from aifred.lib import agent_config
    monkeypatch.setattr(agent_config, "get_agent_config", lambda aid: None)
    with pytest.raises(ValueError, match="Unknown agent"):
        settings_agent("tippfehler")


# ── default buckets ──────────────────────────────────────────────


def test_default_agent_tuning_has_canonical_buckets():
    tuning = default_agent_tuning()
    assert set(tuning) == set(CANONICAL_AGENTS)


def test_vision_defaults_differ():
    vision = default_tuning("vision")
    assert vision.reasoning is False
    assert vision.num_ctx_manual == 32768


def test_secondary_agents_have_temperature_offsets():
    assert default_tuning("sokrates").temperature_offset != 0.0
    assert default_tuning("salomo").temperature_offset != 0.0
    assert default_tuning("aifred").temperature_offset == 0.0


# ── get/set ──────────────────────────────────────────────────────


def test_get_agent_setting_reads_bucket():
    state = _FakeState(temperature=0.7)
    state.agent_tuning["sokrates"].top_k = 42
    assert get_agent_setting(state, "sokrates", "top_k") == 42


def test_aifred_temperature_is_global():
    state = _FakeState(temperature=0.7)
    assert get_agent_setting(state, "aifred", "temperature") == 0.7
    set_agent_setting(state, "aifred", "temperature", 0.9)
    assert state.temperature == 0.9
    # other agents keep their own bucket temperature
    set_agent_setting(state, "salomo", "temperature", 0.3)
    assert state.agent_tuning["salomo"].temperature == 0.3
    assert state.temperature == 0.9


def test_get_agent_setting_unknown_field_raises_without_default():
    state = _FakeState()
    with pytest.raises(AttributeError):
        get_agent_setting(state, "salomo", "does_not_exist")


def test_get_agent_setting_default():
    state = _FakeState()
    assert get_agent_setting(state, "salomo", "does_not_exist", 40) == 40


def test_set_agent_setting_writes_bucket():
    state = _FakeState(temperature=0.5)
    set_agent_setting(state, "vision", "num_ctx_manual", 16384)
    assert state.agent_tuning["vision"].num_ctx_manual == 16384


def test_custom_agent_reads_defaults_then_owns_bucket(monkeypatch):
    _mock_codine(monkeypatch)
    state = _FakeState(temperature=0.5)
    # No bucket yet → defaults, no state mutation
    assert get_agent_setting(state, "codine", "top_k") == default_tuning("codine").top_k
    assert "codine" not in state.agent_tuning
    # First write materializes the bucket
    set_agent_setting(state, "codine", "top_k", 33)
    assert state.agent_tuning["codine"].top_k == 33
    # aifred's bucket stays untouched
    assert state.agent_tuning["aifred"].top_k == default_tuning("aifred").top_k


def test_model_owner_inheritance(monkeypatch):
    from aifred.lib.agent_settings import model_owner
    _mock_codine(monkeypatch)
    state = _FakeState(temperature=0.5)
    state.agent_tuning["aifred"].model_id = "qwen3:14b"
    # No own model → AIfred owns the loaded model (and its speed toggles)
    assert model_owner(state, "sokrates") == "aifred"
    assert model_owner(state, "codine") == "aifred"
    # Own model → agent owns it
    state.agent_tuning["sokrates"].model_id = "qwen3:8b"
    assert model_owner(state, "sokrates") == "sokrates"


# ── model-id inheritance ─────────────────────────────────────────


def test_base_model_id_own_model():
    state = _FakeState()
    state.agent_tuning["aifred"].model_id = "qwen3:14b"
    state.agent_tuning["sokrates"].model_id = "qwen3:8b"
    assert get_agent_base_model_id(state, "sokrates") == "qwen3:8b"


def test_base_model_id_inherits_from_aifred():
    state = _FakeState()
    state.agent_tuning["aifred"].model_id = "qwen3:14b"
    assert get_agent_base_model_id(state, "sokrates") == "qwen3:14b"


def test_base_model_id_custom_agent(monkeypatch):
    _mock_codine(monkeypatch)
    state = _FakeState()
    state.agent_tuning["aifred"].model_id = "qwen3:14b"
    assert get_agent_base_model_id(state, "codine") == "qwen3:14b"


# ── persisted-settings access (Hub path, no State) ───────────────


def test_get_persisted_tuning_reads_bucket():
    settings = {"agent_tuning": {"sokrates": {"top_k": 25}}}
    assert get_persisted_tuning(settings, "sokrates", "top_k", 40) == 25


def test_get_persisted_tuning_default_on_missing():
    assert get_persisted_tuning({}, "sokrates", "top_k", 40) == 40


def test_get_persisted_tuning_aifred_temperature_is_global():
    settings = {"temperature": 0.6, "agent_tuning": {"aifred": {}}}
    assert get_persisted_tuning(settings, "aifred", "temperature", 0.3) == 0.6


def test_get_persisted_tuning_unknown_agent_returns_default(monkeypatch):
    from aifred.lib import agent_config
    monkeypatch.setattr(agent_config, "get_agent_config", lambda aid: None)
    assert get_persisted_tuning({}, "tippfehler", "top_k", 40) == 40
