"""The bulk description (``vision_bulk.run_bulk_describe``): who describes is the describer list, asked for every
occurrence; when nobody can, the run stops there instead of failing every other occurrence one by one."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from aifred.lib import vision_bulk, vision_cluster, vision_event_analysis
from aifred.lib.vision_routing import NoVisionModelError


class FakeStore:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events
        self.described: list[str] = []

    def list_events_without_description(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return self.events

    def apply_cluster_description(self, cluster_id: str, text: str, by: str) -> None:
        self.described.append(cluster_id)


@pytest.fixture
def three_occurrences(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    store = FakeStore([{"id": 1}, {"id": 2}, {"id": 3}])
    monkeypatch.setattr(vision_cluster, "cluster_events", lambda events: {e["id"]: f"c{e['id']}" for e in events})
    monkeypatch.setattr(vision_cluster, "write_clusters", lambda _store, _mapping: None)
    return store


def test_the_run_stops_at_the_first_occurrence_nobody_can_describe(three_occurrences: FakeStore, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[list[int]] = []

    async def nobody(member_ids: list[int], **_kwargs: Any) -> str:
        asked.append(member_ids)
        raise NoVisionModelError("Aragon/VL: Aragon does not answer; local: no VLM is set for this machine")

    monkeypatch.setattr(vision_event_analysis, "analyze_cluster_with_vlm", nobody)
    result = asyncio.run(vision_bulk.run_bulk_describe(store=three_occurrences))
    assert asked == [[1]]
    assert result.no_describer == "Aragon/VL: Aragon does not answer; local: no VLM is set for this machine"
    assert (result.total_clusters, result.processed, result.failed) == (3, 0, 0)


def test_any_other_failure_of_one_occurrence_does_not_stop_the_others(three_occurrences: FakeStore, monkeypatch: pytest.MonkeyPatch) -> None:
    async def second_fails(member_ids: list[int], **_kwargs: Any) -> str:
        if member_ids == [2]:
            raise RuntimeError("VLM returned empty response")
        return "a parcel"

    monkeypatch.setattr(vision_event_analysis, "analyze_cluster_with_vlm", second_fails)
    result = asyncio.run(vision_bulk.run_bulk_describe(store=three_occurrences))
    assert three_occurrences.described == ["c1", "c3"]
    assert (result.processed, result.failed, result.no_describer) == (3, 1, "")
