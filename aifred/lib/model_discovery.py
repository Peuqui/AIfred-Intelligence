"""
Model Discovery - Backend-agnostic model discovery for AIfred

Provides functions to discover available models from different backends:
- Ollama: Query server API
- llama.cpp: Query llama-swap API (GGUF entries)
- vLLM: Query llama-swap API (``-vllm`` entries — vLLM-Checkpoints laufen
  als llama-swap-Einträge, siehe scripts/llama-swap-autoscan.py)

Returns Dict[model_id, display_label] for UI dropdown population.
"""

import json
from pathlib import Path
from typing import Dict, Optional
import httpx

from .formatting import format_number
from .logging_utils import log_message
from .model_manager import sort_models_grouped

# Namenskonvention der vLLM-Einträge in der llama-swap-Config — der
# Autoscan seedet sie als "<checkpoint-dirname>-vllm" (SSOT der Erzeugung:
# scripts/llama-swap-autoscan.py, seed_vllm_entries).
VLLM_ENTRY_SUFFIX = "-vllm"


def is_vllm_entry(model_id: str) -> bool:
    """True, wenn der llama-swap-Eintrag ein vLLM-Checkpoint ist."""
    return model_id.endswith(VLLM_ENTRY_SUFFIX)


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


def discover_llamaswap_models(
    backend_url: str,
    timeout: float = 10.0,
    vllm_entries: bool = False,
) -> Dict[str, str]:
    """
    Discover models from llama-swap via OpenAI-compatible /v1/models endpoint.

    Ein llama-swap-Katalog trägt zwei Modellwelten: GGUF-Einträge
    (llama.cpp) und ``-vllm``-Einträge (vLLM-Checkpoints). Die Backends
    zeigen jeweils nur ihre eigene Welt — ``vllm_entries`` wählt sie.

    Args:
        backend_url: llama-swap URL with /v1 suffix (e.g., "http://localhost:11435/v1")
        timeout: Request timeout in seconds (higher because llama-swap may cold-start)
        vllm_entries: False → nur GGUF-Einträge (Backend llama.cpp),
            True → nur ``-vllm``-Einträge (Backend vLLM)

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
        model_sizes = _get_llamaswap_model_sizes()

        result = {}
        for mid in model_ids:
            if (
                mid.endswith("-speed") or mid.endswith("-visiond")
                or mid.endswith("-embed") or "-tts-" in mid or "-vlm-" in mid
            ):
                continue  # Speed/TTS/VLM/Describer/Embed variants are internal; selected automatically
            if is_vllm_entry(mid) != vllm_entries:
                continue
            size_gb = model_sizes.get(mid)
            if size_gb is not None:
                result[mid] = f"{mid} ({format_number(size_gb, 1)} GB)"
            else:
                result[mid] = mid

        kind = "vLLM" if vllm_entries else "llama.cpp"
        log_message(f"📂 Found {len(result)} {kind} models (via llama-swap)")
        return result

    except httpx.RequestError as e:
        log_message(f"⚠️ llama-swap not reachable: {e}")
        return {}


def vllm_checkpoint_size_bytes(checkpoint_dir: Path) -> int:
    """Gewichtsgröße eines vLLM-Checkpoint-Verzeichnisses in Bytes.

    Summiert die von ``model.safetensors.index.json`` referenzierten
    Dateien (dedupliziert, Symlinks aufgelöst — Transplant-Ordner wie
    MTPQ verlinken in den HF-Cache). Damit zählen nur Gewichte, die der
    Loader wirklich lädt — nicht referenzierte Alt-Shards (z.B. der
    ersetzte BF16-Draftkopf) bleiben außen vor. Ohne Index: alle
    ``*.safetensors`` im Verzeichnis.
    """
    index_file = checkpoint_dir / "model.safetensors.index.json"
    if index_file.exists():
        weight_map = json.loads(index_file.read_text()).get("weight_map", {})
        files = {checkpoint_dir / fname for fname in weight_map.values()}
    else:
        files = set(checkpoint_dir.glob("*.safetensors"))
    return sum(
        p.resolve().stat().st_size for p in files if p.resolve().exists()
    )


def _get_llamaswap_model_sizes() -> Dict[str, float]:
    """Get model sizes (GB) for llama-swap entries.

    GGUF-Einträge: Dateigröße inkl. Draft-Sidecar (``--model-draft``,
    z.B. DSpark) — der lädt bei jedem Run mit und zählt zum realen
    Preis des Profils. ``-vllm``-Einträge: Checkpoint-Verzeichnis über
    den Safetensors-Index.
    """
    try:
        from .calibration.projection import draft_gguf_path
        from .calibration import parse_llamaswap_config
        from .config import LLAMASWAP_CONFIG_PATH
        from .gguf_utils import get_gguf_total_size

        config = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
        result = {}
        for model_id, info in config.items():
            model_path = Path(info["gguf_path"])
            if not model_path.exists():
                continue
            if model_path.is_dir():
                # vLLM-Eintrag: --model zeigt auf ein Checkpoint-Verzeichnis
                total_bytes = vllm_checkpoint_size_bytes(model_path)
            else:
                total_bytes = get_gguf_total_size(model_path)
                draft = draft_gguf_path(info["full_cmd"])
                if draft is not None and draft.exists():
                    total_bytes += get_gguf_total_size(draft)
            result[model_id] = total_bytes / (1024 ** 3)
        return result
    except OSError as e:
        log_message(f"⚠️ Could not read model sizes from llama-swap config: {e}")
        return {}


def discover_models(
    backend_type: str,
    backend_url: Optional[str] = None,
) -> Dict[str, str]:
    """
    Unified model discovery for any backend type.

    Args:
        backend_type: "ollama", "vllm", or "llamacpp"
        backend_url: Required (Ollama-URL bzw. llama-swap-URL)

    Returns:
        Sorted dict mapping model_id to display label (by family, then size)
    """
    if backend_type == "ollama":
        if not backend_url:
            raise ValueError("backend_url required for Ollama")
        unsorted = discover_ollama_models(backend_url)

    elif backend_type in ("llamacpp", "vllm"):
        if not backend_url:
            raise ValueError("backend_url required for llama-swap discovery")
        unsorted = discover_llamaswap_models(
            backend_url, vllm_entries=(backend_type == "vllm")
        )

    else:
        log_message(f"⚠️ Unknown backend type: {backend_type}")
        return {}

    return sort_models_grouped(unsorted)
