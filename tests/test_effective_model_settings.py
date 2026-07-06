"""Tests for get_effective_model_from_settings — Hub-side variant resolution.

Regression for the double-load bug (2026-07-06): the Automatik has no own
``automatik_speed_mode`` key, so the Hub resolved the non-speed variant
while the browser requested ``-speed`` — llama-swap swapped the 27B twice
per Puck request (~5 min).
"""

from unittest.mock import patch

from aifred.lib.config import get_effective_model_from_settings


def _settings(**overrides):
    base = {
        "backend_type": "llamacpp",
        "backend_models": {
            "llamacpp": {
                "aifred_model": "Qwen3.6-27B",
                "automatik_model": "",
                "sokrates_model": "",
            },
        },
        "aifred_speed_mode": True,
        "sokrates_speed_mode": False,
        "enable_tts": False,
        "tts_engine": "",
    }
    base.update(overrides)
    return base


def _run(agent, settings):
    """Call the resolver with everything except the speed logic mocked out.

    resolve_variant_suffix is replaced by a probe that returns '-speed'
    iff speed_on was passed as True — so the result directly shows which
    speed toggle the resolver picked.
    """
    def fake_suffix(_path, _base_id, *, speed_on, **_kw):
        return "-speed" if speed_on else ""

    with patch("aifred.lib.settings.load_settings", return_value=settings), \
         patch("aifred.lib.calibration.parse_llamaswap_config", return_value={"Qwen3.6-27B-speed": {}}), \
         patch("aifred.lib.calibration.resolve_variant_suffix", side_effect=fake_suffix), \
         patch("aifred.lib.vision_prewarm.is_vision_active", return_value=False):
        return get_effective_model_from_settings(agent)


class TestAutomatikSpeedMirror:
    def test_automatik_empty_model_mirrors_aifred_speed(self):
        # THE bug scenario: automatik_model empty + aifred speed ON
        # → Hub must resolve the same '-speed' profile as the browser.
        assert _run("automatik", _settings()) == "Qwen3.6-27B-speed"

    def test_automatik_own_model_still_mirrors_aifred_speed(self):
        # Automatik has no own speed toggle even with a dedicated model.
        s = _settings()
        s["backend_models"]["llamacpp"]["automatik_model"] = "Qwen3-4B"
        assert _run("automatik", s) == "Qwen3-4B-speed"

    def test_automatik_speed_off_when_aifred_speed_off(self):
        assert _run("automatik", _settings(aifred_speed_mode=False)) == "Qwen3.6-27B"


class TestSharedModelSpeedFallback:
    def test_agent_sharing_aifred_model_uses_aifred_speed(self):
        # sokrates_model empty → shares AIfred's LLM → must also share
        # AIfred's speed toggle (sokrates' own toggle is False here).
        assert _run("sokrates", _settings()) == "Qwen3.6-27B-speed"

    def test_agent_with_own_model_uses_own_speed(self):
        s = _settings()
        s["backend_models"]["llamacpp"]["sokrates_model"] = "Qwen3-14B"
        assert _run("sokrates", s) == "Qwen3-14B"  # sokrates_speed_mode=False

    def test_aifred_uses_own_speed(self):
        assert _run("aifred", _settings()) == "Qwen3.6-27B-speed"
        assert _run("aifred", _settings(aifred_speed_mode=False)) == "Qwen3.6-27B"
