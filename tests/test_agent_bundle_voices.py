"""Agent-Bundle: Stimmen kommen aus dem gemeinsamen Stimmenbaum, ohne Engine-Sonderfälle."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import pytest

from aifred.lib import agent_bundle


@pytest.fixture
def bundle_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    voices = tmp_path / "voices"
    (voices / "Codine").mkdir(parents=True)
    (voices / "Codine" / "Codine.wav").write_bytes(b"wav")
    (voices / "Codine" / "Codine.txt").write_text("Transkript", encoding="utf-8")
    monkeypatch.setattr(agent_bundle, "TTS_VOICES_DIR", voices)
    monkeypatch.setattr(agent_bundle, "PROMPTS_DIR", tmp_path / "prompts")
    monkeypatch.setattr(agent_bundle, "load_agents_raw", lambda: {"codine": {"name": "Codine"}})
    monkeypatch.setattr(agent_bundle, "_load_settings", lambda: {
        "tts_agent_voices_per_engine": {
            "qwen3local": {"codine": {"voice": "Codine"}},
            "xtts": {"codine": {"voice": "★ Codine"}},
            "dashscope_audio3": {"codine": {"voice": "Mary"}},
        },
    })
    return voices


def test_export_packs_the_voice_folder_once(bundle_env: Path) -> None:
    names = zipfile.ZipFile(io.BytesIO(agent_bundle.export_bundle(["codine"]))).namelist()
    assert sorted(n for n in names if n.startswith("voices/")) == [
        "voices/Codine/Codine.txt", "voices/Codine/Codine.wav",
    ]


def test_import_writes_voice_folder_and_keeps_existing_ones(
    bundle_env: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    zip_bytes = agent_bundle.export_bundle(["codine"])
    source = zipfile.ZipFile(io.BytesIO(zip_bytes))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for name in source.namelist():
            out.writestr(name, source.read(name))
        out.writestr("voices/Neu/Neu.wav", b"neu")
    saved: dict[str, Any] = {}
    monkeypatch.setattr(agent_bundle, "load_agents_raw", lambda: {})
    monkeypatch.setattr(agent_bundle, "save_agents_raw", lambda agents: saved.update(agents=agents))
    monkeypatch.setattr(agent_bundle, "save_settings", lambda settings: saved.update(settings=settings))
    _ids, warnings = agent_bundle.import_bundle(buf.getvalue())
    assert (bundle_env / "Neu" / "Neu.wav").read_bytes() == b"neu"
    assert any("existierte schon" in w for w in warnings)


def test_import_rejects_a_bundle_with_a_path_escape() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        out.writestr("voices/../escape.wav", b"x")
    with pytest.raises(ValueError, match="Unsafe path"):
        agent_bundle.import_bundle(buf.getvalue())
