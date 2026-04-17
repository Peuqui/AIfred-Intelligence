"""llama.cpp calibration package.

Public entry point: :func:`calibrate_llamacpp_model` (in :mod:`flow`).

All YAML read/write helpers and cmd-string parsers are re-exported here
so callers (backends, state mixins) keep a single import path:

    from aifred.lib.calibration import (
        calibrate_llamacpp_model,
        parse_llamaswap_config,
        update_llamaswap_context,
        ...
    )
"""

from .flow import calibrate_llamacpp_model
from .llamaswap_io import (
    add_llamaswap_speed_variant,
    add_llamaswap_tts_variant,
    parse_llamaswap_config,
    parse_sampling_from_cmd,
    parse_tensor_split,
    remove_llamaswap_kv_cache_quant,
    update_llamaswap_context,
    update_llamaswap_cuda_visible,
    update_llamaswap_kv_cache_quant,
    update_llamaswap_ngl,
    update_llamaswap_reasoning_format,
    update_llamaswap_tensor_split,
)

__all__ = [
    # Core
    "calibrate_llamacpp_model",
    # YAML helpers (consumed across the codebase)
    "parse_llamaswap_config",
    "parse_sampling_from_cmd",
    "parse_tensor_split",
    "update_llamaswap_context",
    "update_llamaswap_ngl",
    "update_llamaswap_tensor_split",
    "update_llamaswap_cuda_visible",
    "update_llamaswap_kv_cache_quant",
    "update_llamaswap_reasoning_format",
    "remove_llamaswap_kv_cache_quant",
    "add_llamaswap_speed_variant",
    "add_llamaswap_tts_variant",
]
