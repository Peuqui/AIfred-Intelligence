"""Tests for the MODE_SWITCH parser — multi/agent keys + symposion_agents list."""

from unittest.mock import patch

from aifred.lib.intent_detector import _parse_mode_switch

# Fixed agent registry so tests are independent of data/agents.json
_AGENTS = {
    "aifred": {"display_name": "AIfred", "aliases": ["alfred"]},
    "sokrates": {"display_name": "Sokrates", "aliases": []},
    "salomo": {"display_name": "Salomo", "aliases": []},
    "hal": {"display_name": "HAL 9000", "aliases": ["hal 9000"]},
    "codi": {"display_name": "Codine", "aliases": ["kodi", "cody"]},
    "rabbi": {"display_name": "Rabbi Shmuel", "aliases": []},
}


def _patched_agents():
    return patch("aifred.lib.agent_config.load_agents_raw", return_value=_AGENTS)


# ── Bestehende Keys (Regression) ──────────────────────────────

class TestExistingKeys:
    def test_empty_field(self):
        assert _parse_mode_switch("") == {}
        assert _parse_mode_switch("   ") == {}

    def test_multi_valid(self):
        assert _parse_mode_switch("multi=tribunal") == {"multi_agent_mode": "tribunal"}

    def test_multi_invalid_dropped(self):
        assert _parse_mode_switch("multi=quatsch") == {}

    def test_agent_resolved_via_alias(self):
        with _patched_agents():
            assert _parse_mode_switch("agent=alfred") == {"active_agent": "aifred"}

    def test_agent_display_name(self):
        with _patched_agents():
            assert _parse_mode_switch("agent=HAL 9000") == {"active_agent": "hal"}

    def test_research_ignored(self):
        assert _parse_mode_switch("research=deep") == {}

    def test_bare_token_without_list_context_ignored(self):
        # A stray comma token with no preceding symposion_agents key
        with _patched_agents():
            assert _parse_mode_switch("multi=tribunal,hal") == {
                "multi_agent_mode": "tribunal"
            }


# ── symposion_agents Liste ────────────────────────────────────

class TestSymposionAgents:
    def test_single_agent(self):
        with _patched_agents():
            result = _parse_mode_switch("multi=symposion,symposion_agents=codi")
        assert result == {
            "multi_agent_mode": "symposion",
            "symposion_agents": ["codi"],
        }

    def test_comma_separated_list(self):
        with _patched_agents():
            result = _parse_mode_switch(
                "multi=symposion,symposion_agents=codi,hal,rabbi"
            )
        assert result["symposion_agents"] == ["codi", "hal", "rabbi"]

    def test_aliases_and_display_names_resolve(self):
        with _patched_agents():
            result = _parse_mode_switch("symposion_agents=kodi,HAL 9000")
        assert result["symposion_agents"] == ["codi", "hal"]

    def test_invalid_names_dropped(self):
        with _patched_agents():
            result = _parse_mode_switch("symposion_agents=codi,gandalf,rabbi")
        assert result["symposion_agents"] == ["codi", "rabbi"]

    def test_all_invalid_drops_key(self):
        # Empty participant list must not reach the session config —
        # it would start a symposion with zero agents.
        with _patched_agents():
            result = _parse_mode_switch("multi=symposion,symposion_agents=gandalf,frodo")
        assert result == {"multi_agent_mode": "symposion"}

    def test_duplicates_deduped(self):
        with _patched_agents():
            result = _parse_mode_switch("symposion_agents=codi,kodi,codi")
        assert result["symposion_agents"] == ["codi"]

    def test_key_after_list_terminates_collection(self):
        # A later key=value pair must stop list collection
        with _patched_agents():
            result = _parse_mode_switch(
                "symposion_agents=codi,hal,multi=symposion"
            )
        assert result == {
            "symposion_agents": ["codi", "hal"],
            "multi_agent_mode": "symposion",
        }
