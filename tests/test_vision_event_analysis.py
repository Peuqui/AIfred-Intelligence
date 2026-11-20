"""Tests for keyframe selection in the cluster sequence describe path.

Covers _select_keyframes (pure, no VLM/disk): temporal spread + change-point
preference. The VLM call itself is exercised elsewhere / live."""

from __future__ import annotations

from datetime import datetime, timedelta

import asyncio
from types import SimpleNamespace

from aifred.lib import vision_event_analysis, vision_prewarm, vision_routing
from aifred.lib.vision_event_analysis import _select_keyframes

_T0 = datetime(2026, 6, 3, 19, 0, 0)


def _items(specs):
    """specs: list of (offset_seconds, phash) → keyframe tuples."""
    return [
        (_T0 + timedelta(seconds=off), ph, b"jpeg", "cam/x")
        for off, ph in specs
    ]


def test_returns_all_when_within_cap():
    its = _items([(0, 1), (1, 2), (2, 3)])
    assert _select_keyframes(its, 8) == its


def test_caps_and_keeps_temporal_order():
    its = _items([(i, 0) for i in range(20)])  # 20 frames, identical phash
    sel = _select_keyframes(its, 5)
    assert len(sel) <= 5
    ts = [s[0] for s in sel]
    assert ts == sorted(ts)          # time order preserved
    assert ts[0] == its[0][0]        # coverage starts at the first frame


def test_spread_across_time_bins():
    # 20 frames over 19s, cap 5 → roughly one per ~3.8s bin, not bunched up.
    its = _items([(i, 0) for i in range(20)])
    sel = _select_keyframes(its, 5)
    offsets = [(s[0] - _T0).total_seconds() for s in sel]
    assert offsets[0] < 5 and offsets[-1] > 14  # early and late both covered


def test_prefers_change_point_within_bin():
    # A single very different frame in the data → it must be picked, not a
    # bland neighbour (max pHash distance to the previously kept frame).
    specs = [(i, 0x0) for i in range(10)]
    specs[6] = (6, 0xFFFFFFFFFFFFFFFF)  # the "something happened" frame
    sel = _select_keyframes(_items(specs), 5)
    assert any(ph == 0xFFFFFFFFFFFFFFFF for _ts, ph, _b, _s in sel)


def test_max_frames_zero_is_empty():
    assert _select_keyframes(_items([(0, 1), (1, 2)]), 0) == []


def test_the_describing_target_follows_the_vision_llm_of_the_main_settings(monkeypatch):
    """The cluster/bulk description asks for the image model of this machine: the Vision-LLM of settings.json
    (no second choice in the vision settings)."""
    asked: list[str] = []

    def describer(model: str, *, explicit: bool):
        asked.append(model)
        return vision_routing.Describer(f"{model}-visiond", evicts_chat_model=False)

    async def describe_via(pick, _run):
        pick()
        return SimpleNamespace(text="a person at the door", model="m")

    monkeypatch.setattr(vision_prewarm, "is_vision_active", lambda: True)
    monkeypatch.setattr(vision_routing, "settings_model", lambda role: "Vision-LLM-27B" if role == "vision" else "")
    monkeypatch.setattr(vision_routing, "camera_describer", describer)
    monkeypatch.setattr(vision_routing, "describe_via", describe_via)
    monkeypatch.setattr(vision_event_analysis, "build_vlm_prompt", lambda *args, **kwargs: "p")
    frame = SimpleNamespace(image_bytes=b"x")
    text, _model = asyncio.run(vision_event_analysis.analyze_frames_with_vlm(
        [frame], base_prompt="p", source_id="cam/x", explicit=False))
    assert asked == ["Vision-LLM-27B"] and text == "a person at the door"


def test_without_a_vision_llm_the_describer_list_still_describes(monkeypatch):
    """No Vision-LLM set: the entry "this machine" has no model, but another machine of the list can still
    describe — the camera paths ask the list instead of refusing beforehand (they raised "no VLM model
    configured" before the list was asked)."""
    asked: list[str] = []

    def describer(model: str, *, explicit: bool):
        asked.append(model)
        return vision_routing.Describer("Aragon/Remote-VL-visiond", evicts_chat_model=False)

    async def describe_via(pick, _run):
        pick()
        return SimpleNamespace(text="a parcel on the step", model="Aragon/Remote-VL-visiond")

    monkeypatch.setattr(vision_prewarm, "is_vision_active", lambda: True)
    monkeypatch.setattr(vision_routing, "settings_model", lambda role: "")
    monkeypatch.setattr(vision_routing, "camera_describer", describer)
    monkeypatch.setattr(vision_routing, "describe_via", describe_via)
    monkeypatch.setattr(vision_event_analysis, "build_vlm_prompt", lambda *args, **kwargs: "p")
    frame = SimpleNamespace(image_bytes=b"x")
    text, model = asyncio.run(vision_event_analysis.analyze_frames_with_vlm(
        [frame], base_prompt="p", source_id="cam/x", explicit=False))
    assert asked == [""] and text == "a parcel on the step" and model == "Aragon/Remote-VL-visiond"
