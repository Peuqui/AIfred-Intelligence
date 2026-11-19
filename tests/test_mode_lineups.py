"""The line-up of every discussion mode (lib/mode_lineups.py): each mode remembers who sits where, the last choice
wins (in the session and globally), a session entering a mode takes the global line-up, there are no defaults, and
scripts/migrate_mode_lineups.py brings the old session files over."""

import importlib.util
import json
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from aifred.lib import session_storage
from aifred.lib.mode_lineups import apply_config_changes, current_author, public_config
from aifred.lib.settings import persisted_settings
from aifred.lib.user_error import Invalid

FIRST = "a" * 32
SECOND = "b" * 32
THIRD = "c" * 32


@pytest.fixture
def sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(session_storage, "SESSION_DIR", tmp_path)
    session_storage._session_meta_cache.clear()
    for session in (FIRST, SECOND, THIRD):
        session_storage.create_empty_session(session, owner="ki")
    yield
    session_storage._session_meta_cache.clear()


def _public(session: str) -> dict[str, Any]:
    return public_config(session_storage.get_session_config(session))


def test_a_new_session_has_no_line_up_at_all(sessions: None) -> None:
    assert persisted_settings()["last_lineups"] == {} and "debate_seats" not in persisted_settings()
    assert _public(FIRST) == {
        "active_agent": "aifred", "multi_agent_mode": "standard", "symposion_agents": [], "incognito": False,
        "interjections": True, "debate_seats": {},
    }
    apply_config_changes(FIRST, {"multi_agent_mode": "recensio"})
    shown = _public(FIRST)
    assert (shown["active_agent"], shown["debate_seats"]) == (None, {})           # nobody: nothing is made up
    apply_config_changes(FIRST, {"multi_agent_mode": "symposion"})
    assert _public(FIRST)["symposion_agents"] == [] and _public(FIRST)["active_agent"] is None


def test_the_critic_of_one_mode_does_not_change_the_critic_of_another(sessions: None) -> None:
    apply_config_changes(FIRST, {"multi_agent_mode": "critical_review", "active_agent": "aifred", "debate_seats": {"critic": "sokrates"}})
    apply_config_changes(FIRST, {"multi_agent_mode": "recensio", "active_agent": "salomo", "debate_seats": {"critic": "salomo"}})
    recensio = _public(FIRST)
    assert (recensio["active_agent"], recensio["debate_seats"]) == ("salomo", {"critic": "salomo"})
    apply_config_changes(FIRST, {"multi_agent_mode": "critical_review"})
    review = _public(FIRST)
    assert (review["active_agent"], review["debate_seats"]) == ("aifred", {"critic": "sokrates"})
    apply_config_changes(FIRST, {"multi_agent_mode": "tribunal"})
    assert _public(FIRST)["debate_seats"] == {}                                    # never chosen there: empty


def test_an_update_applies_to_the_mode_it_switches_to(sessions: None) -> None:
    apply_config_changes(FIRST, {"multi_agent_mode": "tribunal", "debate_seats": {"critic": "sokrates", "judge": "salomo"}})
    assert _public(FIRST)["debate_seats"] == {"critic": "sokrates", "judge": "salomo"}
    assert session_storage.get_session_config(FIRST)["lineups"] == {"tribunal": {"critic": "sokrates", "judge": "salomo"}}


def test_the_last_choice_wins_for_a_session_that_enters_the_mode_and_for_a_new_one(sessions: None) -> None:
    apply_config_changes(FIRST, {"multi_agent_mode": "recensio", "active_agent": "sokrates", "debate_seats": {"critic": "salomo"}})
    assert persisted_settings()["last_lineups"] == {"recensio": {"main": "sokrates", "critic": "salomo"}}
    apply_config_changes(SECOND, {"multi_agent_mode": "recensio"})                 # a session with no line-up of its own
    assert (_public(SECOND)["active_agent"], _public(SECOND)["debate_seats"]) == ("sokrates", {"critic": "salomo"})
    apply_config_changes(SECOND, {"multi_agent_mode": "standard"})
    assert _public(SECOND)["active_agent"] == "aifred"
    apply_config_changes(SECOND, {"multi_agent_mode": "recensio", "debate_seats": {"critic": "sokrates"}})   # the last choice
    apply_config_changes(THIRD, {"multi_agent_mode": "recensio"})                  # a new session
    assert _public(THIRD)["debate_seats"] == {"critic": "sokrates"} and _public(THIRD)["active_agent"] == "sokrates"


def test_a_session_keeps_its_own_line_up_when_another_one_chooses(sessions: None) -> None:
    apply_config_changes(FIRST, {"multi_agent_mode": "recensio", "debate_seats": {"critic": "sokrates"}})
    apply_config_changes(SECOND, {"multi_agent_mode": "recensio", "debate_seats": {"critic": "salomo"}})
    apply_config_changes(FIRST, {"multi_agent_mode": "standard"})
    apply_config_changes(FIRST, {"multi_agent_mode": "recensio"})                  # reopened as it was left
    assert _public(FIRST)["debate_seats"] == {"critic": "sokrates"}


def test_the_symposion_list_is_a_line_up_too(sessions: None) -> None:
    apply_config_changes(FIRST, {"multi_agent_mode": "symposion", "symposion_agents": ["sokrates", "salomo"]})
    apply_config_changes(SECOND, {"multi_agent_mode": "symposion"})
    assert _public(SECOND)["symposion_agents"] == ["sokrates", "salomo"]
    apply_config_changes(SECOND, {"symposion_agents": ["aifred"]})
    assert _public(FIRST)["symposion_agents"] == ["sokrates", "salomo"]


def test_the_standard_agent_comes_back_after_a_debate_mode(sessions: None) -> None:
    apply_config_changes(FIRST, {"active_agent": "sokrates"})
    apply_config_changes(FIRST, {"multi_agent_mode": "recensio", "active_agent": "salomo"})
    assert current_author(session_storage.get_session_config(FIRST)) == "salomo"
    apply_config_changes(FIRST, {"multi_agent_mode": "standard"})
    assert _public(FIRST)["active_agent"] == "sokrates"
    assert persisted_settings()["last_lineups"] == {"recensio": {"main": "salomo"}}     # standard is no line-up


def test_a_place_the_mode_does_not_have_is_refused_and_nothing_is_stored(sessions: None) -> None:
    apply_config_changes(FIRST, {"multi_agent_mode": "recensio", "debate_seats": {"critic": "sokrates"}})
    before = session_storage.get_session_config(FIRST)["lineups"]
    for changes in ({"debate_seats": {"judge": "salomo"}}, {"symposion_agents": ["salomo"]},
                    {"debate_seats": {"critic": "vision"}}, {"active_agent": "gibt-es-nicht"}, {"multi_agent_mode": "devils_advocate"}):
        with pytest.raises(Invalid):
            apply_config_changes(FIRST, changes)
    assert session_storage.get_session_config(FIRST)["lineups"] == before
    assert persisted_settings()["last_lineups"] == {"recensio": {"critic": "sokrates"}}
    apply_config_changes(FIRST, {"multi_agent_mode": "standard"})
    with pytest.raises(Invalid):
        apply_config_changes(FIRST, {"debate_seats": {"critic": "sokrates"}})


# ── scripts/migrate_mode_lineups.py ──────────────────────────────────────


def _script() -> ModuleType:
    path = Path(__file__).resolve().parent.parent / "scripts" / "migrate_mode_lineups.py"
    spec = importlib.util.spec_from_file_location("migrate_mode_lineups", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _old_session(directory: Path, name: str, **config: Any) -> Path:
    path = directory / f"{name}.json"
    path.write_text(json.dumps({"data": {"config": {"active_agent": "aifred", "multi_agent_mode": "standard", "incognito": False,
                                                    "interjections": True, **config}}}), encoding="utf-8")
    return path


def _config(path: Path) -> dict[str, Any]:
    config: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))["data"]["config"]
    return config


def test_the_migration_script_converts_old_sessions_after_a_backup(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    debate = _old_session(tmp_path, "debate", multi_agent_mode="recensio", active_agent="codine",
                          debate_seats={"critic": "hal", "judge": "x"}, symposion_agents=["hal", "codine"])
    standard = _old_session(tmp_path, "standard", debate_seats={"critic": "hal"}, symposion_agents=[])
    empty = _old_session(tmp_path, "empty", debate_seats={}, symposion_agents=[])
    current = _old_session(tmp_path, "current", lineups={"tribunal": {"main": "aifred"}})
    originals = {path: path.read_text(encoding="utf-8") for path in (debate, standard, empty, current)}
    script = _script()

    assert script.migrate_directory(tmp_path, dry_run=True) == 3
    assert {path: path.read_text(encoding="utf-8") for path in originals} == originals        # a dry run writes nothing
    assert list(tmp_path.glob("*.bak-lineups")) == []

    assert script.migrate_directory(tmp_path, dry_run=False) == 3
    assert _config(debate) == {
        "active_agent": "codine", "multi_agent_mode": "recensio", "incognito": False, "interjections": True,
        "lineups": {"recensio": {"main": "codine", "critic": "hal"}, "symposion": {"agents": ["hal", "codine"]}},
    }
    assert _config(standard)["lineups"] == {} and "debate_seats" not in _config(standard)      # the seats belonged to no mode
    assert _config(empty)["lineups"] == {} and "symposion_agents" not in _config(empty)
    assert current.read_text(encoding="utf-8") == originals[current]                           # nothing to do
    assert {path.name for path in tmp_path.glob("*.bak-lineups")} == {"debate.json.bak-lineups", "standard.json.bak-lineups", "empty.json.bak-lineups"}
    assert (tmp_path / "debate.json.bak-lineups").read_text(encoding="utf-8") == originals[debate]
    assert script.migrate_directory(tmp_path, dry_run=False) == 0                              # a second run finds nothing
    assert "line-up of recensio" in capsys.readouterr().out


def test_the_migration_keeps_the_last_choice_of_each_mode_for_new_sessions(tmp_path: Path) -> None:
    import os

    from aifred.lib.settings import persisted_settings

    older = _old_session(tmp_path, "older", lineups={"recensio": {"main": "sokrates", "critic": "salomo"}})
    newer = _old_session(tmp_path, "newer", lineups={"recensio": {"main": "codine", "critic": "hal"},
                                                     "symposion": {"agents": ["hal", "codine"]}})
    os.utime(older, (1_000_000, 1_000_000))
    os.utime(newer, (2_000_000, 2_000_000))
    script = _script()
    expected = {"recensio": {"main": "codine", "critic": "hal"}, "symposion": {"agents": ["hal", "codine"]}}
    assert script.seed_last_lineups(tmp_path, dry_run=True) == expected
    assert persisted_settings()["last_lineups"] == {}                         # a dry run writes nothing
    script.seed_last_lineups(tmp_path, dry_run=False)
    assert persisted_settings()["last_lineups"] == expected                  # the session used last wins
