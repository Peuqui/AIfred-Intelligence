"""The chat and llm_history entries of an agent's reply, shared by the browser and the server turn
(lib/formatting.py: build_agent_panel_entry, assistant_history_entry)."""

import json

from aifred.lib.formatting import assistant_history_entry, build_agent_panel_entry
from aifred.lib.i18n import t


def test_standard_reply_is_text_and_footer() -> None:
    entry = build_agent_panel_entry("aifred", "Hallo.", metadata={"tokens_per_sec": 30.0, "inference_time": 1.2})
    text, footer = entry["content"].split("\n\n", 1)
    assert text == "Hallo."
    assert "tok/s" in footer
    assert (entry["role"], entry["agent"], entry["mode"], entry["has_audio"]) == ("assistant", "aifred", "standard", False)


def test_a_marker_leads_a_multi_agent_step() -> None:
    entry = build_agent_panel_entry("sokrates", "Einwand.", mode="critical_review", round_num=2, marker="[Kritik R2]\n\n")
    assert entry["content"].startswith("[Kritik R2]\n\nEinwand.")
    assert entry["round_num"] == 2


def test_consensus_tags_become_words_of_the_language() -> None:
    entry = build_agent_panel_entry("salomo", "Fertig [LGTM]", lang="de")
    assert t("consensus_agreed", lang="de") in entry["content"]
    assert "[LGTM]" not in entry["content"]


def test_audio_urls_mark_the_entry() -> None:
    entry = build_agent_panel_entry("aifred", "Ton.", metadata={"audio_urls": ["/_upload/audio/a.wav"]})
    assert entry["has_audio"] is True
    assert json.loads(entry["audio_urls_json"]) == ["/_upload/audio/a.wav"]
    assert entry["metadata"]["playback_rate"] == "1.0x"


def test_the_history_entry_carries_the_speaker_and_drops_thinking() -> None:
    entry = assistant_history_entry("aifred", "<think>überlegen</think>Antwort.", ["Notiz"])
    assert entry is not None
    assert entry["role"] == "assistant"
    assert entry["content"].startswith("[AIFRED]: Antwort.")
    assert "überlegen" not in entry["content"]
    assert "Notiz" in entry["content"]


def test_a_reply_of_thinking_only_leaves_no_history_entry() -> None:
    assert assistant_history_entry("aifred", "<think>nur Gedanken</think>") is None
