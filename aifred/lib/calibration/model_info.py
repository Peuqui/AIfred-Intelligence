"""What is known about a model's calibration, per backend: the context it starts with, the layer split over
the cards and that of its speed variant (llama.cpp), the context of its llama-swap entry (vLLM), the context
per RoPE factor (Ollama)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LayerSplit:
    layers: tuple[float, ...]  # --tensor-split: layers per card
    active_cards: int
    total_cards: int  # the cards of the base profile's split (a speed profile names only the cards it uses)
    context: int  # the profile's -c; 0 when it has none


@dataclass(frozen=True)
class RopeContext:
    factor: float
    context: int


@dataclass(frozen=True)
class ModelCalibration:
    context: int  # what the server starts the model with (llama.cpp: the profile, else the cache; vLLM: the entry); 0 = not calibrated (Ollama: rope_contexts)
    cache_context: int  # llama.cpp: the cache's context when it differs from the profile's (calibrate again to sync); else 0
    split: LayerSplit | None
    speed: LayerSplit | None
    rope_contexts: tuple[RopeContext, ...]  # Ollama


def _split(entry: dict | None, total_cards: int = 0) -> LayerSplit | None:
    from .llamaswap_io import parse_tensor_split

    if not entry:
        return None
    layers = parse_tensor_split(entry["full_cmd"])
    if not layers:
        return None
    return LayerSplit(tuple(layers), sum(1 for value in layers if value > 0), total_cards or len(layers),
                      int(entry.get("current_context", 0)))


def _llamacpp(model_id: str) -> ModelCalibration:
    from ..config import LLAMASWAP_CONFIG_PATH
    from ..model_vram_cache import get_llamacpp_calibration
    from .llamaswap_io import parse_llamaswap_config

    # The profile is what llama-swap starts; the cache may lag behind a hand edit or a partial run.
    profiles = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
    profile_context = int(profiles.get(model_id, {}).get("current_context", 0))
    cached = get_llamacpp_calibration(model_id) or 0
    base = _split(profiles.get(model_id))
    # The speed profile holds the real per-card layout; the cache keeps only "card 0 : the rest".
    speed = _split(profiles.get(f"{model_id}-speed"), base.total_cards if base else 0)
    return ModelCalibration(
        context=profile_context or cached,
        cache_context=cached if profile_context and cached and cached != profile_context else 0,
        split=base,
        speed=speed,
        rope_contexts=(),
    )


def _ollama(model_id: str) -> ModelCalibration:
    from ..model_vram_cache import (
        OLLAMA_ROPE_FACTORS,
        get_ollama_calibrated_max_context,
    )

    contexts = ((factor, get_ollama_calibrated_max_context(model_id, rope_factor=factor)) for factor in OLLAMA_ROPE_FACTORS)
    return ModelCalibration(0, 0, None, None, tuple(RopeContext(factor, context) for factor, context in contexts if context is not None))


def _vllm(model_id: str) -> ModelCalibration:
    from ..operating_points import get_vllm_entry_context

    return ModelCalibration(get_vllm_entry_context(model_id), 0, None, None, ())


def model_calibration(backend_type: str, model_id: str) -> ModelCalibration | None:
    """The calibration of ``model_id`` (a base id, no variant suffix); None for a backend that has none
    (the cloud) or no model."""
    readers = {"llamacpp": _llamacpp, "vllm": _vllm, "ollama": _ollama}
    if not model_id or backend_type not in readers:
        return None
    return readers[backend_type](model_id)
