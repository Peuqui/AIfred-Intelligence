"""
Unified Model VRAM Cache Management

Manages a JSON-based cache for VRAM-related measurements across ALL backends
(Ollama, vLLM). Combines:
- VRAM ratio measurements (MB/token) - Universal for all backends
- vLLM context calibrations - vLLM-specific

Cache location: data/model_vram_cache.json

Structure:
{
    "model_name": {
        "backend": "ollama|vllm",
        "architecture": "moe|dense",
        "native_context": 262144,
        "gpu_model": "NVIDIA GeForce RTX 3090 Ti",

        "vram_ratio": {
            "measurements": [
                {
                    "context_tokens": 20720,
                    "measured_mb_per_token": 0.0872,
                    "measured_at": "2025-11-22T02:30:00"
                }
            ],
            "avg_mb_per_token": 0.0872
        },

        "vllm_calibrations": [  # Only for vLLM models
            {
                "free_vram_mb": 22968,
                "max_context": 21608,
                "measured_at": "2025-11-21T23:31:17"
            }
        ]
    }
}
"""

import json
import logging
import os
import threading
from typing import Optional, Dict, Any, List
from datetime import datetime

from .config import DATA_DIR

logger = logging.getLogger(__name__)

# Cache file location (centralized data directory)
CACHE_DIR = DATA_DIR
CACHE_FILE = CACHE_DIR / "model_vram_cache.json"

# In-memory cache with mtime-based invalidation
_cache: Dict[str, Any] | None = None
_cache_mtime: float = 0.0
# Guards read-modify-write sequences (add_vram_measurement, add_vllm_calibration)
# so two concurrent measurements don't overwrite each other's update.
_cache_lock = threading.Lock()


def ensure_cache_dir() -> None:
    """Create cache directory if it doesn't exist"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def load_cache() -> Dict[str, Any]:
    """
    Load unified model VRAM cache from JSON file.

    Uses an in-memory cache with mtime-based invalidation to avoid
    redundant disk reads when the file hasn't changed.

    Returns:
        Dict with model_name → cache_data mappings
        Empty dict if file doesn't exist or is invalid
    """
    global _cache, _cache_mtime
    ensure_cache_dir()

    # Check if file exists and get its mtime
    try:
        file_mtime = os.path.getmtime(CACHE_FILE)
    except OSError:
        # File doesn't exist
        return {}

    # Return cached version if file hasn't changed
    if _cache is not None and file_mtime <= _cache_mtime:
        return _cache

    # Load from disk
    try:
        with open(CACHE_FILE, encoding='utf-8') as f:
            cache: Dict[str, Any] = json.load(f)
        logger.info(f"Loaded unified model cache: {len(cache)} models")
        _cache = cache
        _cache_mtime = file_mtime
        return cache
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Failed to load cache file {CACHE_FILE}: {e}")
        return {}


def save_cache(cache: Dict[str, Any]) -> bool:
    """
    Save unified model VRAM cache to JSON file.

    After writing, invalidates the in-memory cache so the next load_cache()
    picks up the new data.

    Args:
        cache: Dict with model_name → cache_data mappings

    Returns:
        True if successful, False otherwise
    """
    global _cache, _cache_mtime
    ensure_cache_dir()

    tmp_path = CACHE_FILE.with_suffix(".json.tmp")
    try:
        # Atomic write via tmp + os.replace so a crash mid-write doesn't
        # leave the cache file truncated/corrupt (which would wipe every
        # earlier calibration on the next load).
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, CACHE_FILE)
        logger.info(f"Saved unified model cache: {len(cache)} models")
        # Invalidate in-memory cache so next load picks up fresh data
        _cache = None
        _cache_mtime = 0.0
        return True
    except (IOError, OSError) as e:
        logger.error(f"Failed to save cache file {CACHE_FILE}: {e}")
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False


# ============================================================================
# CALIBRATION STATUS HELPERS
# ============================================================================

def is_model_calibrated(model_id: str) -> bool:
    """True if ``model_id`` was *really* calibrated (not just discovered).

    Discriminator: ``gpu_model`` field on the cache entry.

    - **autoscan-default** (preliminary, fit-params without GPU layout):
      writes the entry with ``gpu_model=""`` so consumers can tell it
      apart from a real measurement.
    - **AIfred calibration flow** (full per-GPU measurement): sets
      ``gpu_model="RTX 8000, V100, ..."``.

    Both fill ``llamacpp_calibrations`` with a max_context entry, so the
    list alone doesn't distinguish the two. ``gpu_model`` does.
    """
    entry = load_cache().get(model_id)
    return bool(entry and entry.get("gpu_model"))


def is_tts_variant_calibrated(
    model_id: str, tts_backend: str, require_speed: bool = False,
) -> bool:
    """True if a TTS variant of ``model_id`` for ``tts_backend`` has a
    real (non-preliminary) calibration in the vram cache.

    ``require_speed=False`` (default): accept either ``-tts-<backend>``
    or ``-tts-<backend>-speed``.
    ``require_speed=True``: only ``-tts-<backend>-speed``.

    A TTS profile that exists only in llama-swap.yaml (e.g. carried over
    from an older model via a manual migration) but has no entry in the
    vram cache must NOT be reported as calibrated — picking it would
    load the base VRAM profile and OOM once the TTS container starts.
    """
    cache = load_cache()
    candidates = [f"{model_id}-tts-{tts_backend}-speed"]
    if not require_speed:
        candidates.append(f"{model_id}-tts-{tts_backend}")
    for name in candidates:
        entry = cache.get(name)
        if entry and entry.get("gpu_model"):
            return True
    return False


def is_vlm_variant_calibrated(
    model_id: str, vlm_key: str, require_speed: bool = False,
) -> bool:
    """True if a VLM variant of ``model_id`` for ``vlm_key`` has a real
    (non-preliminary) calibration in the vram cache. Mirrors
    :func:`is_tts_variant_calibrated` exactly so the picker UI can light
    up the green dot the same way.
    """
    cache = load_cache()
    candidates = [f"{model_id}-vlm-{vlm_key}-speed"]
    if not require_speed:
        candidates.append(f"{model_id}-vlm-{vlm_key}")
    for name in candidates:
        entry = cache.get(name)
        if entry and entry.get("gpu_model"):
            return True
    return False


# ============================================================================
# VRAM RATIO FUNCTIONS (Universal - ALL backends)
# ============================================================================

def add_vram_measurement(
    model_name: str,
    context_tokens: int,
    vram_before_mb: int,
    vram_during_mb: int,
    architecture: str,  # "moe" or "dense"
    backend: str = "ollama"
) -> None:
    """
    Add a VRAM ratio measurement for a model

    Args:
        model_name: Name of the model
        context_tokens: Number of context tokens used
        vram_before_mb: Free VRAM before inference (after model load)
        vram_during_mb: Free VRAM during inference (KV cache allocated)
        architecture: "moe" or "dense"
        backend: "ollama" or "vllm"
    """
    # Calculate MB per token from the measurement
    vram_used_by_context = vram_before_mb - vram_during_mb

    # Ignore invalid measurements (negative values = measurement error)
    if vram_used_by_context <= 0 or context_tokens <= 0:
        return

    mb_per_token = vram_used_by_context / context_tokens

    # Serialise read-modify-write so concurrent measurements don't
    # overwrite each other.
    with _cache_lock:
        cache = load_cache()

        # Initialize model entry if not exists
        if model_name not in cache:
            cache[model_name] = {
                "backend": backend,
                "architecture": architecture,
                "native_context": 0,  # Will be updated later
                "gpu_model": "Unknown",  # Will be updated later
                "vram_ratio": {
                    "measurements": [],
                    "avg_mb_per_token": 0.0
                }
            }

        # Ensure vram_ratio exists (for migrated entries)
        if "vram_ratio" not in cache[model_name]:
            cache[model_name]["vram_ratio"] = {
                "measurements": [],
                "avg_mb_per_token": 0.0
            }

        # Update architecture if different
        cache[model_name]["architecture"] = architecture
        cache[model_name]["backend"] = backend

        # Add measurement
        measurement = {
            "context_tokens": context_tokens,
            "measured_mb_per_token": round(mb_per_token, 4),
            "measured_at": datetime.now().isoformat()
        }
        cache[model_name]["vram_ratio"]["measurements"].append(measurement)

        # Keep only last 10 measurements per model
        if len(cache[model_name]["vram_ratio"]["measurements"]) > 10:
            cache[model_name]["vram_ratio"]["measurements"] = \
                cache[model_name]["vram_ratio"]["measurements"][-10:]

        # Calculate average MB/token from all measurements
        measurements = cache[model_name]["vram_ratio"]["measurements"]
        avg = sum(m["measured_mb_per_token"] for m in measurements) / len(measurements)
        cache[model_name]["vram_ratio"]["avg_mb_per_token"] = round(avg, 4)

        # Save cache
        save_cache(cache)


def get_calibrated_ratio(model_name: str, architecture: str, default_ratio: float) -> float:
    """
    Get calibrated MB/token ratio for a model

    Args:
        model_name: Name of the model
        architecture: "moe" or "dense" (for fallback)
        default_ratio: Default ratio to use if no measurements exist

    Returns:
        Calibrated MB/token ratio, or default if no calibration data
    """
    cache = load_cache()

    if model_name in cache:
        vram_ratio = cache[model_name].get("vram_ratio", {})
        avg = float(vram_ratio.get("avg_mb_per_token", 0.0))
        if avg > 0:
            return avg

    return default_ratio


def get_measurement_count(model_name: str) -> int:
    """Get number of VRAM ratio measurements for a model"""
    cache = load_cache()
    if model_name in cache:
        vram_ratio = cache[model_name].get("vram_ratio", {})
        return len(vram_ratio.get("measurements", []))
    return 0


def get_ollama_calibrated_max_context(
    model_name: str,
    rope_factor: float = 1.0
) -> Optional[int]:
    """
    Get calibrated max context for an Ollama model.

    Returns the experimentally measured maximum context that fits in GPU memory
    without CPU offloading. This is more accurate than dynamic VRAM calculation.

    Supports RoPE scaling factors:
    - 1.0x: Native context limit (no RoPE scaling)
    - 1.5x: Extended context (1.5x RoPE scaling)
    - 2.0x: Maximum context (2.0x RoPE scaling)

    Args:
        model_name: Ollama model name (e.g., "qwen3:30b-a3b-instruct-2507-q8_0")
        rope_factor: RoPE scaling factor (1.0, 1.5, or 2.0)

    Returns:
        Calibrated max context tokens, or None if no calibration exists
    """
    cache = load_cache()

    if model_name not in cache:
        return None

    calibrations = cache[model_name].get("ollama_calibrations", [])
    if not calibrations:
        return None

    # Determine field name based on RoPE factor
    if rope_factor == 1.0:
        field_name = "max_context_1.0x"
    elif rope_factor == 1.5:
        field_name = "max_context_1.5x"
    elif rope_factor == 2.0:
        field_name = "max_context_2.0x"
    else:
        field_name = "max_context_1.0x"  # Fallback

    # Search backwards for the most recent calibration with this field
    for cal in reversed(calibrations):
        max_ctx = cal.get(field_name)
        if max_ctx is not None:
            return int(max_ctx)

    return None


def add_ollama_calibration(
    model_name: str,
    max_context_gpu_only: int,
    native_context: int,
    gpu_model: str = "Unknown",
    rope_factor: float = 1.0,
    is_hybrid: bool = False
) -> bool:
    """
    Add a calibration point for an Ollama model.

    Stores the experimentally measured maximum context that fits entirely
    in GPU memory (no CPU offloading). This is determined via binary search
    using Ollama's /api/ps endpoint (size == size_vram check).

    Supports RoPE scaling factors:
    - 1.0x: Native context limit (no RoPE scaling)
    - 1.5x: Extended context (1.5x RoPE scaling)
    - 2.0x: Maximum context (2.0x RoPE scaling)

    Args:
        model_name: Ollama model name (e.g., "qwen3:8b")
        max_context_gpu_only: Maximum context tokens without CPU offload
        native_context: Model's architectural context limit
        gpu_model: GPU model name (e.g., "NVIDIA GeForce RTX 3090 Ti")
        rope_factor: RoPE scaling factor (1.0, 1.5, or 2.0)
        is_hybrid: True if using CPU+GPU hybrid mode (CPU offload)

    Returns:
        True if successfully added, False otherwise
    """
    with _cache_lock:
        cache = load_cache()

        # Initialize model entry if not exists
        if model_name not in cache:
            cache[model_name] = {
                "backend": "ollama",
                "native_context": native_context,
                "gpu_model": gpu_model,
                "ollama_calibrations": []
            }

        # Ensure ollama_calibrations exists
        if "ollama_calibrations" not in cache[model_name]:
            cache[model_name]["ollama_calibrations"] = []

        # Update metadata
        cache[model_name]["native_context"] = native_context
        cache[model_name]["gpu_model"] = gpu_model

        # Determine field name based on RoPE factor
        if rope_factor == 1.0:
            field_name = "max_context_1.0x"
            mode_label = "1.0x (native)"
        elif rope_factor == 1.5:
            field_name = "max_context_1.5x"
            mode_label = "1.5x (RoPE)"
        elif rope_factor == 2.0:
            field_name = "max_context_2.0x"
            mode_label = "2.0x (RoPE)"
        else:
            field_name = "max_context_1.0x"
            mode_label = "1.0x (native, fallback)"

        # Add or replace calibration point for this rope_factor
        calibration = {
            field_name: max_context_gpu_only,
            "is_hybrid": is_hybrid,
            "measured_at": datetime.now().isoformat()
        }

        calibrations = cache[model_name]["ollama_calibrations"]
        for i, cal in enumerate(calibrations):
            if field_name in cal:
                calibrations[i] = calibration
                break
        else:
            calibrations.append(calibration)

        hybrid_label = " [HYBRID]" if is_hybrid else ""
        logger.info(
            f"📊 Ollama calibration saved ({mode_label}{hybrid_label}): {model_name} → "
            f"{max_context_gpu_only:,} tokens (native: {native_context:,})"
        )

        return save_cache(cache)


def get_ollama_calibration(model_name: str, rope_factor: float = 1.0) -> Optional[int]:
    """
    Get the calibrated max context for an Ollama model from persistent cache.

    Args:
        model_name: Ollama model name (e.g., "qwen3:8b")
        rope_factor: RoPE scaling factor (1.0, 1.5, or 2.0)

    Returns:
        Calibrated max context tokens, or None if not found
    """
    cache = load_cache()

    if model_name not in cache:
        return None

    model_data = cache[model_name]
    calibrations = model_data.get("ollama_calibrations", [])

    if not calibrations:
        return None

    # Determine field name based on RoPE factor
    if rope_factor == 1.5:
        field_name = "max_context_1.5x"
    elif rope_factor == 2.0:
        field_name = "max_context_2.0x"
    else:
        field_name = "max_context_1.0x"

    # Get latest calibration with the requested RoPE factor
    for cal in reversed(calibrations):
        if field_name in cal:
            result: int | None = cal[field_name]
            return result

    return None


def is_ollama_model_hybrid(model_name: str, rope_factor: float = 1.0) -> bool:
    """
    Check if an Ollama model is running in hybrid mode (CPU+GPU offload).

    Args:
        model_name: Ollama model name (e.g., "qwen3:30b")
        rope_factor: RoPE scaling factor (1.0, 1.5, or 2.0)

    Returns:
        True if model uses hybrid mode, False otherwise (or if unknown)
    """
    cache = load_cache()

    if model_name not in cache:
        return False

    model_data = cache[model_name]
    calibrations = model_data.get("ollama_calibrations", [])

    if not calibrations:
        return False

    # Determine field name based on RoPE factor
    if rope_factor == 1.5:
        field_name = "max_context_1.5x"
    elif rope_factor == 2.0:
        field_name = "max_context_2.0x"
    else:
        field_name = "max_context_1.0x"

    # Get latest calibration with the requested RoPE factor
    for cal in reversed(calibrations):
        if field_name in cal:
            # Return is_hybrid flag (default False if not present)
            result: bool = cal.get("is_hybrid", False)
            return result

    return False


def get_model_parameters(model_name: str) -> Dict[str, Any]:
    """
    Get all cached parameters for a model (used by State to avoid repeated file I/O).

    Returns ALL model parameters including rope_factor, max_context, is_hybrid,
    and supports_thinking. This is the central function for loading model metadata.

    Args:
        model_name: Model name (e.g., "qwen3:30b")

    Returns:
        dict with keys: rope_factor, max_context, is_hybrid, supports_thinking
        Default values if model not in cache: rope_factor=1.0, rest are 0/False/None
    """
    cache = load_cache()

    if model_name not in cache:
        return {
            "rope_factor": 1.0,
            "max_context": 0,
            "is_hybrid": False,
            "supports_thinking": None
        }

    model_data = cache[model_name]

    # Get RoPE factor from cache
    rope_factor = float(model_data.get("rope_factor", 1.0))

    calibrations = model_data.get("ollama_calibrations", [])

    # Determine max_context and is_hybrid based on rope_factor
    max_context = 0
    is_hybrid = False

    if calibrations:
        # Determine field name based on RoPE factor
        if rope_factor == 1.5:
            field_name = "max_context_1.5x"
        elif rope_factor == 2.0:
            field_name = "max_context_2.0x"
        else:
            field_name = "max_context_1.0x"

        # Get latest calibration with the requested RoPE factor
        for cal in reversed(calibrations):
            if field_name in cal:
                max_context = cal.get(field_name, 0)
                is_hybrid = cal.get("is_hybrid", False)
                break

    # Get thinking support from model-level data (not calibration-specific)
    supports_thinking = model_data.get("supports_thinking")

    return {
        "rope_factor": rope_factor,
        "max_context": max_context,
        "is_hybrid": is_hybrid,
        "supports_thinking": supports_thinking
    }


def get_rope_factor_for_model(model_name: str) -> float:
    """
    Get the RoPE scaling factor for a specific Ollama model.

    This allows per-model configuration of RoPE scaling (1.0x, 1.5x, or 2.0x).

    Args:
        model_name: Ollama model name (e.g., "qwen3:14b")

    Returns:
        RoPE scaling factor (1.0, 1.5, or 2.0). Default: 1.0
    """
    cache = load_cache()

    if model_name not in cache:
        return 1.0

    return float(cache[model_name].get("rope_factor", 1.0))


def set_rope_factor_for_model(model_name: str, rope_factor: float) -> bool:
    """
    Set the RoPE scaling factor for a specific Ollama model.

    Args:
        model_name: Ollama model name (e.g., "qwen3:14b")
        rope_factor: RoPE scaling factor (1.0, 1.5, or 2.0)

    Returns:
        True if successfully saved, False otherwise
    """
    with _cache_lock:
        cache = load_cache()

        # Initialize model entry if not exists
        if model_name not in cache:
            cache[model_name] = {
                "backend": "ollama",
                "native_context": 0,
                "gpu_model": "Unknown",
                "rope_factor": rope_factor
            }
        else:
            cache[model_name]["rope_factor"] = rope_factor

        logger.info(f"📊 Set rope_factor={rope_factor}x for {model_name}")
        return save_cache(cache)


def set_thinking_support_for_model(model_name: str, supports_thinking: bool) -> bool:
    """
    Set the thinking/reasoning capability for a model.

    This is typically called after testing during calibration or first inference.

    Args:
        model_name: Model name (e.g., "qwen3:30b")
        supports_thinking: True if model supports <think> tags, False otherwise

    Returns:
        True if successfully saved, False otherwise
    """
    with _cache_lock:
        cache = load_cache()

        # Initialize model entry if not exists
        if model_name not in cache:
            cache[model_name] = {
                "backend": "ollama",
                "native_context": 0,
                "gpu_model": "Unknown",
                "supports_thinking": supports_thinking
            }
        else:
            cache[model_name]["supports_thinking"] = supports_thinking

        status = "✅" if supports_thinking else "⚠️"
        logger.info(f"{status} Set thinking support={supports_thinking} for {model_name}")
        return save_cache(cache)


def get_thinking_support_for_model(model_name: str) -> Optional[bool]:
    """
    Get thinking/reasoning capability for a model.

    Returns:
        True/False if tested, None if unknown
    """
    cache = load_cache()
    if model_name not in cache:
        return None
    result: bool | None = cache[model_name].get("supports_thinking")
    return result


def set_reasoning_levels_for_model(model_name: str, levels: List[str]) -> bool:
    """
    Set the steerable reasoning-effort levels for a model, detected from
    its embedded chat template (gguf_utils.detect_reasoning_levels).

    Empty list = template analyzed, no effort levels (plain on/off
    thinking, or none at all).
    """
    with _cache_lock:
        cache = load_cache()

        if model_name not in cache:
            cache[model_name] = {
                "backend": "llamacpp",
                "native_context": 0,
                "gpu_model": "Unknown",
                "reasoning_levels": levels,
            }
        else:
            cache[model_name]["reasoning_levels"] = levels

        logger.info(f"🧠 Set reasoning_levels={levels} for {model_name}")
        return save_cache(cache)


def get_reasoning_levels_for_model(model_name: str) -> Optional[List[str]]:
    """
    Get the steerable reasoning-effort levels for a model.

    Returns:
        Level list if analyzed (may be empty = on/off only),
        None if never analyzed.
    """
    cache = load_cache()
    if model_name not in cache:
        return None
    result: List[str] | None = cache[model_name].get("reasoning_levels")
    return result


# ============================================================================
# MoE EXPERT COUNT FUNCTIONS
# ============================================================================

def get_expert_counts(model_name: str) -> Optional[Dict[str, int]]:
    """
    Get cached expert_count and expert_used_count for a model.

    Returns:
        Dict with "expert_count" and "expert_used_count" if present, None otherwise
    """
    cache = load_cache()
    entry = cache.get(model_name, {})
    expert_count = entry.get("expert_count")
    if expert_count is not None:
        return {
            "expert_count": expert_count,
            "expert_used_count": entry.get("expert_used_count", 0),
        }
    return None


def set_expert_counts(model_name: str, expert_count: int, expert_used_count: int = 0) -> bool:
    """
    Cache expert_count and expert_used_count for a model.

    Called during calibration or first model load when GGUF metadata is read.

    Args:
        model_name: Model name (key in cache)
        expert_count: Total number of experts (e.g. 128 for GPT-OSS)
        expert_used_count: Experts active per token (e.g. 4 for GPT-OSS)

    Returns:
        True if successfully saved
    """
    with _cache_lock:
        cache = load_cache()

        if model_name not in cache:
            cache[model_name] = {
                "backend": "llamacpp",
                "native_context": 0,
                "gpu_model": "",
            }

        cache[model_name]["expert_count"] = expert_count
        cache[model_name]["expert_used_count"] = expert_used_count

        label = f"MoE ({expert_count}x, {expert_used_count} active)" if expert_count > 1 else "Dense"
        logger.info(f"📊 Set expert counts for {model_name}: {label}")
        return save_cache(cache)


# ============================================================================
# vLLM CALIBRATION FUNCTIONS (vLLM-specific)
# ============================================================================

def get_vllm_calibrations(model_id: str) -> Optional[List[Dict[str, Any]]]:
    """
    Get all vLLM calibration points for a model

    Args:
        model_id: The model identifier (e.g., "Qwen/Qwen3-8B-AWQ")

    Returns:
        List of calibration dicts, or None if no calibrations exist
    """
    cache = load_cache()

    if model_id not in cache:
        return None

    calibrations = cache[model_id].get("vllm_calibrations", None)
    if calibrations is None:
        return None
    return list(calibrations) if isinstance(calibrations, list) else None


def interpolate_vllm_context(model_id: str, current_vram_mb: int, tolerance_mb: int = 500) -> Optional[int]:
    """
    Get max context for a model at current VRAM level using interpolation

    Args:
        model_id: The model identifier
        current_vram_mb: Current free VRAM in MB
        tolerance_mb: Consider calibration points within ±tolerance_mb as exact matches

    Returns:
        Interpolated max context tokens, or None if insufficient calibration data
    """
    calibrations = get_vllm_calibrations(model_id)

    if not calibrations or len(calibrations) == 0:
        return None

    # Sort calibrations by VRAM (ascending)
    sorted_cal = sorted(calibrations, key=lambda x: x["free_vram_mb"])

    # Check for exact match within tolerance
    for cal in sorted_cal:
        if abs(cal["free_vram_mb"] - current_vram_mb) <= tolerance_mb:
            logger.info(f"Exact match: {cal['free_vram_mb']} MB ≈ {current_vram_mb} MB → {cal['max_context']} tokens")
            return int(cal["max_context"])

    # Interpolation logic (same as old vllm_context_cache.py)
    if current_vram_mb < sorted_cal[0]["free_vram_mb"]:
        # Below lowest calibration - extrapolate downward
        if len(sorted_cal) >= 2:
            x1, y1 = sorted_cal[0]["free_vram_mb"], sorted_cal[0]["max_context"]
            x2, y2 = sorted_cal[1]["free_vram_mb"], sorted_cal[1]["max_context"]
            if x2 == x1:
                return int(y1)
            slope = (y2 - y1) / (x2 - x1)
            interpolated = int(y1 + slope * (current_vram_mb - x1))
            return max(interpolated, 1024)  # Minimum 1K tokens
        else:
            return int(sorted_cal[0]["max_context"])

    elif current_vram_mb > sorted_cal[-1]["free_vram_mb"]:
        # Above highest calibration - use highest known value
        return int(sorted_cal[-1]["max_context"])

    else:
        # Between two calibration points - linear interpolation
        for i in range(len(sorted_cal) - 1):
            x1, y1 = sorted_cal[i]["free_vram_mb"], sorted_cal[i]["max_context"]
            x2, y2 = sorted_cal[i + 1]["free_vram_mb"], sorted_cal[i + 1]["max_context"]

            if x1 <= current_vram_mb <= x2:
                if x2 == x1:
                    return int(y1)
                slope = (y2 - y1) / (x2 - x1)
                interpolated = int(y1 + slope * (current_vram_mb - x1))
                logger.info(f"Interpolated: {current_vram_mb} MB → {interpolated} tokens (between {x1} and {x2} MB)")
                return interpolated

    return None


def add_vllm_calibration(
    model_id: str,
    free_vram_mb: int,
    max_context: int,
    native_context: int,
    gpu_model: str,
    architecture: str = "unknown"
) -> bool:
    """
    Add a new vLLM calibration point for a model

    Args:
        model_id: The model identifier
        free_vram_mb: Free VRAM in MB when this context was measured
        max_context: Maximum context tokens at this VRAM level
        native_context: Model's native/architectural context limit
        gpu_model: GPU model name (e.g., "NVIDIA GeForce RTX 3090 Ti")
        architecture: "moe", "dense", or "unknown"

    Returns:
        True if successfully added, False otherwise
    """
    with _cache_lock:
        cache = load_cache()

        # Initialize model entry if not exists
        if model_id not in cache:
            cache[model_id] = {
                "backend": "vllm",
                "architecture": architecture,
                "native_context": native_context,
                "gpu_model": gpu_model,
                "vllm_calibrations": []
            }

        # Ensure vllm_calibrations exists (for Ollama models)
        if "vllm_calibrations" not in cache[model_id]:
            cache[model_id]["vllm_calibrations"] = []

        # Update metadata
        cache[model_id]["native_context"] = native_context
        cache[model_id]["gpu_model"] = gpu_model
        if architecture != "unknown":
            cache[model_id]["architecture"] = architecture

        # Add calibration point
        calibration = {
            "free_vram_mb": free_vram_mb,
            "max_context": max_context,
            "measured_at": datetime.now().isoformat()
        }

        cache[model_id]["vllm_calibrations"].append(calibration)

        # Save and return result
        return save_cache(cache)


# ============================================================
# llama.cpp Calibration (via llama-swap)
# ============================================================

def add_llamacpp_calibration(
    model_id: str,
    max_context: int,
    native_context: int,
    gguf_path: str,
    quantization: str,
    gpu_model: str,
    model_size_gb: float,
    ngl: int = 99,
    mode: str = "gpu",
    speed_split: int = 0,
    vram_per_gpu: Optional[Dict[str, int]] = None,
    ram_cpu_mb: Optional[int] = None,
    gpu_uuids: Optional[list] = None,
    remaining_free_mb: Optional[list] = None,
) -> bool:
    """Add a calibration point for a llama.cpp model (via llama-swap).

    Stores the experimentally measured maximum context that fits in GPU VRAM,
    determined via binary search by starting llama-server with different -c values.

    Args:
        model_id: llama-swap model name (e.g., "Qwen3-4B-Instruct-2507-Q4_K_M")
        max_context: Maximum context tokens that fit in VRAM
        native_context: Model's architectural context limit (from GGUF metadata)
        gguf_path: Path to GGUF file
        quantization: Quantization level (e.g., "Q4_K_M")
        gpu_model: Comma-separated short names of the GPUs in the active
            set, in calibration order (compute_cap DESC). Human-readable
            label only — not used for cache validation.
        model_size_gb: GGUF file size in GB
        ngl: GPU layers used (99 = all on GPU, lower = hybrid mode)
        mode: Calibration mode ("gpu" or "hybrid")
        speed_split: Tensor-split N for the speed variant (N:1 ratio). 0 = no speed variant.
        vram_per_gpu: Per-GPU total-VRAM snapshot at calibration time.
        ram_cpu_mb: CPU-side memory consumed in hybrid mode.
        gpu_uuids: Permanent NVIDIA GPU UUIDs of the active set, in
            calibration order. Used to invalidate the cache when the
            hardware actually changes (slot moves are tolerated, GPU
            replacements are detected).
        remaining_free_mb: Measured free VRAM per GPU AFTER the model
            loaded at ``max_context`` (parallel to ``gpu_uuids``). This is
            the real leftover capacity per card — weights + KV + buffers
            already deducted. TTS/VLM variant derivation uses it as the
            spill headroom so a full RTX 8000 (little leftover) never gets
            overloaded while an idle V100 (lots leftover) absorbs the
            overflow. Without it the spill only sees ``free − weight`` and
            ignores the ctx-dependent KV load.

    Returns:
        True if successfully added, False otherwise
    """
    with _cache_lock:
        cache = load_cache()

        if model_id not in cache:
            cache[model_id] = {
                "backend": "llamacpp",
                "native_context": native_context,
                "quantization": quantization,
                "model_size_gb": model_size_gb,
                "gpu_model": gpu_model,
                "gguf_path": gguf_path,
                "llamacpp_calibrations": []
            }

        if "llamacpp_calibrations" not in cache[model_id]:
            cache[model_id]["llamacpp_calibrations"] = []

        cache[model_id]["native_context"] = native_context
        cache[model_id]["quantization"] = quantization
        cache[model_id]["model_size_gb"] = model_size_gb
        cache[model_id]["gpu_model"] = gpu_model
        cache[model_id]["gguf_path"] = gguf_path
        if gpu_uuids:
            cache[model_id]["gpu_uuids"] = list(gpu_uuids)
        # A successful calibration overrides any earlier failure_status
        # so the picker drops the red dot on the same cell.
        cache[model_id].pop("failure_status", None)

        calibration: Dict[str, Any] = {
            "max_context": max_context,
            "ngl": ngl,
            "mode": mode,
            "measured_at": datetime.now().isoformat()
        }
        if speed_split > 0:
            calibration["speed_split"] = speed_split
        if vram_per_gpu:
            calibration["vram_per_gpu"] = vram_per_gpu
        if ram_cpu_mb and ram_cpu_mb > 0:
            calibration["ram_cpu_mb"] = ram_cpu_mb
        if gpu_uuids:
            calibration["gpu_uuids"] = list(gpu_uuids)
        if remaining_free_mb:
            calibration["remaining_free_mb"] = list(remaining_free_mb)

        # Replace all previous calibrations — only the latest matters
        cache[model_id]["llamacpp_calibrations"] = [calibration]

        speed_info = f", speed_split={speed_split}:1" if speed_split > 0 else ""
        logger.info(
            f"Added llama.cpp calibration for {model_id}: "
            f"{max_context:,} tokens (native: {native_context:,}, ngl={ngl}, mode={mode}{speed_info})"
        )

        return save_cache(cache)


def get_llamacpp_calibration(model_id: str) -> Optional[int]:
    """
    Get the latest calibrated max_context for a llama.cpp model.

    Args:
        model_id: llama-swap model name

    Returns:
        Calibrated max_context in tokens, or None if not calibrated
    """
    cache = load_cache()

    if model_id not in cache:
        return None

    calibrations = cache[model_id].get("llamacpp_calibrations", [])
    if not calibrations:
        return None

    # Return most recent calibration
    return int(calibrations[-1]["max_context"])


def get_llamacpp_calibration_info(model_id: str) -> Optional[Dict[str, Any]]:
    """
    Get full calibration info for a llama.cpp model (including ngl and mode).

    Returns:
        Dict with keys: max_context, ngl, mode, measured_at
        Or None if not calibrated
    """
    cache = load_cache()
    if model_id not in cache:
        return None
    calibrations = cache[model_id].get("llamacpp_calibrations", [])
    if not calibrations:
        return None
    latest = calibrations[-1]
    return {
        "max_context": int(latest["max_context"]),
        "ngl": latest.get("ngl", 99),
        "mode": latest.get("mode", "gpu"),
        "measured_at": latest.get("measured_at", ""),
    }


def get_llamacpp_remaining_free_by_uuid(model_id: str) -> Dict[str, int]:
    """Map each GPU UUID → measured free VRAM (MiB) after the base model
    loaded at its calibrated context, from the latest calibration.

    Keyed by UUID (not list position) so variant derivation stays correct
    even if the GPU enumeration order differs from calibration time. Returns
    an empty dict when the field is absent (cache entries written before
    this field existed) — the caller then keeps the weight-only headroom.
    """
    cache = load_cache()
    if model_id not in cache:
        return {}
    calibrations = cache[model_id].get("llamacpp_calibrations", [])
    if not calibrations:
        return {}
    latest = calibrations[-1]
    uuids = latest.get("gpu_uuids") or cache[model_id].get("gpu_uuids") or []
    free = latest.get("remaining_free_mb") or []
    if not uuids or len(uuids) != len(free):
        return {}
    return {str(u): int(f) for u, f in zip(uuids, free)}


def update_llamacpp_speed_split(
    model_id: str,
    speed_split: int,
    speed_split_rest: int = 0,
    speed_split_context: int = 0,
) -> bool:
    """
    Update the speed_split fields on the most recent llama.cpp calibration entry.

    Called after speed variant calibration completes (separate from main calibration
    because the speed result arrives after the context result is already saved).
    """
    with _cache_lock:
        cache = load_cache()
        if model_id not in cache:
            return False
        calibrations = cache[model_id].get("llamacpp_calibrations", [])
        if not calibrations:
            return False
        calibrations[-1]["speed_split"] = speed_split
        calibrations[-1]["speed_split_rest"] = speed_split_rest
        if speed_split_context > 0:
            calibrations[-1]["speed_split_context"] = speed_split_context
        logger.info(f"Updated speed_split={speed_split}:{speed_split_rest} for {model_id}")
        return save_cache(cache)


def get_llamacpp_speed_split(model_id: str) -> tuple[int, int, int]:
    """
    Get the speed-variant tensor-split for a llama.cpp model.

    Returns (cuda0_layers, rest_layers, context) from the most recent calibration.
    All zeros means no speed variant was calibrated.
    """
    cache = load_cache()
    if model_id not in cache:
        return (0, 0, 0)
    calibrations = cache[model_id].get("llamacpp_calibrations", [])
    if not calibrations:
        return (0, 0, 0)
    cal = calibrations[-1]
    split = cal.get("speed_split", 0)
    if split > 0:
        return (
            int(split),
            int(cal.get("speed_split_rest", 0)),
            int(cal.get("speed_split_context", 0)),
        )
    return (0, 0, 0)


def get_llamacpp_calibrations(model_id: str) -> List[Dict[str, Any]]:
    """
    Get all llama.cpp calibration points for a model.

    Args:
        model_id: llama-swap model name

    Returns:
        List of calibration dicts, newest first
    """
    cache = load_cache()

    if model_id not in cache:
        return []

    calibrations = cache[model_id].get("llamacpp_calibrations", [])
    return list(reversed(calibrations))


def remove_model_from_cache(model_id: str) -> bool:
    """Delete a model's entire entry from the VRAM cache.

    Used when a TTS-variant calibration fails: the stale
    ``<model>-tts-<backend>`` cache entry must go, otherwise the
    calibration picker keeps showing it as "already calibrated".
    Returns True if an entry was actually removed."""
    with _cache_lock:
        cache = load_cache()
        if model_id not in cache:
            return False
        del cache[model_id]
        logger.info(f"Removed stale cache entry: {model_id}")
        return save_cache(cache)


# ============================================================================
# CALIBRATION FAILURE TRACKING
# ============================================================================
#
# A variant that has been *tried* and failed gets a ``failure_status`` entry.
# The picker UI reads this to render a red dot — distinct from "never tried"
# (no entry) and "successfully calibrated" (``gpu_model`` set). On the next
# successful calibration for the same key, ``failure_status`` is cleared.

_VALID_FAILURE_REASONS = {
    "capacity_exceeded",     # TTS+VLM reserves > side-channel-GPU total (combo pre-check)
    "model_too_big",         # TTS reserve so large that LLM doesn't fit alongside it
    "projection_failed",     # fit-params says "no fit" even at minimum context
    "probe_unrecoverable",   # All layer-shifts and ctx-shrinks exhausted without a passing probe
}


def record_calibration_failure(
    model_id: str,
    reason: str,
    detail: str = "",
) -> bool:
    """Record a failed calibration attempt for ``model_id``.

    The picker UI uses this to show a red dot ("tried, doesn't work")
    instead of leaving the cell visually empty ("never tried"). The
    next successful calibration for the same key clears the failure
    automatically via ``add_llamacpp_calibration``.

    Args:
        model_id: Variant key (e.g. ``"<base>-tts-fishspeech"``,
            ``"<base>-tts-fishspeech-vlm-qwen3vl8b"``).
        reason: One of ``_VALID_FAILURE_REASONS``.
        detail: Human-readable explanation, shown in the UI tooltip.

    Returns:
        True if the failure was stored, False on validation or I/O error.
    """
    if reason not in _VALID_FAILURE_REASONS:
        logger.error(
            f"record_calibration_failure: invalid reason {reason!r} "
            f"(allowed: {sorted(_VALID_FAILURE_REASONS)})"
        )
        return False

    with _cache_lock:
        cache = load_cache()
        entry = cache.get(model_id) or {"backend": "llamacpp"}
        entry["failure_status"] = {
            "reason": reason,
            "detail": detail,
            "measured_at": datetime.now().isoformat(),
        }
        # A failure overrides any prior "calibrated" state: drop the
        # gpu_model + llamacpp_calibrations so is_*_calibrated() helpers
        # return False and the picker shows the red dot, not green.
        entry.pop("gpu_model", None)
        entry.pop("llamacpp_calibrations", None)
        cache[model_id] = entry
        logger.info(f"Recorded calibration failure for {model_id}: {reason} — {detail}")
        return save_cache(cache)


def is_calibration_failed(model_id: str) -> bool:
    """True if ``model_id`` has a recorded failure (no successful retry yet)."""
    entry = load_cache().get(model_id)
    return bool(entry and entry.get("failure_status"))


def get_calibration_failure(model_id: str) -> Optional[Dict[str, Any]]:
    """Return the raw ``failure_status`` dict or None if not failed."""
    entry = load_cache().get(model_id)
    if not entry:
        return None
    status = entry.get("failure_status")
    return status if isinstance(status, dict) else None


def get_model_native_context_from_cache(model_id: str) -> Optional[int]:
    """
    Get native (architectural) context limit from VRAM cache.

    This is the maximum context the model architecture supports,
    stored during calibration. Works for all backends (Ollama, llama.cpp, vLLM).

    Returns:
        Native context in tokens, or None if not in cache
    """
    cache = load_cache()
    if model_id not in cache:
        return None
    native = cache[model_id].get("native_context", 0)
    return int(native) if native else None


def get_cached_context(model_id: str) -> Optional[Dict[str, Any]]:
    """Get all cached data for a model"""
    cache = load_cache()
    return cache.get(model_id, None)
