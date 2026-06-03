"""Tests for keyframe selection in the cluster sequence describe path.

Covers _select_keyframes (pure, no VLM/disk): temporal spread + change-point
preference. The VLM call itself is exercised elsewhere / live."""

from __future__ import annotations

from datetime import datetime, timedelta

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
