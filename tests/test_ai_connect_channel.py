"""AI-Connect channel: peer allowlist, loop guard and shared-file rendering."""

import pytest

from aifred.plugins.channels import ai_connect_channel as channel


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch) -> None:
    channel._turn_times.clear()
    monkeypatch.setenv("AI_CONNECT_REPLY_LIMIT", "2")
    monkeypatch.setenv("AI_CONNECT_REPLY_WINDOW_MINUTES", "10")


def test_empty_allowlist_blocks_everyone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_CONNECT_ALLOWED_PEERS", "")
    assert not channel._is_peer_allowed("Mini:vllm-research")


def test_allowlist_matches_names_and_patterns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_CONNECT_ALLOWED_PEERS", "Mini:vllm-research, Aragon:*")
    assert channel._is_peer_allowed("Mini:vllm-research")
    assert channel._is_peer_allowed("Aragon:FreeEchoDot2")
    assert not channel._is_peer_allowed("Mini:Agent-Orc")


def test_bare_wildcard_blocks_everyone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_CONNECT_ALLOWED_PEERS", "*")
    assert not channel._is_peer_allowed("Mini:vllm-research")


def test_reply_limit_counts_per_peer() -> None:
    assert not channel._reply_limit_reached("Mini:a")
    assert not channel._reply_limit_reached("Mini:a")
    assert channel._reply_limit_reached("Mini:a")
    assert not channel._reply_limit_reached("Mini:b")


def test_context_excerpt_is_appended() -> None:
    text = channel._with_context("Review?", {"file": "api.py", "lines": "1-2", "excerpt": "x = 1"})
    assert text == "Review?\n\n📎 api.py lines 1-2\n```\nx = 1\n```"
    assert channel._with_context("Hallo", None) == "Hallo"
