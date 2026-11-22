"""The server watches this machine's hold itself and pushes every change to the connected pages (kind ``local_hold``)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fake_gpu_orc import FakeGpuOrc, install_fake_gpu_orc

from aifred.lib import gpu_hold
from aifred.lib.gpu_hold import LOCAL_HOLD_KIND, push_local_hold_if_changed

HOLD = {"since": "2026-11-20T09:30:00+01:00", "reason": "vllm-research: v13", "owner": "vllm-research"}


@pytest.fixture
def orc(monkeypatch: pytest.MonkeyPatch) -> FakeGpuOrc:
    fake = FakeGpuOrc()
    install_fake_gpu_orc(monkeypatch, fake)
    return fake


@pytest.fixture
def pushed(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, dict[str, Any]]]:
    """Every ``browser_push`` of the watcher as (session, kind, fields); two pages are connected."""
    calls: list[tuple[str, str, dict[str, Any]]] = []
    monkeypatch.setattr(gpu_hold.session_events, "sessions_with_subscribers", lambda: ["s1", "s2"])
    monkeypatch.setattr(gpu_hold, "browser_push", lambda session_id, kind, **fields: calls.append((session_id, kind, fields)))
    return calls


def test_a_change_of_the_hold_is_pushed_once_to_every_connected_page(orc: FakeGpuOrc, pushed: list[tuple[str, str, dict[str, Any]]]) -> None:
    last = asyncio.run(push_local_hold_if_changed(None))
    assert [(session, kind, fields["state"]) for session, kind, fields in pushed] == [("s1", LOCAL_HOLD_KIND, "free"), ("s2", LOCAL_HOLD_KIND, "free")]
    pushed.clear()
    orc.held = dict(HOLD)
    last = asyncio.run(push_local_hold_if_changed(last))
    assert [(session, fields["state"], fields["owner"]) for session, _, fields in pushed] == [
        ("s1", "active", "vllm-research"), ("s2", "active", "vllm-research"),
    ]
    assert last is not None and last.state == "active"


def test_an_unchanged_hold_pushes_nothing(orc: FakeGpuOrc, pushed: list[tuple[str, str, dict[str, Any]]]) -> None:
    last = asyncio.run(push_local_hold_if_changed(None))
    pushed.clear()
    assert asyncio.run(push_local_hold_if_changed(last)) == last
    assert pushed == []


def test_without_a_connected_page_gpu_orc_is_not_asked(orc: FakeGpuOrc, monkeypatch: pytest.MonkeyPatch, pushed: list[tuple[str, str, dict[str, Any]]]) -> None:
    monkeypatch.setattr(gpu_hold.session_events, "sessions_with_subscribers", list)
    assert asyncio.run(push_local_hold_if_changed(None)) is None
    assert pushed == [] and orc.requests == []


def test_the_newest_hold_replaces_older_ones_in_the_replay_buffer() -> None:
    from aifred.lib.browser_push import LATEST_WINS_KINDS
    assert LOCAL_HOLD_KIND in LATEST_WINS_KINDS
