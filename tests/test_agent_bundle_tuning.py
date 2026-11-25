"""Agent-Bundle: das Tuning eines Agenten (settings["agent_tuning"][id]) reist mit und kommt nur dort wieder an."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fake_tts_service import FakeTTSService, install_fake_service

from aifred.lib import agent_bundle, prompt_loader
from aifred.lib.user_error import Invalid

TUNING = {"temperature": 0.7, "top_k": 40, "top_p": 0.9, "min_p": 0.05, "repeat_penalty": 1.1,
          "personality": False, "reasoning": True, "speed_mode": True, "thinking_by_model": {"m": "on"}}
AGENT = {"display_name": "Codine", "emoji": "🧑‍💻", "description": "", "role": "agent", "color": "#7d8590", "tools": []}


@pytest.fixture
def saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    fake = FakeTTSService([])
    fake.voices_dir = str(tmp_path / "voices")
    install_fake_service(monkeypatch, tmp_path, fake)
    for lang in ("de", "en"):
        (tmp_path / "prompts" / lang).mkdir(parents=True)
    monkeypatch.setattr(prompt_loader, "PROMPTS_DIR", tmp_path / "prompts")
    state: dict[str, Any] = {"agents": {"codine": AGENT}, "settings": {"agent_tuning": {"codine": {**TUNING, "num_ctx_manual": 1}}}}
    monkeypatch.setattr(agent_bundle, "load_agents_raw", lambda: dict(state["agents"]))
    monkeypatch.setattr(agent_bundle, "save_agents_raw", lambda agents: state.update(agents=agents))
    monkeypatch.setattr(agent_bundle, "_load_settings", lambda: json.loads(json.dumps(state["settings"])))
    monkeypatch.setattr(agent_bundle, "save_settings", lambda settings: state.update(settings=settings) or True)
    return state


def _with_tuning(zip_bytes: bytes, tuning: dict[str, Any]) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(zip_bytes))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as out:
        for name in source.namelist():
            out.writestr(name, json.dumps(tuning) if name.endswith("tuning.json") else source.read(name))
    return buffer.getvalue()


def test_export_carries_the_persisted_tuning(saved: dict[str, Any]) -> None:
    archive = zipfile.ZipFile(io.BytesIO(agent_bundle.export_bundle(["codine"])))
    assert json.loads(archive.read("agents/codine/tuning.json")) == TUNING   # num_ctx_manual is not persisted tuning


def test_roundtrip_lands_in_the_tuning_of_the_agent(saved: dict[str, Any]) -> None:
    bundle = agent_bundle.export_bundle(["codine"])
    saved["agents"] = {}
    saved["settings"] = {}
    agent_bundle.import_bundle(bundle)
    assert saved["settings"]["agent_tuning"] == {"codine": TUNING}


def test_roundtrip_with_rename_lands_under_the_new_id(saved: dict[str, Any]) -> None:
    bundle = agent_bundle.export_bundle(["codine"])
    ids, _warnings = agent_bundle.import_bundle(bundle, conflict="rename")
    assert ids == ["codine_imported"]
    assert saved["settings"]["agent_tuning"]["codine_imported"] == TUNING
    assert saved["settings"]["agent_tuning"]["codine"]["num_ctx_manual"] == 1   # the original stays


def test_the_voice_lands_with_its_voice_fields_only(saved: dict[str, Any]) -> None:
    voice = {"voice": "de-DE-KatjaNeural", "speed": "1.1x", "pitch": "1.0x"}
    saved["settings"]["tts_agent_voices_per_engine"] = {"edge": {"codine": {**voice, "enabled": True}}}
    bundle = agent_bundle.export_bundle(["codine"])
    saved["agents"] = {}
    saved["settings"] = {}
    agent_bundle.import_bundle(bundle)
    assert saved["settings"]["tts_agent_voices_per_engine"] == {"edge": {"codine": voice}}


def test_a_foreign_settings_key_is_refused_and_nothing_is_written(saved: dict[str, Any]) -> None:
    bundle = _with_tuning(agent_bundle.export_bundle(["codine"]), {"temperature": 0.5, "inject_api_token": "x"})
    saved["agents"] = {}
    before = json.loads(json.dumps(saved["settings"]))
    with pytest.raises(agent_bundle.BundleUnreadable) as refused:
        agent_bundle.import_bundle(bundle)
    assert refused.value.key == "bundle_tuning_invalid"
    assert saved["settings"] == before and saved["agents"] == {}


def test_a_value_outside_its_range_is_refused(saved: dict[str, Any]) -> None:
    bundle = _with_tuning(agent_bundle.export_bundle(["codine"]), {"temperature": 5.0})
    saved["agents"] = {}
    with pytest.raises(Invalid) as refused:
        agent_bundle.import_bundle(bundle)
    assert refused.value.key == "sampling_out_of_range"
    assert saved["agents"] == {}


def test_a_bundle_of_the_old_format_is_refused(saved: dict[str, Any]) -> None:
    source = zipfile.ZipFile(io.BytesIO(agent_bundle.export_bundle(["codine"])))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as out:
        for name in source.namelist():
            data = source.read(name)
            if name == "manifest.json":
                data = json.dumps({**json.loads(data), "format_version": 1}).encode()
            out.writestr(name, data)
    with pytest.raises(agent_bundle.BundleUnreadable) as refused:
        agent_bundle.import_bundle(buffer.getvalue())
    assert refused.value.key == "bundle_version"
