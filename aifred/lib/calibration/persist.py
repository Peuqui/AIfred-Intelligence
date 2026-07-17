"""Phase D: YAML-Config-Writer + persistenter Kalibrier-Cache."""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

from ..formatting import format_number
from ..model_vram_cache import add_llamacpp_calibration
from . import llamaswap_io as io
from .reporting import _active_uuid_csv, _split_str
from .types import GPU, Model, Result


async def _write_base_config(
    config_path: Path, model_id: str, result: Result, gpus: list[GPU],
) -> AsyncIterator[str]:
    io.update_llamaswap_context(config_path, model_id, result.context)
    io.update_llamaswap_ngl(config_path, model_id, result.ngl)
    io.update_llamaswap_tensor_split(
        config_path, model_id, list(result.tensor_split),
    )
    # Result.tensor_split is parallel to the GPU list at calibration
    # time. Map back to UUIDs so the config pin is hardware-stable.
    all_uuids = [g.uuid for g in gpus]
    active_uuids = [
        gpus[i].uuid for i, v in enumerate(result.tensor_split)
        if i < len(gpus) and v > 0
    ]
    io.update_llamaswap_cuda_visible(
        config_path, model_id, active_uuids, all_uuids,
    )
    if result.kv_quant != "f16":
        io.update_llamaswap_kv_cache_quant(
            config_path, model_id, result.kv_quant,
        )
    else:
        io.remove_llamaswap_kv_cache_quant(config_path, model_id)
    yield f"Base config written: ctx={format_number(result.context)}, split={_split_str(result.tensor_split)}"


async def _write_speed_config(
    config_path: Path, model_id: str, result: Result, gpus: list[GPU],
) -> AsyncIterator[str]:
    split_colon = _split_str(result.tensor_split)
    io.add_llamaswap_speed_variant(
        config_path=config_path,
        model_id=model_id,
        speed_split_cuda0=0,  # legacy, unused when speed_layer_split given
        speed_split_rest=0,
        speed_context=result.context,
        num_gpus=result.num_gpus,
        kv_quant=result.kv_quant,
        speed_layer_split=split_colon,
        cuda_visible_devices=_active_uuid_csv(result.tensor_split, gpus),
    )
    yield f"Speed config written: ctx={format_number(result.context)}, split={split_colon}"


def _persist_cache(
    model: Model, result: Result, gpus: list[GPU],
    speed_result: Result | None = None,
) -> None:
    """Write the base result (and optional speed variant) to the persistent
    JSON cache.

    The UI reads ``speed_split`` from the cache to decide whether to show
    the Speed-Mode toggle. Writing it atomically here prevents the race
    where a follow-up calibration run (e.g. TTS variant) overwrites the
    cache before a separate ``update_llamacpp_speed_split`` call lands.
    """
    vram_per_gpu = ",".join(str(g.total_mb) for g in gpus)
    speed_split_cuda0 = 0
    if speed_result is not None and speed_result.tensor_split:
        layer_vals = [int(v) for v in speed_result.tensor_split]
        if layer_vals and layer_vals[0] > 0:
            speed_split_cuda0 = layer_vals[0]
    add_llamacpp_calibration(
        model_id=model.model_id,
        max_context=result.context,
        native_context=model.native_context,
        gguf_path=str(model.gguf_path),
        quantization=model.quantization,
        gpu_model=", ".join(g.name for g in gpus),
        model_size_gb=model.size_mb / 1024,
        ngl=result.ngl,
        mode=result.mode,
        speed_split=speed_split_cuda0,
        vram_per_gpu=vram_per_gpu,  # type: ignore[arg-type]
        gpu_uuids=[g.uuid for g in gpus],
        # Real leftover per card after the base loaded — the SSOT the
        # variant spill uses instead of the KV-blind ``free − weight``.
        remaining_free_mb=list(result.remaining_free_mb) or None,
    )
    # Patch in the rest of the speed details (rest_layers + ctx) — these
    # power the UI's "speed available" indicator and CUDA_VISIBLE_DEVICES.
    if speed_result is not None and speed_split_cuda0 > 0:
        from ..model_vram_cache import update_llamacpp_speed_split
        layer_vals = [int(v) for v in speed_result.tensor_split]
        rest = sum(layer_vals[1:]) if len(layer_vals) > 1 else 0
        update_llamacpp_speed_split(
            model.model_id,
            speed_split_cuda0,
            rest,
            speed_result.context,
        )
