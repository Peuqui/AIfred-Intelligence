"""
GPU Utilities - VRAM Detection and Context Calculation

Provides functions to query free VRAM and calculate practical context limits
based on available GPU memory.
"""

import logging
import math
import struct
import requests
from pathlib import Path
from typing import Optional, Dict, List
from .config import (
    VRAM_SAFETY_MARGIN,
    VRAM_CONTEXT_RATIO_DENSE,
    VRAM_CONTEXT_RATIO_MOE,
    ENABLE_VRAM_CONTEXT_CALCULATION,
    DEFAULT_OLLAMA_URL
)
from .formatting import format_number
from . import nvidia_smi

logger = logging.getLogger(__name__)


# Common GPU VRAM sizes in GB (marketing specs)
NOMINAL_VRAM_SIZES: List[int] = [4, 6, 8, 10, 11, 12, 16, 20, 24, 32, 40, 48, 64, 80]


def round_to_nominal_vram(vram_mb: int) -> int:
    """
    Round VRAM to nearest marketing spec (nominal size).

    nvidia-smi reports slightly less VRAM than the marketing spec due to
    firmware overhead. This function rounds up to the expected nominal size.

    Args:
        vram_mb: VRAM in MiB as reported by nvidia-smi

    Returns:
        Nominal VRAM size in GB (e.g., 8, 12, 24, 48)

    Examples:
        >>> round_to_nominal_vram(23040)  # RTX 3090/4090
        24
        >>> round_to_nominal_vram(11264)  # RTX 2080 Ti
        12
        >>> round_to_nominal_vram(8192)   # RTX 3070
        8
        >>> round_to_nominal_vram(45000)  # Tesla A40
        48
    """
    vram_gb = vram_mb / 1024

    # Find closest size that's >= actual VRAM
    for size in NOMINAL_VRAM_SIZES:
        if vram_gb <= size:
            return size

    # For future larger GPUs: round up to nearest GB
    return math.ceil(vram_gb)


def total_actual_vram_gb(gpu_info) -> float:
    """Calculate total actual VRAM from GPU info (not nominal/marketing).

    Single source of truth for VRAM total display.
    """
    if gpu_info.all_gpu_vram_mb:
        return round(sum(gpu_info.all_gpu_vram_mb) / 1024, 1)
    return round(gpu_info.vram_mb / 1024, 1)


# NOTE: is_model_loaded() was moved to backends/base.py as abstractmethod
# Each backend implements its own logic:
# - Ollama: Query /api/ps endpoint
# - vLLM: Always True (model fixed at server start)
# - TabbyAPI: Always True (model fixed at server start)


def get_gpu_memory_info(gpu_index: int = 0) -> Optional[Dict]:
    """
    Query comprehensive GPU memory info via nvidia-smi.

    Args:
        gpu_index: GPU index (default: 0 for primary GPU)

    Returns:
        Dict with keys:
        - total_mb: Total VRAM in MB
        - free_mb: Free VRAM in MB
        - used_mb: Used VRAM in MB
        - gpu_model: GPU model name (e.g., "NVIDIA GeForce RTX 3090")
        Or None if GPU unavailable
    """
    rows = nvidia_smi.query(gpu_index=gpu_index)
    if not rows:
        return None
    row = rows[0]
    return {
        "total_mb": int(row["memory.total"]),
        "free_mb": int(row["memory.free"]),
        "used_mb": int(row["memory.used"]),
        "gpu_model": row["name"]
    }


def get_free_vram_for_single_gpu(gpu_index: int = 0) -> Optional[int]:
    """
    Query free VRAM for a specific GPU via nvidia-smi.

    Args:
        gpu_index: GPU index (0 for first GPU, 1 for second, etc.)

    Returns:
        int: Free VRAM in MB for the specified GPU, or None if GPU unavailable
    """
    rows = nvidia_smi.query("memory.free", gpu_index=gpu_index)
    if not rows:
        return None
    return int(rows[0]["memory.free"])


def get_free_vram_mb() -> Optional[int]:
    """
    Query free VRAM via nvidia-smi.

    For multi-GPU systems, returns the SUM of free VRAM across ALL GPUs.

    Returns:
        int: Total free VRAM in MB (summed across all GPUs), or None if GPU unavailable
    """
    rows = nvidia_smi.query("index,name,memory.free")
    if not rows:
        return None

    total_free = 0
    for i, row in enumerate(rows):
        free = int(row["memory.free"])
        total_free += free
        if len(rows) > 1:
            logger.debug(f"   GPU {i} ({row['name']}): {free} MB free")

    if len(rows) > 1:
        logger.debug(f"   Total free VRAM (sum of {len(rows)} GPUs): {total_free} MB")

    return total_free


def get_gpu_model_name(gpu_index: int = 0) -> Optional[str]:
    """
    Get GPU model name (e.g., "NVIDIA GeForce RTX 3090 Ti")

    Args:
        gpu_index: GPU index (default: 0 for primary GPU)

    Returns:
        GPU model name string, or None if unavailable
    """
    rows = nvidia_smi.query("name", gpu_index=gpu_index)
    if not rows:
        return None
    return rows[0]["name"]


def get_all_gpus_memory_info() -> Optional[Dict]:
    """
    Query memory info for ALL GPUs in the system via nvidia-smi.

    Returns aggregated stats plus per-GPU details.

    Returns:
        Dict with keys:
        - gpu_count: Number of GPUs
        - total_mb: Total VRAM summed across all GPUs
        - free_mb: Free VRAM summed across all GPUs
        - used_mb: Used VRAM summed across all GPUs
        - gpu_models: List of GPU model names
        - per_gpu: List of dicts with per-GPU info
        Or None if unavailable
    """
    rows = nvidia_smi.query()
    if not rows:
        return None

    total_mb = 0
    free_mb = 0
    used_mb = 0
    gpu_models: list[str] = []
    per_gpu: list[dict] = []

    for i, row in enumerate(rows):
        gt = int(row["memory.total"])
        gf = int(row["memory.free"])
        gu = int(row["memory.used"])
        total_mb += gt
        free_mb += gf
        used_mb += gu
        gpu_models.append(row["name"])
        per_gpu.append({
            "index": i,
            "gpu_model": row["name"],
            "total_mb": gt,
            "free_mb": gf,
            "used_mb": gu
        })

    return {
        "gpu_count": len(rows),
        "total_mb": total_mb,
        "free_mb": free_mb,
        "used_mb": used_mb,
        "gpu_models": gpu_models,
        "per_gpu": per_gpu
    }


def get_free_ram_mb() -> Optional[int]:
    """
    Query free system RAM using psutil.

    Returns:
        int: Available RAM in MB, or None if unavailable
    """
    try:
        import psutil
        mem = psutil.virtual_memory()
        return int(mem.available / (1024 * 1024))
    except ImportError:
        logger.warning("psutil not installed - install via: pip install psutil")
        return None
    except ImportError as e:
        logger.debug(f"Could not query RAM via psutil: {e}")
        return None


def get_swap_used_mb() -> Optional[int]:
    """
    Query current swap usage using psutil.

    Returns:
        int: Used swap in MB, or None if unavailable
    """
    try:
        import psutil
        swap = psutil.swap_memory()
        return int(swap.used / (1024 * 1024))
    except ImportError:
        return None
    except ImportError as e:
        logger.debug(f"Could not query swap via psutil: {e}")
        return None


def get_dynamic_ram_reserve(free_ram_mb: int) -> int:
    """
    Calculate dynamic RAM reserve based on available RAM.

    More RAM available = larger reserve for comfort and stability.
    Less RAM available = smaller reserve to maximize usability.

    Args:
        free_ram_mb: Currently free RAM in MB

    Returns:
        int: RAM reserve in MB (2048-8192)
    """
    if free_ram_mb >= 32768:  # 32+ GB free
        return 8192  # 8 GB reserve (very comfortable)
    elif free_ram_mb >= 16384:  # 16-32 GB free
        return 6144  # 6 GB reserve
    elif free_ram_mb >= 8192:  # 8-16 GB free
        return 4096  # 4 GB reserve
    elif free_ram_mb >= 4096:  # 4-8 GB free
        return 3072  # 3 GB reserve
    else:  # < 4 GB free
        return 2048  # 2 GB reserve (minimum)


def calculate_context_from_memory(
    available_mb: float,
    reserve_mb: float,
    ratio_mb_per_token: float,
    max_context: int | None = None
) -> int:
    """
    Calculate maximum context tokens based on available memory.

    Universal function for both VRAM and RAM (Hybrid mode) calculations.
    Uses the formula: max_tokens = (available_mb - reserve_mb) / ratio_mb_per_token

    Args:
        available_mb: Available memory in MB (from get_free_vram_mb or get_free_ram_mb)
        reserve_mb: Memory to keep free in MB (safety margin)
        ratio_mb_per_token: MB per token (0.10 for MoE, 0.15 for Dense models)
        max_context: Optional upper limit (e.g., native context limit)

    Returns:
        int: Maximum context tokens, or 0 if not enough memory
    """
    usable_mb = available_mb - reserve_mb
    if usable_mb <= 0:
        return 0

    calculated_tokens = int(usable_mb / ratio_mb_per_token)

    if max_context is not None:
        return min(calculated_tokens, max_context)
    return calculated_tokens


def read_gguf_field(gguf_path: Path, key_suffix: str) -> Optional[int]:
    """
    Read an integer field from GGUF metadata by key suffix.

    Uses raw struct parsing — does not require the gguf Python module.
    Searches for a metadata key ending with key_suffix (case-insensitive).

    Args:
        gguf_path: Path to GGUF model file
        key_suffix: Suffix to match (e.g. ".expert_count", ".context_length")

    Returns:
        Integer value if found, None otherwise
    """
    # GGUF value type IDs
    _UINT8, _INT8 = 0, 1
    _UINT16, _INT16 = 2, 3
    _UINT32, _INT32 = 4, 5
    _FLOAT32 = 6
    _BOOL = 7
    _STRING = 8
    _ARRAY = 9
    _UINT64, _INT64 = 10, 11
    _FLOAT64 = 12

    try:
        with open(gguf_path, "rb") as f:
            magic = f.read(4)
            if magic != b"GGUF":
                return None

            version = struct.unpack("<I", f.read(4))[0]
            if version < 2:
                return None

            _tensor_count = struct.unpack("<Q", f.read(8))[0]
            kv_count = struct.unpack("<Q", f.read(8))[0]

            def _read_string():
                length = struct.unpack("<Q", f.read(8))[0]
                return f.read(length).decode("utf-8", errors="ignore")

            def _read_value(vtype: int):
                if vtype == _UINT8:
                    return struct.unpack("<B", f.read(1))[0]
                if vtype == _INT8:
                    return struct.unpack("<b", f.read(1))[0]
                if vtype == _UINT16:
                    return struct.unpack("<H", f.read(2))[0]
                if vtype == _INT16:
                    return struct.unpack("<h", f.read(2))[0]
                if vtype == _UINT32:
                    return struct.unpack("<I", f.read(4))[0]
                if vtype == _INT32:
                    return struct.unpack("<i", f.read(4))[0]
                if vtype == _FLOAT32:
                    return struct.unpack("<f", f.read(4))[0]
                if vtype == _BOOL:
                    return struct.unpack("<B", f.read(1))[0]
                if vtype == _STRING:
                    return _read_string()
                if vtype == _UINT64:
                    return struct.unpack("<Q", f.read(8))[0]
                if vtype == _INT64:
                    return struct.unpack("<q", f.read(8))[0]
                if vtype == _FLOAT64:
                    return struct.unpack("<d", f.read(8))[0]
                if vtype == _ARRAY:
                    arr_type = struct.unpack("<I", f.read(4))[0]
                    arr_len = struct.unpack("<Q", f.read(8))[0]
                    return [_read_value(arr_type) for _ in range(arr_len)]
                return None

            suffix_lower = key_suffix.lower()
            for _ in range(kv_count):
                key = _read_string()
                vtype = struct.unpack("<I", f.read(4))[0]
                value = _read_value(vtype)

                if key.lower().endswith(suffix_lower):
                    if isinstance(value, (int, float)):
                        return int(value)

    except Exception as e:
        logger.debug(f"GGUF read failed for {gguf_path}: {e}")

    return None


def is_moe_model(model_name: str, ollama_url: str = DEFAULT_OLLAMA_URL) -> bool:
    """
    Detect if model is MoE (Mixture of Experts) architecture.

    Detection priority:
    1. Cached expert_count in model_vram_cache.json (instant)
    2. GGUF metadata: {arch}.expert_count field (for llama-swap models with gguf_path)
    3. Name-based: "-A{N}B" pattern (e.g. A3B, A22B) → MoE active parameter indicator
    4. Ollama API family field (for Ollama-only models)

    Results are cached in model_vram_cache.json for future calls.

    Args:
        model_name: Model name (Ollama tag or llama-swap model ID)
        ollama_url: Ollama API base URL

    Returns:
        True if MoE, False if Dense or unknown
    """
    from .model_vram_cache import load_cache, get_expert_counts, set_expert_counts

    # Method 1: Check cached expert_count (fastest path)
    cached = get_expert_counts(model_name)
    if cached is not None:
        is_moe = cached["expert_count"] > 1
        logger.debug(
            f"{'✅ MoE' if is_moe else '📊 Dense'} (cached): {model_name} "
            f"(experts: {cached['expert_count']}, active: {cached['expert_used_count']})"
        )
        return is_moe

    # Method 2: Read from GGUF metadata (llama-swap models have gguf_path in cache)
    cache = load_cache()
    entry = cache.get(model_name, {})
    gguf_path_str = entry.get("gguf_path")

    if gguf_path_str:
        gguf_path = Path(gguf_path_str)
        if gguf_path.exists():
            expert_count = read_gguf_field(gguf_path, ".expert_count")
            if expert_count is not None and expert_count > 1:
                expert_used = read_gguf_field(gguf_path, ".expert_used_count") or 0
                set_expert_counts(model_name, expert_count, expert_used)
                logger.debug(
                    f"✅ MoE detected (GGUF): {model_name} "
                    f"(experts: {expert_count}, active: {expert_used})"
                )
                return True
            elif expert_count is not None:
                # expert_count == 0 or 1 → Dense, cache it
                set_expert_counts(model_name, expert_count, 0)
                logger.debug(f"📊 Dense model (GGUF): {model_name}")
                return False

    # Method 3: Name-based detection — "-A{N}B" pattern (e.g. A3B, A22B, A32B)
    # This is a strong MoE indicator used by Qwen, GLM, and other model families
    import re as _re
    active_match = _re.search(r'-[Aa](\d+)[Bb]', model_name)
    if active_match:
        active_params = int(active_match.group(1))
        logger.debug(
            f"✅ MoE detected (name pattern): {model_name} "
            f"(active: {active_params}B)"
        )
        return True

    # Method 4: Query Ollama API for family field
    try:
        response = requests.post(
            f"{ollama_url}/api/show",
            json={"name": model_name},
            timeout=5.0
        )

        if response.status_code == 200:
            data = response.json()
            family = data.get("details", {}).get("family", "")
            moe_families = ["moe", "mixtral", "qwen3moe", "deepseek-moe"]
            is_moe = any(indicator in family.lower() for indicator in moe_families)

            if is_moe:
                logger.debug(f"✅ MoE detected (Ollama API): {model_name} (family: {family})")
            else:
                logger.debug(f"📊 Dense model (Ollama API): {model_name} (family: {family})")

            return is_moe

    except Exception as e:
        logger.debug(f"Ollama API query failed for {model_name}: {e}")

    logger.debug(f"📊 MoE detection inconclusive, defaulting to Dense: {model_name}")
    return False


def measure_vram_during_inference(
    context_tokens: int,
    vram_before_mb: int
) -> Optional[Dict]:
    """
    Measure VRAM usage during inference to calibrate MB/token ratio

    Args:
        context_tokens: Number of context tokens used in this inference
        vram_before_mb: Free VRAM before inference started (baseline)

    Returns:
        Dict with measurement data:
        {
            "vram_during_mb": int,           # Free VRAM during inference
            "vram_used_by_context": int,     # VRAM consumed by KV cache
            "measured_mb_per_token": float   # Calculated MB/token ratio
        }
        or None if measurement failed
    """
    vram_during_mb = get_free_vram_mb()

    if vram_during_mb is None or vram_before_mb is None:
        return None

    # Calculate VRAM used by context (KV cache)
    vram_used_by_context = vram_before_mb - vram_during_mb

    # Ignore invalid measurements (negative = measurement error or noise)
    if vram_used_by_context <= 0 or context_tokens <= 0:
        return None

    # Calculate MB/token from this measurement
    measured_mb_per_token = vram_used_by_context / context_tokens

    return {
        "vram_during_mb": vram_during_mb,
        "vram_used_by_context": vram_used_by_context,
        "measured_mb_per_token": round(measured_mb_per_token, 4)
    }


def get_model_size_from_cache(model_name: str) -> int:
    """
    Get model size in bytes from HuggingFace cache

    Args:
        model_name: Model name (e.g., "cpatonn/Qwen3-30B-A3B-Instruct-2507-AWQ-4bit")

    Returns:
        int: Model size in bytes, or 0 if not found
    """
    from pathlib import Path

    cache_dir = Path.home() / ".cache" / "huggingface" / "hub"

    # Convert model name to cache folder format
    cache_folder_name = f"models--{model_name.replace('/', '--')}"

    try:
        for model_dir in cache_dir.glob(cache_folder_name):
            # Find all model files (safetensors, bin, gguf, etc.)
            total_size = 0

            # Search for model weight files
            for pattern in ["**/*.safetensors", "**/*.bin", "**/*.gguf", "**/*.pth"]:
                for file_path in model_dir.glob(pattern):
                    if file_path.is_file():
                        total_size += file_path.stat().st_size

            if total_size > 0:
                logger.debug(f"Model size for {model_name}: {total_size / (1024**3):.2f} GB")
                return total_size

        logger.warning(f"Could not determine size for model: {model_name}")
        return 0

    except Exception as e:
        logger.warning(f"Error getting model size: {e}")
        return 0


async def calculate_vram_based_context(
    model_name: str,
    model_size_bytes: int,
    model_context_limit: int,
    vram_context_ratio: float | None = None,
    safety_margin_mb: int = VRAM_SAFETY_MARGIN,
    model_is_loaded: bool = False,
    backend_type: str = "ollama",
    backend = None  # Backend instance for unloading models (Ollama only)
) -> tuple[int, list[str]]:
    """
    Calculate maximum practical context window based on available VRAM

    UNIVERSAL FUNCTION FOR ALL BACKENDS (Ollama, vLLM, TabbyAPI)

    For Ollama: Reads per-model use_extended setting from VRAM cache automatically.

    Args:
        model_name: Name of the model (for MoE detection and logging)
        model_size_bytes: Model size in bytes (from HF cache or Ollama blobs)
        model_context_limit: Model's architectural context limit (from config.json)
        vram_context_ratio: MB of VRAM per context token (default: auto-detect via MoE)
        safety_margin_mb: MB to reserve for system (default: 512 MB from config)
        model_is_loaded: Whether model is already loaded in VRAM (affects calculation)
        backend_type: Backend type ("ollama", "vllm", "tabbyapi") for MoE detection

    Returns:
        tuple[int, list[str]]: (num_ctx, debug_messages)
            - num_ctx: Maximum practical context based on VRAM constraints
            - debug_messages: List of debug messages for UI console (via yield)

    Process:
        1. Query free VRAM from nvidia-smi
        2. Subtract safety margin (OS, Xorg, Whisper)
        3. If model NOT loaded: Subtract model size from free VRAM
        4. If model IS loaded: Free VRAM already accounts for it
        5. Calculate: max_tokens = vram_for_context / vram_context_ratio
        6. Clip to model's architectural limit

    Fallbacks:
        - VRAM calculation disabled: Use model_context_limit
        - nvidia-smi unavailable: Use model_context_limit
        - Insufficient VRAM (<100 MB): Return 2048 tokens (minimal)
    """
    debug_msgs = []  # Collect messages for UI yield

    # PRIORITY 1: Check for calibrated max_context (most accurate!)
    # If we have a manually calibrated value, use it directly instead of calculating dynamically
    if backend_type == "ollama":
        from .model_vram_cache import get_ollama_calibrated_max_context, get_rope_factor_for_model

        # Read RoPE factor from cache (per-model setting)
        rope_factor = get_rope_factor_for_model(model_name)

        # For extended mode: try extended calibration first, fall back to native
        if rope_factor >= 2.0:
            calibrated_max = get_ollama_calibrated_max_context(model_name, rope_factor=2.0)
            if calibrated_max is not None:
                # Extended calibration found - use it (can exceed native limit via RoPE)
                debug_msgs.append(f"🎯 Calibrated (RoPE 2x): {format_number(calibrated_max)} tok")
                return calibrated_max, debug_msgs
            # No extended calibration - fall through to native or VRAM calculation

        # Native calibration (default)
        calibrated_max = get_ollama_calibrated_max_context(model_name, rope_factor=1.0)
        if calibrated_max is not None:
            # Use calibrated value directly - no VRAM calculation needed!
            final_ctx = min(calibrated_max, model_context_limit)
            debug_msgs.append(f"🎯 Calibrated: {format_number(final_ctx)} tok (measured max_context_gpu_only)")
            return final_ctx, debug_msgs

    # PRIORITY 2: Auto-detect VRAM context ratio if not provided
    if vram_context_ratio is None:
        # Detect MoE for Ollama and llama.cpp (vLLM/TabbyAPI use manual override)
        if backend_type in ("ollama", "llamacpp"):
            is_moe = is_moe_model(model_name)
            architecture = "moe" if is_moe else "dense"
            default_ratio = VRAM_CONTEXT_RATIO_MOE if is_moe else VRAM_CONTEXT_RATIO_DENSE

            # Try to get calibrated ratio from unified cache
            from .model_vram_cache import get_calibrated_ratio, get_measurement_count
            vram_context_ratio = get_calibrated_ratio(model_name, architecture, default_ratio)

            # Show whether using calibrated or default ratio
            measurement_count = get_measurement_count(model_name)
            if measurement_count > 0:
                model_type = "MoE (calibrated)" if is_moe else "Dense (calibrated)"
                debug_msgs.append(f"🔍 {model_type} → {format_number(vram_context_ratio, 4)} MB/token ({measurement_count} measurements)")
            else:
                model_type = "MoE" if is_moe else "Dense"
                debug_msgs.append(f"🔍 {model_type} detected → {format_number(vram_context_ratio, 2)} MB/token")
        else:
            # For vLLM/TabbyAPI: Default to Dense (safer)
            vram_context_ratio = VRAM_CONTEXT_RATIO_DENSE

    # Check if VRAM calculation is enabled
    if not ENABLE_VRAM_CONTEXT_CALCULATION:
        logger.debug("VRAM context calculation disabled in config")
        return model_context_limit, []

    # Query free VRAM immediately (no stabilization wait)
    # REMOVED: 3-second VRAM stabilization loop - unnecessary overhead
    free_vram_mb = get_free_vram_mb()

    if free_vram_mb is None:
        # Fallback: Use architectural limit
        msg = (
            "⚠️ VRAM query failed (CPU-only or nvidia-smi unavailable), "
            "using model's architectural limit"
        )
        debug_msgs.append(msg)
        return model_context_limit, debug_msgs

    # Calculate usable VRAM (after safety margin)
    usable_vram = free_vram_mb - safety_margin_mb

    # Convert model size to MB for calculations
    model_size_mb = model_size_bytes / (1024**2) if model_size_bytes > 0 else 0

    # TWO-SCENARIO LOGIC:
    # Scenario 1: Model NOT loaded → Must subtract model size from free VRAM
    # Scenario 2: Model IS loaded → Free VRAM already reflects loaded model
    # (Caller determines this based on backend-specific logic)
    if model_is_loaded:
        # Model already in VRAM - free_vram_mb already accounts for it
        vram_for_context = usable_vram
        msg1 = f"💾 Model loaded → {format_number(vram_for_context, 0)} MB for context"
        msg2 = f"   ({format_number(free_vram_mb)} MB free - {format_number(safety_margin_mb)} MB margin)"
        debug_msgs.append(msg1)
        debug_msgs.append(msg2)
    else:
        # Model NOT loaded - must subtract its size from available VRAM
        vram_for_context_calc = int(usable_vram - model_size_mb)

        # DISABLED: Ollama manages VRAM automatically with LRU strategy
        # Manual unloading is redundant - when we load a new model, Ollama
        # automatically unloads the old one. This avoids unnecessary delays.
        #
        # Special case: Negative value indicates another model is still loaded
        # if vram_for_context_calc < 0:
        #     msg = "⚠️ Another model still loaded → Unloading all models..."
        #     debug_msgs.append(msg)
        #     if backend and hasattr(backend, 'unload_all_models'):
        #         success, unloaded = await backend.unload_all_models()
        #         ... (removed for brevity)

        # Simply use the calculated value (may be negative if another model loaded)
        # Ollama will handle the swap automatically when we make a request
        vram_for_context = max(0, vram_for_context_calc)
        if vram_for_context_calc < 0:
            msg = f"💾 Another model loaded → Ollama will auto-swap (calculated: {format_number(vram_for_context_calc, 0)} MB)"
            debug_msgs.append(msg)
        else:
            msg1 = f"💾 Model NOT loaded → {format_number(vram_for_context, 0)} MB for context"
            msg2 = f"   ({format_number(free_vram_mb)} MB - {format_number(model_size_mb, 0)} MB model - {format_number(safety_margin_mb)} MB margin)"
            debug_msgs.append(msg1)
            debug_msgs.append(msg2)

    if vram_for_context < 100:
        msg = f"❌ Insufficient VRAM for context: {format_number(vram_for_context, 0)} MB (< 100 MB minimum) → Fallback 2.048"
        debug_msgs.append(msg)
        return 2048, debug_msgs  # Minimal fallback

    # Calculate max tokens
    max_practical_tokens = int(vram_for_context / vram_context_ratio)

    # Clip to architectural limit
    final_num_ctx = min(max_practical_tokens, model_context_limit)

    # Log detailed VRAM calculation to debug console
    # Format with German thousands separator (dot instead of comma)
    formatted_ctx = format_number(final_num_ctx)
    formatted_vram_max = format_number(max_practical_tokens)
    formatted_model_max = format_number(model_context_limit)

    # Determine limiting factor and create compact message
    if max_practical_tokens <= model_context_limit:
        # VRAM is the bottleneck
        msg = f"🎯 VRAM-Limit: {formatted_ctx} tok (Model Max: {formatted_model_max} tok) [uncalibrated]"
    else:
        # Model is the bottleneck
        msg = f"🎯 Model-Limit: {formatted_ctx} tok (VRAM Max: {formatted_vram_max} tok) [uncalibrated]"
    debug_msgs.append(msg)

    return final_num_ctx, debug_msgs


def calculate_gpu_layers(model_size_gb: float, vram_mb: int) -> int:
    """
    Calculate optimal number of GPU layers based on model size and VRAM

    Args:
        model_size_gb: Model size in GB
        vram_mb: Available VRAM in MB

    Returns:
        Number of GPU layers to offload
    """
    # Safety margin
    safety_margin_mb = 2048  # 2GB safety margin

    usable_vram_mb = vram_mb - safety_margin_mb

    if usable_vram_mb < 0:
        return 0  # Not enough VRAM

    # Estimate layers based on VRAM
    # Rough approximation: 40 layers for 30B model = ~17GB
    # So 1 layer ≈ 425MB for 30B model

    model_size_mb = model_size_gb * 1024

    if model_size_mb <= usable_vram_mb:
        return -1  # All layers fit

    # Calculate partial layers
    # Assuming linear relationship (simplification)
    estimated_layers = int((usable_vram_mb / model_size_mb) * 40)

    return max(0, estimated_layers)


# ============================================================
# GPU UTILIZATION MONITORING (für InactivityMonitor)
# ============================================================

def get_gpu_utilization() -> Optional[List[int]]:
    """
    Get current GPU utilization for all GPUs via nvidia-smi.

    Returns:
        List of utilization percentages (0-100) for each GPU
        None if nvidia-smi fails or no GPUs found
    """
    rows = nvidia_smi.query("utilization.gpu")
    if not rows:
        return None
    try:
        return [int(row["utilization.gpu"]) for row in rows]
    except (ValueError, KeyError):
        return None


def are_all_gpus_idle(utilizations: Optional[List[int]]) -> bool:
    """
    Check if all GPUs are idle (0% utilization)

    Args:
        utilizations: List from get_gpu_utilization()

    Returns:
        True if all GPUs at 0%, False if any GPU active or None
    """
    if not utilizations:
        return False  # Assume active if can't determine

    return all(u == 0 for u in utilizations)
