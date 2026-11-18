"""agents.json is written atomically: a save that fails midway leaves the old file whole."""
import json
from pathlib import Path

import pytest

from aifred.lib import agent_config


@pytest.fixture
def agents_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "agents.json"
    monkeypatch.setattr(agent_config, "AGENTS_FILE", path)
    monkeypatch.setattr(agent_config, "DATA_DIR", tmp_path)
    return path


def test_a_failed_save_leaves_the_old_file_whole(agents_file: Path) -> None:
    agent_config.save_agents_raw({"alt": {"display_name": "Alt"}})
    before = agents_file.read_text(encoding="utf-8")
    # json.dump has written the first entry when it meets the second: a crash mid-write
    broken: dict[str, dict[str, object]] = {"neu": {"display_name": "Neu"}, "kaputt": {"display_name": object()}}
    with pytest.raises(TypeError):
        agent_config.save_agents_raw(broken)
    assert agents_file.read_text(encoding="utf-8") == before
    assert list(agents_file.parent.iterdir()) == [agents_file]  # no temporary file stays


def test_a_save_replaces_the_content(agents_file: Path) -> None:
    agent_config.save_agents_raw({"alt": {"display_name": "Alt"}})
    agent_config.save_agents_raw({"neu": {"display_name": "Neü"}})
    assert json.loads(agents_file.read_text(encoding="utf-8")) == {"agents": {"neu": {"display_name": "Neü"}}}
