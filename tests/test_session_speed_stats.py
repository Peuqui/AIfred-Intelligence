"""session_speed_stats groups answers by entry and running profile.

The answer footer names the profile since 2026-09-16
("(<entry> · PLE→Host→SSD)"); the stats script took that whole text for the
model name and merged side-channel variants no longer (16.09.2026).
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "session_speed_stats.py"


@pytest.fixture(scope="module")
def stats():
    spec = importlib.util.spec_from_file_location("session_speed_stats", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["session_speed_stats"] = module
    spec.loader.exec_module(module)
    return module


def _answer(source: str, decode: float, stamp: str = "2026-09-16T18:00:00") -> dict:
    return {
        "role": "assistant",
        "timestamp": stamp,
        "metadata": {"source": source, "tokens_per_sec": decode, "prompt_per_sec": 600.0},
    }


def test_profiles_of_one_model_stay_apart(stats, tmp_path: Path) -> None:
    history = [
        # New footer: variant with profile, and the plain entry with a profile.
        _answer("AIfred (Flash-MTP-vllm-vlm-qwen3vl4b · PLE→Host→SSD)", 48.0),
        _answer("AIfred (Flash-MTP-vllm · PLE→Host→SSD)", 50.0),
        _answer("AIfred (Flash-MTP-vllm · PLE→Host→SSD→SSD)", 37.0),
        # Old footer: bare entry, the path is unknown.
        _answer("AIfred (Flash-MTP-vllm-tts-qwen3local)", 44.0),
    ]
    session = tmp_path / "s.json"
    session.write_text(json.dumps({"data": {"chat_history": history}}))

    groups = stats.collect(tmp_path)

    assert sorted(groups) == [
        ("Flash-MTP-vllm", "vllm"),
        ("Flash-MTP-vllm · PLE→Host→SSD", "vllm"),
        ("Flash-MTP-vllm · PLE→Host→SSD→SSD", "vllm"),
    ]
    # The side-channel variant joins its base entry within the same profile.
    assert len(groups[("Flash-MTP-vllm · PLE→Host→SSD", "vllm")]) == 2


def test_llamacpp_entries_with_badges_keep_their_backend(stats, tmp_path: Path) -> None:
    session = tmp_path / "s.json"
    session.write_text(
        json.dumps({"data": {"chat_history": [_answer("AIfred (Qwen3VL-4B-Instruct-Q8_0 · n-gram)", 53.0)]}})
    )
    assert list(stats.collect(tmp_path)) == [("Qwen3VL-4B-Instruct-Q8_0 · n-gram", "llamacpp")]
