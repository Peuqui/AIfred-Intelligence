"""
Model Manager - Utility functions for model sorting and backend compatibility.

This module extracts pure helper functions from state.py that don't require
State access and can be used independently.

Extracted from state.py (Phase 3.2 Refactoring):
- sort_models_grouped(): Sort models by family and size
- is_backend_compatible(): Check model compatibility with backend
"""

import logging
import re
import json
from typing import Dict
from pathlib import Path

logger = logging.getLogger(__name__)


def sort_models_grouped(models_dict: Dict[str, str]) -> Dict[str, str]:
    """
    Sort models by model family (alphabetically) and then by size (ascending).

    Groups models by their base name (e.g., "qwen2.5", "qwen3", "mistral", "gemma")
    and sorts within each group by size.

    Args:
        models_dict: Dict[model_id, display_label] e.g., {"qwen3:8b": "qwen3:8b (5.2 GB)"}

    Returns:
        Sorted dict with same structure

    Example:
        >>> models = {"qwen3:8b": "qwen3:8b (5.2 GB)", "qwen3:1.7b": "qwen3:1.7b (1.0 GB)"}
        >>> sorted_models = sort_models_grouped(models)
        >>> list(sorted_models.keys())
        ['qwen3:1.7b', 'qwen3:8b']  # Sorted by size within family
    """

    def get_model_family(model_id: str) -> str:
        """Extract model family for grouping.

        Handles both Ollama format (qwen3:8b) and llama.cpp format (Qwen3-14B-Q4_K_M).
        Keeps -vl, -coder as they define different model families.
        """
        base = model_id.lower()
        # Ollama: Remove size suffix like :8b, :30b, :0.6b etc.
        base = re.sub(r':\d+\.?\d*b.*$', '', base)
        base = re.sub(r':.*$', '', base)
        # llama.cpp: Remove size like -14b, -4b, -30b and everything after
        base = re.sub(r'-\d+\.?\d*b([-_].*)?$', '', base)
        # Remove quantization suffixes like -q4_k_m, -q8_0 etc.
        base = re.sub(r'[-_]q\d+.*$', '', base)
        # Remove version suffixes like -instruct, -chat, -latest, -thinking, -a3b, -2507 etc.
        # BUT keep -vl, -coder as they define different model families!
        base = re.sub(r'[-_](instruct|chat|latest|thinking|a3b|\d{4}).*$', '', base)
        return base

    def get_model_size_gb(display_label: str) -> float:
        """Extract size in GB from display label like 'model (5.2 GB)' or 'model (5,2 GB)'"""
        match = re.search(r'\(([\d.,]+)\s*GB\)', display_label)
        if match:
            # Handle both locale formats: "61.2" (EN) and "61,2" (DE)
            return float(match.group(1).replace(",", "."))
        return 0.0

    # Create list of (model_id, display_label, family, size)
    models_with_info = [
        (mid, label, get_model_family(mid), get_model_size_gb(label))
        for mid, label in models_dict.items()
    ]

    # Sort by family (alphabetically), then by size (ascending)
    models_with_info.sort(key=lambda x: (x[2], x[3]))

    # Convert back to dict (preserves order in Python 3.7+)
    return {mid: label for mid, label, _, _ in models_with_info}


def is_backend_compatible(model_dir: Path, backend: str) -> bool:
    """
    Check if model is compatible with backend by reading config.json.

    vLLM supports:
    - AWQ (quantization_config.quant_method = "awq")
    - GPTQ (quantization_config.quant_method = "gptq")
    - compressed-tensors (quantization_config.quant_method = "compressed-tensors")
    - FP16/BF16 (no quantization_config)

    Does NOT support:
    - GGUF (Ollama-only)
    - Non-LLM models (Whisper, Vision, etc.)

    Args:
        model_dir: Path to the model directory (e.g., from HuggingFace cache)
        backend: Backend name ("vllm")

    Returns:
        True if model is compatible with the specified backend

    Example:
        >>> from pathlib import Path
        >>> model_path = Path("/home/user/.cache/huggingface/hub/models--Qwen--Qwen2.5-7B-AWQ")
        >>> is_backend_compatible(model_path, "vllm")
        True
    """
    model_name = model_dir.name.replace("models--", "").replace("--", "/", 1)

    # Exclude non-LLM models by name pattern
    exclude_patterns = ['whisper', 'faster-whisper', 'table-transformer', 'resnet', 'gguf']
    if any(pattern in model_name.lower() for pattern in exclude_patterns):
        return False

    # Try to find config.json in model directory
    config_paths = list(model_dir.glob("**/config.json"))

    if not config_paths:
        # No config.json found - skip this model
        return False

    try:
        with open(config_paths[0], 'r') as f:
            config_data = json.load(f)

        # Check if it's a valid LLM config (has model_type)
        if "model_type" not in config_data:
            return False

        # Check quantization format
        if "quantization_config" in config_data:
            quant_method = config_data["quantization_config"].get("quant_method", "")

            if backend == "vllm":
                # vLLM supports: awq, gptq, compressed-tensors
                return quant_method in ["awq", "gptq", "compressed-tensors"]
        else:
            # No quantization config
            if backend == "vllm":
                # FP16/BF16 (supported by vLLM)
                return True

    except (OSError, json.JSONDecodeError, AttributeError) as e:
        logger.warning(f"Failed to parse config.json for model '{model_name}': {e}")
        return False

    return False
