"""_pre_search_max_ctx must rebuild the seed split from each GPU's
reserve-reduced usable VRAM when a side-channel reserve (TTS/VLM) is set.

Without this the fixed base split overloads the reserved card at any ctx —
the bug that made the TTS variant fail where the classic algorithm's
re-balanced split fits at native ctx.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import aifred.lib.calibration.ai_agent as ai_agent
import aifred.lib.calibration.projection as proj
from aifred.lib.calibration.types import GPU


def _gpu(uuid: str, name: str, total: int) -> GPU:
    return GPU(uuid=uuid, name=name, compute_cap=7.0, total_mb=total,
               free_mb=total, speed_class=0, first_in_class=True)


class _Point:
    def __init__(self, free):
        self.per_gpu_free_mb = free


def _run(reserve):
    gpus = [_gpu("g0", "RTX 8000", 49152), _gpu("g1", "RTX 8000", 49152),
            _gpu("g2", "V100", 32768)]

    async def fake_project(cmd, gguf, ctx, **kw):
        # Plenty free so native ctx "fits" → returns the seed split directly.
        return _Point((10000, 10000, 10000))

    async def go():
        return await ai_agent._pre_search_max_ctx(
            full_cmd="--model x --port 1", gguf_path=Path("/x.gguf"),
            gpus=gpus, safety_margin_mb=192, native_ctx=262144,
            initial_split=[49152.0, 49152.0, 32768.0], reserve_mb=reserve,
        )

    import pytest
    monkey = pytest.MonkeyPatch()
    monkey.setattr(proj, "project", fake_project)
    try:
        return _run_async(go)
    finally:
        monkey.undo()


def _run_async(coro_fn):
    return asyncio.run(coro_fn())


def test_reserve_rebuilds_split_capacity_proportional():
    ctx, split, _log = _run(reserve=(0, 0, 6536))
    assert ctx == 262144
    # V100 share drops by exactly its reserve; the others are untouched.
    assert split == [49152.0, 49152.0, 32768.0 - 6536.0]


def test_no_reserve_keeps_seed_split():
    ctx, split, _log = _run(reserve=(0, 0, 0))
    assert ctx == 262144
    assert split == [49152.0, 49152.0, 32768.0]
