"""The system agents and the Automatik LLM over the API (agents page, gap 7): their prompt layers through the same
prompt endpoints as a chat agent's (the Automatik's are the files of prompts/<lang>/automatik/), and the cloud
workflow of the calibration agent: provider, model and reasoning, saved in agents.json. Until 08.11.2026 only the
Reflex editor offered them. agents.json and the prompts folder are temporary ones; no cloud provider is asked."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from aifred.lib import agent_config, prompt_loader


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, Path]]:
    agents = agent_config._default_agents()
    agents["calibration"] = {
        "display_name": "Calibration", "emoji": "📐", "description": "", "role": "system", "color": "#7d8590", "model": "qwen-max",
        "cloud_provider": "qwen", "prompts": {"system": "calibration/system.txt"},
        "toggles": {"personality": False, "reasoning": False, "thinking": False}, "tools": None,
    }
    agents_file = tmp_path / "agents.json"
    agents_file.write_text(json.dumps({"agents": agents}), encoding="utf-8")
    monkeypatch.setattr(agent_config, "AGENTS_FILE", agents_file)
    prompts = tmp_path / "prompts"
    (prompts / "en" / "calibration").mkdir(parents=True)   # the calibration's prompts are English only
    (prompts / "en" / "calibration" / "system.txt").write_text("Calibrate", encoding="utf-8")
    (prompts / "de").mkdir()
    (prompts / "en" / "automatik").mkdir()
    (prompts / "en" / "automatik" / "intent_detection.txt").write_text("Who is addressed?", encoding="utf-8")
    (prompts / "en" / "automatik" / "url_ranking.txt").write_text("Rank these.", encoding="utf-8")
    monkeypatch.setattr(prompt_loader, "PROMPTS_DIR", prompts)
    yield agents_file, prompts


@pytest.fixture
def client(world: tuple[Path, Path]) -> Iterator[TestClient]:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import USERNAME_COOKIE_NAME, sign_username

    with TestClient(api_app) as test_client:
        test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        yield test_client


def _saved(agents_file: Path, agent: str) -> dict[str, Any]:
    entry: dict[str, Any] = json.loads(agents_file.read_text(encoding="utf-8"))["agents"][agent]
    return entry


def test_the_list_holds_the_system_agents_and_the_automatik(client: TestClient) -> None:
    cards = {card["id"]: card for card in client.get("/agents/system").json()}
    assert list(cards) == ["vision", "calibration", "automatik"]
    assert cards["automatik"]["prompt_keys"] == ["intent_detection", "url_ranking"]
    assert cards["automatik"]["languages"] == ["en"]            # it is always asked in English
    assert cards["calibration"]["languages"] == ["en"]          # its files exist in English only
    assert cards["calibration"]["prompt_keys"] == ["system"]
    assert cards["vision"]["cloud"] is None and cards["automatik"]["cloud"] is None
    cloud = cards["calibration"]["cloud"]
    assert (cloud["provider"], cloud["model"], cloud["reasoning"]) == ("qwen", "qwen-max", False)
    assert "qwen" in [provider["id"] for provider in cloud["providers"]]


def test_the_prompts_of_a_system_agent_and_of_the_automatik_are_read_and_written(
    client: TestClient, world: tuple[Path, Path],
) -> None:
    _agents_file, prompts = world
    assert client.get("/agents/calibration/prompts/en/system").json() == {"text": "Calibrate", "exists": True}
    assert client.get("/agents/automatik/prompts/en/url_ranking").json() == {"text": "Rank these.", "exists": True}
    assert client.get("/agents/automatik/prompts/de/url_ranking").json() == {"text": "", "exists": False}
    client.put("/agents/automatik/prompts/en/intent_detection", json={"text": "Who is meant?"})
    assert (prompts / "en" / "automatik" / "intent_detection.txt").read_text(encoding="utf-8") == "Who is meant?"
    assert client.get("/agents/automatik/prompts/en/elsewhere").status_code == 404
    assert client.get("/agents/nobody/prompts/en/identity").status_code == 404


def test_the_cloud_choice_is_saved_and_only_for_a_cloud_workflow(client: TestClient, world: tuple[Path, Path]) -> None:
    agents_file, _prompts = world
    card = client.patch("/agents/system/calibration/cloud", json={"model": "qwen-plus", "reasoning": True}).json()
    assert (card["cloud"]["model"], card["cloud"]["reasoning"]) == ("qwen-plus", True)
    saved = _saved(agents_file, "calibration")
    assert saved["model"] == "qwen-plus" and saved["toggles"] == {"personality": False, "reasoning": True, "thinking": False}
    assert client.patch("/agents/system/calibration/cloud", json={"provider": "nirgendwo"}).status_code == 422
    assert client.patch("/agents/system/vision/cloud", json={"model": "x"}).status_code == 404
    assert client.patch("/agents/system/aifred/cloud", json={"model": "x"}).status_code == 404
    assert client.get("/agents/system/calibration/cloud/models", params={"provider": "nirgendwo"}).status_code == 422
