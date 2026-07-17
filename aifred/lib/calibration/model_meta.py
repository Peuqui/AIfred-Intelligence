"""GGUF-Metadaten → :class:`Model`."""

from __future__ import annotations

from pathlib import Path

from ..gguf_utils import (
    extract_quantization_from_filename,
    get_gguf_layer_count,
    get_gguf_native_context,
    get_gguf_total_size,
)
from .types import Model


def _load_model_meta(model_id: str, gguf_path: Path) -> Model | None:
    native = get_gguf_native_context(gguf_path)
    total_layers = get_gguf_layer_count(gguf_path)
    if not native or not total_layers:
        return None
    size_mb = get_gguf_total_size(gguf_path) / (1024 ** 2)
    return Model(
        model_id=model_id,
        gguf_path=gguf_path,
        native_context=native,
        total_layers=total_layers,
        size_mb=size_mb,
        mb_per_layer=size_mb / total_layers,
        quantization=extract_quantization_from_filename(gguf_path.name),
    )
