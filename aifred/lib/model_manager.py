"""
Model Manager - Utility functions for model sorting and backend compatibility.

This module extracts pure helper functions from state.py that don't require
State access and can be used independently.

Extracted from state.py (Phase 3.2 Refactoring):
- sort_models_grouped(): Sort models by family and size
"""

import logging
import re
from typing import Dict

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

