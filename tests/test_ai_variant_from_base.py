"""_ai_variant_from_base: AI calibration of a variant cell on the active set.

Covers the index-fragile parts: GPU/reserve/seed slicing by the active set
(= hard speed lock), the result-split mapping (active >0 values only), and
the feasibility gate that skips impossible speed sets without AI probes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import aifred.lib.calibration.ai_agent as ai_agent
import aifred.lib.calibration.flow as flow
from aifred.lib.calibration.types import Budget, GPU, Model


def _gpu(uuid: str, name: str, cc: float, total: int) -> GPU:
    return GPU(uuid=uuid, name=name, compute_cap=cc, total_mb=total,
               free_mb=total, speed_class=0, first_in_class=True)


def _model(size_mb: float) -> Model:
    return Model(model_id="m", gguf_path=Path("/x.gguf"), native_context=262144,
                 total_layers=60, size_mb=size_mb, mb_per_layer=size_mb / 60,
                 quantization="IQ3_XXS")


def _budget(reserve: tuple[int, ...]) -> Budget:
    return Budget(per_gpu_free=(0,) * len(reserve), first_gpu_handicap=0,
                  safety_margin=192, gpu_reserve_mb=reserve)


def _drain(gen) -> list[str]:
    async def run() -> list[str]:
        return [x async for x in gen]
    return asyncio.run(run())


def _mock_ai(monkeypatch, ai_result_split: str, capture: dict):
    """Patch calibrate_with_ai to record kwargs and emit a fixed result."""
    async def fake(**kwargs):
        capture.update(kwargs)
        yield "scouting…"
        yield f"__AI_RESULT__:188000:{ai_result_split}:reasoning here"
    monkeypatch.setattr(ai_agent, "calibrate_with_ai", fake)


def test_normal_variant_all_gpus(monkeypatch):
    # 3 GPUs, all active in base_split → AI sees all 3, result maps straight.
    gpus = [_gpu("g0", "RTX 8000", 7.5, 49152), _gpu("g1", "RTX 8000", 7.5, 49152),
            _gpu("g2", "V100", 7.0, 32768)]
    cap: dict = {}
    _mock_ai(monkeypatch, "20,20,18", cap)
    out = _drain(flow._ai_variant_from_base(
        model=_model(40000), gguf_path=Path("/x.gguf"), full_cmd="--model x --port 1",
        gpus=gpus, active=[0, 1, 2], base_split=(20.0, 20.0, 18.0), base_ctx=262144,
        base_kv="f16", budget=_budget((0, 0, 6000)), port=1, env={},
        known_thinking=True,
    ))
    result = [ln for ln in out if ln.startswith("__RESULT__:")][-1]
    # 8th field: active-GPU UUIDs, parallel to the split values (the mixin
    # passes them 1:1 as CUDA_VISIBLE_DEVICES to the YAML variant writers).
    assert result == "__RESULT__:188000:99:gpu:thinks:f16:20,20,18:3:g0,g1,g2"
    assert len(cap["gpus"]) == 3  # all cards handed to the AI
    assert cap["reserve_mb"] == (0, 0, 6000)


def test_speed_variant_locks_to_active_subset(monkeypatch):
    # base_split deactivates GPU2 (speed) → AI must only see the 2 fast cards.
    gpus = [_gpu("g0", "RTX 8000", 7.5, 49152), _gpu("g1", "RTX 8000", 7.5, 49152),
            _gpu("g2", "V100", 7.0, 32768)]
    cap: dict = {}
    _mock_ai(monkeypatch, "22,22", cap)
    out = _drain(flow._ai_variant_from_base(
        model=_model(40000), gguf_path=Path("/x.gguf"), full_cmd="--model x --port 1",
        gpus=gpus, active=[0, 1], base_split=(22.0, 22.0, 0.0), base_ctx=262144,
        base_kv="f16", budget=_budget((0, 0, 6000)), port=1, env={},
        known_thinking=False,
    ))
    result = [ln for ln in out if ln.startswith("__RESULT__:")][-1]
    assert result == "__RESULT__:188000:99:gpu:nothink:f16:22,22:2:g0,g1"
    # Hard lock: only the 2 active GPUs reach the AI, reserve sliced to match.
    assert [g.uuid for g in cap["gpus"]] == ["g0", "g1"]
    assert cap["reserve_mb"] == (0, 0)
    assert cap["seed_split"] == [22.0, 22.0]


def test_infeasible_speed_set_skips_ai(monkeypatch):
    # Active set is a single 24 GB P40 but the model weighs 100 GB → gate
    # fires, AI is never called.
    gpus = [_gpu("g0", "P40", 6.1, 24576), _gpu("g1", "P40", 6.1, 24576)]
    called = {"ai": False}
    async def fake(**kwargs):
        called["ai"] = True
        yield "should not run"
    monkeypatch.setattr(ai_agent, "calibrate_with_ai", fake)
    out = _drain(flow._ai_variant_from_base(
        model=_model(100000), gguf_path=Path("/x.gguf"), full_cmd="--model x --port 1",
        gpus=gpus, active=[0], base_split=(1.0, 0.0), base_ctx=262144,
        base_kv="f16", budget=_budget((0, 0)), port=1, env={},
        known_thinking=True,
    ))
    assert called["ai"] is False
    assert out[-1] == "__RESULT__:0:0:error"


def test_as_speed_emits_full_split_sentinel(monkeypatch):
    # Speed: 2 of 3 GPUs active. The AI returns a split for the 2 active
    # cards ("20,20"); __SPEED__ must carry the FULL colon split with 0 for
    # the inactive GPU, so the mixin's parser keeps the right CUDA order.
    gpus = [_gpu("g0", "RTX 8000", 7.5, 49152), _gpu("g1", "RTX 8000", 7.5, 49152),
            _gpu("g2", "V100", 7.0, 32768)]
    cap: dict = {}
    _mock_ai(monkeypatch, "20,20", cap)
    out = _drain(flow._ai_variant_from_base(
        model=_model(40000), gguf_path=Path("/x.gguf"), full_cmd="--model x --port 1",
        gpus=gpus, active=[0, 1], base_split=(22.0, 22.0, 0.0), base_ctx=262144,
        base_kv="f16", budget=_budget((0, 0, 0)), port=1, env={},
        known_thinking=True, as_speed=True,
    ))
    speed = [ln for ln in out if ln.startswith("__SPEED__:")][-1]
    # 5th comma-element: UUIDs of the ACTIVE GPUs (parallel to the split) —
    # the mixin passes them as CUDA_VISIBLE_DEVICES so env and tensor-split
    # can never desync in the YAML variant writers.
    assert speed == "__SPEED__:20:20:0,188000,2,f16,g0,g1"
    assert not any(ln.startswith("__RESULT__:") for ln in out)  # speed, not base


def test_ai_error_no_fallback(monkeypatch):
    gpus = [_gpu("g0", "RTX 8000", 7.5, 49152), _gpu("g1", "RTX 8000", 7.5, 49152)]
    async def fake(**kwargs):
        yield "__AI_ERROR__:no GPU-only fit"
    monkeypatch.setattr(ai_agent, "calibrate_with_ai", fake)
    out = _drain(flow._ai_variant_from_base(
        model=_model(40000), gguf_path=Path("/x.gguf"), full_cmd="--model x --port 1",
        gpus=gpus, active=[0, 1], base_split=(20.0, 20.0), base_ctx=262144,
        base_kv="f16", budget=_budget((0, 0)), port=1, env={},
        known_thinking=True,
    ))
    assert out[-1] == "__RESULT__:0:0:error"  # no algorithmic fallback
