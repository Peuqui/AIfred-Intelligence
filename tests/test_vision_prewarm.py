"""Tests für aifred.lib.vision_prewarm.

New model (see is_vision_active docstring):
* is_vision_active() == "Vision plugin enabled" (the override trigger),
  NOT vision_mode. The plugin status is a filesystem fact (tools/ vs
  disabled/), so we mock plugin_registry.is_plugin_enabled.
* prewarm_vlm() only PRELOADS the VLM in ``live`` mode. ``on-demand`` keeps
  the reserved slot empty (loads lazily on first request), so it's a no-op.
  Plugin disabled → no-op too.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import aifred.lib.plugin_registry as pr
import aifred.lib.vision_prewarm as vpw


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def patched_settings(monkeypatch, tmp_path: Path):
    """Fake vision settings + plugin-enabled status.

    ``state["settings"]`` feeds _load_vision_settings (vision_mode/vlm),
    ``state["plugin_enabled"]`` feeds is_plugin_enabled (the override trigger)."""
    state: dict = {"settings": {}, "plugin_enabled": True, "visiond": None}

    monkeypatch.setattr(vpw, "_load_vision_settings", lambda: state["settings"])
    monkeypatch.setattr(pr, "is_plugin_enabled", lambda *a, **k: state["plugin_enabled"])
    # Describer-Auflösung isolieren (sonst liest sie die ECHTE llama-swap-
    # config): Default None = Ollama-Fallback-Pfad; Tests für den
    # llama-swap-Zweig setzen state["visiond"] auf einen Profilnamen.
    import aifred.lib.vision_routing as vr
    monkeypatch.setattr(vr, "visiond_profile_for", lambda name: state["visiond"])
    return state


class TestActiveCheck:
    def test_plugin_disabled(self, patched_settings):
        # Override trigger is off when the plugin is disabled, regardless of mode.
        patched_settings["plugin_enabled"] = False
        patched_settings["settings"] = {"vision_mode": "live"}
        assert vpw.is_vision_active() is False

    def test_plugin_enabled_on_demand(self, patched_settings):
        patched_settings["plugin_enabled"] = True
        patched_settings["settings"] = {
            "vision_mode": "on-demand",
            "vlm": {"model": "qwen3-vl:4b-instruct-q8_0"},
        }
        assert vpw.is_vision_active() is True
        assert vpw.get_active_vlm_model() == "qwen3-vl:4b-instruct-q8_0"

    def test_plugin_enabled_live(self, patched_settings):
        patched_settings["plugin_enabled"] = True
        patched_settings["settings"] = {"vision_mode": "live"}
        assert vpw.is_vision_active() is True


class TestPrewarm:
    def test_plugin_disabled_is_noop(self, patched_settings):
        patched_settings["plugin_enabled"] = False
        patched_settings["settings"] = {"vision_mode": "live"}
        assert run(vpw.prewarm_vlm()) is True

    def test_on_demand_does_not_preload(self, patched_settings, monkeypatch):
        # on-demand: slot reserved but empty → no Ollama call, just True.
        patched_settings["settings"] = {
            "vision_mode": "on-demand",
            "vlm": {"model": "qwen3-vl:4b-instruct-q8_0", "keep_alive": "30m"},
        }
        called: dict = {"hit": False}

        async def fake_generate(self, **kwargs):
            called["hit"] = True
            return {"done": True}

        import ollama
        monkeypatch.setattr(ollama.AsyncClient, "generate", fake_generate)
        assert run(vpw.prewarm_vlm()) is True
        assert called["hit"] is False  # on-demand must NOT preload

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
        assert captured["model"] == "qwen3-vl:4b-instruct-q8_0"
        assert captured["prompt"] == ""
        # live → int -1, not "-1" (Ollama parses strings as a duration)
        assert captured["keep_alive"] == -1

    def test_visiond_profile_prewarms_via_llamaswap(
        self, patched_settings, monkeypatch
    ):
        # Existiert ein -visiond-Profil, lädt der Prewarm es per
        # Mini-Request über llama-swap statt über Ollama.
        patched_settings["settings"] = {
            "vision_mode": "live",
            "vlm": {"model": "qwen3-vl:4b-instruct-q8_0"},
        }
        patched_settings["visiond"] = "Qwen3VL-4B-Instruct-Q8_0-visiond"
        captured: dict = {}

        class FakeResp:
            def raise_for_status(self):
                pass

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json=None):
                captured["url"] = url
                captured["json"] = json
                return FakeResp()

        import httpx
        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
        assert run(vpw.prewarm_vlm()) is True
        assert captured["json"]["model"] == "Qwen3VL-4B-Instruct-Q8_0-visiond"
        assert captured["json"]["max_tokens"] == 1

    def test_returns_false_when_no_model_configured(self, patched_settings):
        # live mode reaches the model check (on-demand would no-op earlier).
        patched_settings["settings"] = {"vision_mode": "live", "vlm": {}}
        assert run(vpw.prewarm_vlm()) is False

    def test_ollama_failure_returns_false(self, patched_settings, monkeypatch):
        patched_settings["settings"] = {
            "vision_mode": "live",
            "vlm": {"model": "qwen3-vl:4b-instruct-q8_0"},
        }

        async def boom(self, **kwargs):
            raise ConnectionError("ollama unreachable")

        import ollama
        monkeypatch.setattr(ollama.AsyncClient, "generate", boom)
        assert run(vpw.prewarm_vlm()) is False

    def test_keep_alive_override_wins(self, patched_settings, monkeypatch):
        # override bypasses the on-demand no-op and forces a load.
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
        patched_settings["plugin_enabled"] = False
        # plugin-disabled branch is no-op + true — exercises the sync wrapper
        assert vpw.prewarm_vlm_sync() is True
