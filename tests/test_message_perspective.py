"""How an agent sees the conversation (message_builder.build_messages_from_llm_history).

Until 07.11.2026 the perspective knew aifred, sokrates and salomo only: any other agent got the
history as it was stored, so AIfred's replies came to it as its own (role assistant). The judge's
view (observer) always carried Salomo's style reminder, whoever judged."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aifred.lib import agent_config, message_builder, prompt_loader
from aifred.lib.message_builder import build_messages_from_llm_history, history_speaker

HISTORY = [
    {"role": "user", "content": "Was ist Wahrheit?"},
    {"role": "assistant", "content": "[AIFRED]: Eine Frage für Philosophen."},
    {"role": "assistant", "content": "[SOKRATES]: Ich weiß, dass ich nichts weiß."},
    {"role": "assistant", "content": "[PATER]: Gott weiß es."},
    {"role": "assistant", "content": "[AIfred]: Von einem Kanal geschrieben."},         # display-name label
    {"role": "assistant", "content": "[AUTOMATISCHE MELDUNG (scheduler)]: Es ist 12 Uhr."},
    {"role": "system", "content": "Bisher: ein Gespräch über Wahrheit."},
]


@pytest.fixture(autouse=True)
def agents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = agent_config._default_agents()
    raw["pater"] = {**raw["sokrates"], "display_name": "Pater Tuck", "aliases": [], "role": "agent", "color": "#7d8590"}
    agents_file = tmp_path / "agents.json"
    agents_file.write_text(json.dumps({"agents": raw}), encoding="utf-8")
    monkeypatch.setattr(agent_config, "AGENTS_FILE", agents_file)
    monkeypatch.setattr(message_builder, "get_user_name", lambda: "Peuqui")
    monkeypatch.setattr(prompt_loader, "load_personality_reminder", lambda agent, lang=None: f"[STIL {agent}]")


def test_the_label_names_the_agent_by_its_id_also_in_the_display_name_form() -> None:
    assert history_speaker("[CODINE]: hallo") == (None, "[CODINE]: hallo")      # no such agent here
    assert history_speaker("[PATER]: Amen") == ("pater", "Amen")
    assert history_speaker("[AIfred]: Gern") == ("aifred", "Gern")
    assert history_speaker("[2026-11-07 Sat 09:16] Frage") == (None, "[2026-11-07 Sat 09:16] Frage")


def test_a_custom_agent_sees_only_its_own_replies_as_its_own() -> None:
    messages = build_messages_from_llm_history(HISTORY, perspective="pater")
    assert messages == [
        {"role": "user", "content": "[Peuqui]: Was ist Wahrheit?"},
        {"role": "user", "content": "[AIfred]: Eine Frage für Philosophen."},
        {"role": "user", "content": "[Sokrates]: Ich weiß, dass ich nichts weiß."},
        {"role": "assistant", "content": "Gott weiß es."},
        {"role": "user", "content": "[AIfred]: Von einem Kanal geschrieben."},
        {"role": "user", "content": "[AUTOMATISCHE MELDUNG (scheduler)]: Es ist 12 Uhr."},
        {"role": "user", "content": "[CONVERSATION CONTEXT]:\nBisher: ein Gespräch über Wahrheit."},
    ]


def test_aifred_knows_his_replies_in_both_label_forms() -> None:
    own = [m["content"] for m in build_messages_from_llm_history(HISTORY, perspective="aifred") if m["role"] == "assistant"]
    assert own == ["Eine Frage für Philosophen.", "Von einem Kanal geschrieben."]


def test_the_judge_sees_every_reply_as_someone_elses_and_keeps_its_own_style() -> None:
    messages = build_messages_from_llm_history(HISTORY, "Dein Urteil?", perspective="pater", detected_language="de", observer=True)
    assert all(m["role"] == "user" for m in messages)
    assert "[Pater Tuck]: Gott weiß es." in [m["content"] for m in messages]
    assert messages[-1]["content"].startswith("[STIL pater]\n\nDein Urteil?")
    assert messages[-1]["content"].endswith("[AUSGABESPRACHE: DEUTSCH]")
