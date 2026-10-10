"""Agent editor save: a text field is taken from the page only when the user
changed it — an empty, freshly rebuilt field must never wipe stored text
(10.10.2026: AIfred's identity prompt and description were emptied)."""
from __future__ import annotations

import json
from types import MethodType

import pytest

import aifred.lib.agent_config as agent_config
from aifred.state._agent_editor_mixin import AgentEditorMixin as M


class _Editor:
    """What save_agent_editor touches, editing the existing agent 'aifred'."""

    def __init__(self) -> None:
        self.editor_agent_id = "aifred"
        self.editor_display_name = "AIfred"
        self.editor_emoji = "🎩"
        self.editor_role = "main"
        self.editor_tools = {"web_search": True}
        self.editor_dirty = True
        self._editor_typed: list[str] = []
        self._editor_description = "Gentleman-Berater und KI-Butler"
        self._editor_prompt_content = "Du bist AIfred."
        self._agents_json_revision = 0
        self.saved_prompt: str | None = None
        self.mark_editor_dirty = MethodType(M.mark_editor_dirty, self)

    def _save_editor_prompt_to_disk(self) -> None:
        self.saved_prompt = self._editor_prompt_content

    def add_debug(self, message: str) -> None:
        pass

    def _refresh_agent_dropdown(self) -> None:
        pass


@pytest.fixture
def editor(monkeypatch: pytest.MonkeyPatch):
    updates: list[dict] = []
    monkeypatch.setattr(agent_config, "update_agent", lambda agent_id, payload: updates.append(payload))
    state = _Editor()
    state.updates = updates  # type: ignore[attr-defined]
    return state


def _save(editor: _Editor, **dom: str) -> None:
    page = {"name": "", "description": "", "prompt": "", "agent_id": "", **dom}
    M.save_agent_editor(editor, json.dumps(page))  # type: ignore[arg-type]


def test_empty_untouched_fields_keep_the_stored_text(editor) -> None:
    _save(editor)                                    # page rebuilt, fields empty
    assert editor.updates[-1]["description"] == "Gentleman-Berater und KI-Butler"
    assert editor.saved_prompt == "Du bist AIfred."
    assert editor.editor_display_name == "AIfred"


def test_changed_fields_are_saved_even_when_cleared(editor) -> None:
    editor.mark_editor_dirty("description")
    editor.mark_editor_dirty("prompt")
    _save(editor, description="", prompt="Neue Identität.")
    assert editor.updates[-1]["description"] == ""   # cleared on purpose
    assert editor.saved_prompt == "Neue Identität."


def test_a_save_starts_a_fresh_round(editor) -> None:
    editor.mark_editor_dirty("prompt")
    _save(editor, prompt="Einmal geändert.")
    _save(editor)                                    # next save without typing
    assert editor.saved_prompt == "Einmal geändert."
    assert editor._editor_typed == []
