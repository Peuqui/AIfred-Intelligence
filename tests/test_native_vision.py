"""Tests für die Native-Vision-SSOT (vision_utils.has_native_vision) —
llama.cpp (--mmproj) und vLLM (Checkpoint-Encoder ohne --language-model-only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aifred.lib import vision_utils as vu


def _checkpoint(root: Path, name: str, *, vision: bool) -> Path:
    ckpt = root / name
    ckpt.mkdir()
    config: dict = {"architectures": ["X"]}
    if vision:
        config["vision_config"] = {}
    (ckpt / "config.json").write_text(json.dumps(config))
    return ckpt


@pytest.fixture
def swap_config(tmp_path, monkeypatch):
    vision_ckpt = _checkpoint(tmp_path, "vl", vision=True)
    text_ckpt = _checkpoint(tmp_path, "text", vision=False)
    vllm = "/venv/bin/python -m vllm.entrypoints.openai.api_server --model"
    models = {
        "gguf-mmproj": {"full_cmd": "llama-server --model a.gguf --mmproj m.gguf"},
        "gguf-plain": {"full_cmd": "llama-server --model b.gguf"},
        "Qwen-VL-named-gguf": {"full_cmd": "llama-server --model c.gguf"},
        "vllm-vision": {"full_cmd": f"{vllm} {vision_ckpt} --port 1"},
        "vllm-vision-vlm-qwen3vl4b": {
            "full_cmd": f"{vllm} {vision_ckpt} --port 1 --language-model-only"
        },
        "vllm-text": {"full_cmd": f"{vllm} {text_ckpt} --port 1"},
    }
    config_file = tmp_path / "config.yaml"
    config_file.write_text("models: {}\n")
    monkeypatch.setattr("aifred.lib.config.LLAMASWAP_CONFIG_PATH", config_file)
    monkeypatch.setattr(
        "aifred.lib.calibration.parse_llamaswap_config", lambda _path: models
    )
    monkeypatch.setattr(vu, "_native_vision_cache_mtime", -1.0)


@pytest.mark.parametrize("model, expected", [
    ("gguf-mmproj", True),
    ("gguf-plain", False),
    ("vllm-vision", True),
    ("vllm-vision-vlm-qwen3vl4b", False),
    ("vllm-text", False),
    ("unknown", False),
])
def test_has_native_vision(swap_config, model, expected):
    assert vu.has_native_vision(model) is expected


def test_llamaswap_entry_ignores_name_pattern(swap_config):
    # Ein llama-swap-Eintrag entscheidet über das, was er lädt — der
    # "VL"-Name allein macht ihn nicht vision-fähig.
    assert vu.is_vision_model_sync("Qwen-VL-named-gguf") is False


def test_foreign_id_uses_name_pattern(swap_config):
    assert vu.is_vision_model_sync("qwen3-vl:4b-instruct-q8_0") is True
    assert vu.is_vision_model_sync("qwen3:8b") is False
