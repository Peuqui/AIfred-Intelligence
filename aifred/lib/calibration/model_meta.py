"""GGUF-Metadaten → :class:`Model`."""

from __future__ import annotations

from pathlib import Path

from ..gguf_utils import (
    extract_quantization_from_filename,
    get_gguf_layer_count,
    get_gguf_lazy_tensor_bytes,
    get_gguf_native_context,
    get_gguf_total_size,
)
from .types import Model


def _load_model_meta(model_id: str, gguf_path: Path) -> Model | None:
    native = get_gguf_native_context(gguf_path)
    total_layers = get_gguf_layer_count(gguf_path)
    if not native or not total_layers:
        return None
    file_size_mb = get_gguf_total_size(gguf_path) / (1024 ** 2)
    # Tensoren, die llama.cpp on-demand von der Platte liest, belegen kein
    # VRAM — sie gehoeren nicht in die Bedarfsrechnung.
    lazy_mb = get_gguf_lazy_tensor_bytes(gguf_path) / (1024 ** 2)
    size_mb = file_size_mb - lazy_mb
    return Model(
        model_id=model_id,
        gguf_path=gguf_path,
        native_context=native,
        total_layers=total_layers,
        size_mb=size_mb,
        file_size_mb=file_size_mb,
        mb_per_layer=size_mb / total_layers,
        quantization=extract_quantization_from_filename(gguf_path.name),
    )
