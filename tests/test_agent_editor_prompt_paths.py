"""The prompt editor reads and writes only files under prompts/<lang>/.

07.11.2026: tab and language are event arguments from the client and went straight into the path
(``PROMPTS_DIR / lang / "automatik" / f"{tab}.txt"``); with ``../`` any .txt file was readable and
writable. The agents' prompt paths come from agents.json, which a bundle import fills.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from aifred.lib import agent_config, prompt_loader
from aifred.state._agent_editor_mixin import AgentEditorMixin as M

if TYPE_CHECKING:
    # For mypy the stand-in is the mixin, so its fields and borrowed methods are checked against the
    # real ones. At runtime it stays a plain class: a Reflex state refuses undeclared attributes.
    _StateBase = M
else:
    _StateBase = object


class _Editor(_StateBase):
    _editor_prompt_file = M._editor_prompt_file
    _load_editor_prompt = M._load_editor_prompt
    _save_editor_prompt_to_disk = M._save_editor_prompt_to_disk

    def __init__(self, agent_id: str, lang: str, tab: str) -> None:
        self.editor_agent_id = agent_id
        self.editor_prompt_lang = lang
        self.editor_prompt_tab = tab
        self._editor_prompt_content = ""


@pytest.fixture
def prompts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "prompts"
    (root / "de" / "automatik").mkdir(parents=True)
    (root / "de" / "automatik" / "intent.txt").write_text("Absicht")
    (root / "de" / "aifred").mkdir()
    (root / "de" / "aifred" / "identity.txt").write_text("Du bist AIfred.")
    (root / "en").mkdir()
    (tmp_path / "secret.txt").write_text("geheim")
    monkeypatch.setattr(prompt_loader, "PROMPTS_DIR", root)
    configs = {
        "aifred": SimpleNamespace(prompts={"identity": "aifred/identity.txt"}),
        "evil": SimpleNamespace(prompts={"identity": "../../secret.txt"}),
    }
    monkeypatch.setattr(agent_config, "get_agent_config", configs.get)
    return root


@pytest.mark.parametrize(("agent_id", "lang", "tab"), [
    ("automatik", "de", "../../secret"),
    ("automatik", "..", "intent"),
    ("automatik", "../..", "secret"),
    ("automatik", "", "intent"),
    ("evil", "de", "identity"),
])
def test_a_path_outside_prompts_gets_no_file(prompts: Path, agent_id: str, lang: str, tab: str) -> None:
    editor = _Editor(agent_id, lang, tab)
    assert editor._editor_prompt_file(tab, lang) is None
    editor._load_editor_prompt(tab)
    assert "geheim" not in editor._editor_prompt_content


def test_saving_with_a_bad_tab_writes_nothing(prompts: Path) -> None:
    editor = _Editor("automatik", "de", "../../secret")
    editor._editor_prompt_content = "überschrieben"
    editor._save_editor_prompt_to_disk()
    assert (prompts.parent / "secret.txt").read_text() == "geheim"


def test_the_editor_reads_and_writes_its_own_prompts(prompts: Path) -> None:
    editor = _Editor("aifred", "de", "identity")
    editor._load_editor_prompt("identity")
    assert editor._editor_prompt_content == "Du bist AIfred."
    editor._editor_prompt_content = "Du bist AIfred, der Butler."
    editor._save_editor_prompt_to_disk()
    assert (prompts / "de" / "aifred" / "identity.txt").read_text() == "Du bist AIfred, der Butler."


def test_a_prompt_only_in_the_other_language_is_shown_with_a_hint(prompts: Path) -> None:
    editor = _Editor("automatik", "en", "intent")
    editor._load_editor_prompt("intent")
    assert editor._editor_prompt_content == "[DE only]\n\nAbsicht"
