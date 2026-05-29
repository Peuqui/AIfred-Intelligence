"""Tests für aifred.lib.vision_prewarm."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import aifred.lib.vision_prewarm as vpw


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def patched_settings(monkeypatch, tmp_path: Path):
    """Provide a fake settings dict by monkeypatching _load_vision_settings."""
    state: dict = {"settings": {}}

    def fake_load():
        return state["settings"]

    monkeypatch.setattr(vpw, "_load_vision_settings", fake_load)
    return state


class TestActiveCheck:
    def test_off_mode(self, patched_settings):
        patched_settings["settings"] = {"vision_mode": "off"}
        assert vpw.is_vision_active() is False
        assert vpw.get_active_vlm_model() is None

    def test_on_demand_mode(self, patched_settings):
        patched_settings["settings"] = {
            "vision_mode": "on-demand",
            "vlm": {"model": "qwen3-vl:4b-instruct-q8_0"},
        }
        assert vpw.is_vision_active() is True
        assert vpw.get_active_vlm_model() == "qwen3-vl:4b-instruct-q8_0"

    def test_live_mode(self, patched_settings):
        patched_settings["settings"] = {
            "vision_mode": "live",
            "vlm": {"model": "qwen3-vl:8b-instruct-q8_0"},
        }
        assert vpw.is_vision_active() is True


class TestPrewarm:
    def test_off_mode_is_noop(self, patched_settings):
        patched_settings["settings"] = {"vision_mode": "off"}
        assert run(vpw.prewarm_vlm()) is True

    def test_calls_ollama_with_correct_keep_alive_for_on_demand(
        self, patched_settings, monkeypatch
    ):
        patched_settings["settings"] = {
            "vision_mode": "on-demand",
            "vlm": {"model": "qwen3-vl:4b-instruct-q8_0", "keep_alive": "30m"},
        }
        captured: dict = {}

        async def fake_generate(self, **kwargs):
            captured.update(kwargs)
            return {"done": True}

        import ollama

        monkeypatch.setattr(ollama.AsyncClient, "generate", fake_generate)
        assert run(vpw.prewarm_vlm()) is True
        assert captured["model"] == "qwen3-vl:4b-instruct-q8_0"
        assert captured["prompt"] == ""
        assert captured["keep_alive"] == "30m"

    def test_live_mode_forces_keep_alive_minus_one(
        self, patched_settings, monkeypatch
    ):
        patched_settings["settings"] = {
            "vision_mode": "live",
            "vlm": {"model": "qwen3-vl:4b-instruct-q8_0", "keep_alive": "30m"},
        }
        captured: dict = {}

        async def fake_generate(self, **kwargs):
            captured.update(kwargs)
            return {"done": True}

        import ollama

        monkeypatch.setattr(ollama.AsyncClient, "generate", fake_generate)
        assert run(vpw.prewarm_vlm()) is True
        # live mode → int -1, not the string "-1" (Ollama parses strings
        # as a duration and would reject "-1")
        assert captured["keep_alive"] == -1

    def test_returns_false_when_no_model_configured(self, patched_settings):
        patched_settings["settings"] = {"vision_mode": "on-demand", "vlm": {}}
        assert run(vpw.prewarm_vlm()) is False

    def test_ollama_failure_returns_false(self, patched_settings, monkeypatch):
        patched_settings["settings"] = {
            "vision_mode": "on-demand",
            "vlm": {"model": "qwen3-vl:4b-instruct-q8_0"},
        }

        async def boom(self, **kwargs):
            raise ConnectionError("ollama unreachable")

        import ollama

        monkeypatch.setattr(ollama.AsyncClient, "generate", boom)
        assert run(vpw.prewarm_vlm()) is False

    def test_keep_alive_override_wins(self, patched_settings, monkeypatch):
        patched_settings["settings"] = {
            "vision_mode": "on-demand",
            "vlm": {"model": "qwen3-vl:4b-instruct-q8_0", "keep_alive": "30m"},
        }
        captured: dict = {}

        async def fake_generate(self, **kwargs):
            captured.update(kwargs)
            return {"done": True}

        import ollama

        monkeypatch.setattr(ollama.AsyncClient, "generate", fake_generate)
        run(vpw.prewarm_vlm(keep_alive_override="2h"))
        assert captured["keep_alive"] == "2h"


class TestSyncWrapper:
    def test_sync_wrapper_runs(self, patched_settings):
        patched_settings["settings"] = {"vision_mode": "off"}
        # The off-mode branch is no-op + true — exercises the sync wrapper path
        assert vpw.prewarm_vlm_sync() is True
