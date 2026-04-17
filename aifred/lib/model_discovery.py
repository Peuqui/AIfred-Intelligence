"""
Model Discovery - Backend-agnostic model discovery for AIfred

Provides functions to discover available models from different backends:
- vLLM/TabbyAPI: Scan HuggingFace cache
- Ollama: Query server API
- llama.cpp: Query llama-swap API

Returns Dict[model_id, display_label] for UI dropdown population.
"""

from pathlib import Path
from typing import Callable, Dict, Optional
import httpx

from .formatting import format_number
from .logging_utils import log_message
from .model_manager import sort_models_grouped


def discover_huggingface_models(
    backend_type: str,
    is_compatible_fn: Callable[[Path, str], bool]
) -> Dict[str, str]:
    """
    Discover models from HuggingFace cache for vLLM/TabbyAPI backends.

    Args:
        backend_type: "vllm" or "tabbyapi"
        is_compatible_fn: Function(model_dir, backend_type) -> bool

    Returns:
        Dict mapping model_id to display label with size
    """
    hf_cache = Path.home() / ".cache" / "huggingface" / "hub"

    if not hf_cache.exists():
        log_message("⚠️ HuggingFace cache not found")
        return {}

    # Find all model directories (format: models--Org--ModelName)
    model_dirs = [
        d for d in hf_cache.iterdir()
        if d.is_dir() and d.name.startswith("models--")
    ]

    result = {}
    for model_dir in model_dirs:
        if is_compatible_fn(model_dir, backend_type):
            model_id = model_dir.name.replace("models--", "").replace("--", "/", 1)

            # Calculate size using blob-based calculation
            try:
                from .vllm_manager import get_model_size_bytes
                total_size = get_model_size_bytes(model_id)
                size_gb = total_size / (1024**3)
                result[model_id] = f"{model_id} ({format_number(size_gb, 1)} GB)"
            except (httpx.HTTPError, ImportError):
                # Fallback: show without size if calculation fails
                result[model_id] = model_id

    log_message(f"📂 Found {len(result)} {backend_type}-compatible models ({len(model_dirs)} total in cache)")
    return result


def discover_ollama_models(backend_url: str, timeout: float = 5.0) -> Dict[str, str]:
    """
    Discover models from Ollama server API.

    Args:
        backend_url: Ollama server URL (e.g., "http://localhost:11434")
        timeout: Request timeout in seconds

    Returns:
        Dict mapping model_name to display label with size
    """
    endpoint = f'{backend_url}/api/tags'

    try:
        response = httpx.get(endpoint, timeout=timeout)
        if response.status_code == 200:
            data = response.json()
            result = {
                m['name']: f"{m['name']} ({format_number(m['size'] / (1024**3), 1)} GB)"
                for m in data.get("models", [])
            }
            log_message(f"📂 Found {len(result)} Ollama models")
            return result
        else:
            log_message(f"⚠️ Ollama API returned {response.status_code}")
            return {}
    except httpx.RequestError as e:
        log_message(f"⚠️ Ollama API not reachable: {e}")
        return {}


def discover_llamacpp_models(backend_url: str, timeout: float = 10.0) -> Dict[str, str]:
    """
    Discover models from llama-swap via OpenAI-compatible /v1/models endpoint.

    Display format matches Ollama: model_id + file size.
    llama-swap keys are already descriptive (e.g., "qwen3-30b-a3b-instruct-2507-q8_0").

    Args:
        backend_url: llama-swap URL with /v1 suffix (e.g., "http://localhost:8080/v1")
        timeout: Request timeout in seconds (higher because llama-swap may cold-start)

    Returns:
        Dict mapping model_id to display label
        e.g., {"qwen3-30b-a3b-instruct-2507-q8_0": "qwen3-30b-a3b-instruct-2507-q8_0 (30.3 GB)"}
    """
    endpoint = f'{backend_url}/models'

    try:
        response = httpx.get(endpoint, timeout=timeout)
        if response.status_code != 200:
            log_message(f"⚠️ llama-swap API returned {response.status_code}")
            return {}

        data = response.json()
        model_ids = [m['id'] for m in data.get("data", [])]

        # Get file sizes from llama-swap config
        model_sizes = _get_llamacpp_model_sizes()

        result = {}
        for mid in model_ids:
            if mid.endswith("-speed") or "-tts-" in mid:
                continue  # Speed/TTS variants are internal; selected automatically
            size_gb = model_sizes.get(mid)
            if size_gb is not None:
                result[mid] = f"{mid} ({format_number(size_gb, 1)} GB)"
            else:
                result[mid] = mid

        log_message(f"📂 Found {len(result)} llama.cpp models (via llama-swap)")
        return result

    except httpx.RequestError as e:
        log_message(f"⚠️ llama-swap not reachable: {e}")
        return {}


def _get_llamacpp_model_sizes() -> Dict[str, float]:
    """Get GGUF file sizes for llama-swap models."""
    try:
        from .calibration import parse_llamaswap_config
        from .config import LLAMASWAP_CONFIG_PATH

        config = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
        result = {}
        for model_id, info in config.items():
            gguf_path = Path(info["gguf_path"])
            if gguf_path.exists():
                from .gguf_utils import get_gguf_total_size
                result[model_id] = get_gguf_total_size(gguf_path) / (1024 ** 3)
        return result
    except httpx.HTTPError:
        return {}


def discover_models(
    backend_type: str,
    backend_url: Optional[str] = None,
    is_compatible_fn: Optional[Callable[[Path, str], bool]] = None
) -> Dict[str, str]:
    """
    Unified model discovery for any backend type.

    Args:
        backend_type: "ollama", "vllm", "tabbyapi", or "llamacpp"
        backend_url: Required for Ollama and llamacpp backends
        is_compatible_fn: Required for vLLM/TabbyAPI backends

    Returns:
        Sorted dict mapping model_id to display label (by family, then size)
    """
    if backend_type in ["vllm", "tabbyapi"]:
        if not is_compatible_fn:
            raise ValueError("is_compatible_fn required for vLLM/TabbyAPI")
        unsorted = discover_huggingface_models(backend_type, is_compatible_fn)

    elif backend_type == "ollama":
        if not backend_url:
            raise ValueError("backend_url required for Ollama")
        unsorted = discover_ollama_models(backend_url)

    elif backend_type == "llamacpp":
        if not backend_url:
            raise ValueError("backend_url required for llamacpp")
        unsorted = discover_llamacpp_models(backend_url)

    else:
        log_message(f"⚠️ Unknown backend type: {backend_type}")
        return {}

    return sort_models_grouped(unsorted)
