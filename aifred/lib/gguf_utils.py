"""
GGUF Model Discovery and Metadata Utilities

Finds GGUF models on the filesystem and extracts metadata
for llama.cpp backend integration.
"""

import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


# GGUF value-type IDs that are numeric scalars (UINT/INT/BOOL only).
# Excludes STRING (8) and ARRAY (9) so int(value_array[0]) doesn't
# silently interpret the first byte of a string as the value, and
# excludes FLOAT (6, 12) where integer fields shouldn't show up.
_GGUF_NUMERIC_INT_TYPES = frozenset({0, 1, 2, 3, 4, 5, 7, 10, 11})


def _is_numeric_scalar(field) -> bool:  # type: ignore[no-untyped-def]
    """True if ``field`` is a GGUF scalar with an integer/bool value type."""
    types = getattr(field, "types", None)
    if not types:
        return False
    return types[-1] in _GGUF_NUMERIC_INT_TYPES


class GGUFModelInfo:
    """GGUF model metadata"""
    def __init__(
        self,
        path: Path,
        name: str,
        size_gb: float,
        quantization: str,
        native_context: Optional[int] = None,
        architecture: Optional[str] = None
    ):
        self.path = path
        self.name = name
        self.size_gb = size_gb
        self.quantization = quantization
        self.native_context = native_context
        self.architecture = architecture

    def __repr__(self):
        return f"GGUFModelInfo(name='{self.name}', size={self.size_gb:.1f}GB, quant={self.quantization})"


def get_gguf_total_size(gguf_path: Path) -> int:
    """
    Get total file size for a GGUF model, including all split parts.

    Split GGUFs follow the pattern: model-00001-of-00002.gguf, model-00002-of-00002.gguf
    This function detects split files and sums all parts.

    Args:
        gguf_path: Path to GGUF file (first part for split GGUFs)

    Returns:
        Total size in bytes across all parts
    """
    import re

    name = gguf_path.name
    # Match split pattern: *-00001-of-NNNNN.gguf
    match = re.match(r'^(.+)-(\d{5})-of-(\d{5})\.gguf$', name)
    if not match:
        return gguf_path.stat().st_size

    prefix = match.group(1)
    total_parts = int(match.group(3))
    total_size = 0
    for i in range(1, total_parts + 1):
        part_path = gguf_path.parent / f"{prefix}-{i:05d}-of-{total_parts:05d}.gguf"
        if part_path.exists():
            total_size += part_path.stat().st_size
        else:
            logger.warning(f"Split GGUF part missing: {part_path}")
    return total_size


def is_gguf_file(file_path: Path) -> bool:
    """
    Check if file is GGUF format by reading magic bytes

    Args:
        file_path: Path to potential GGUF file

    Returns:
        True if file starts with 'GGUF' magic bytes
    """
    try:
        with open(file_path, 'rb') as f:
            magic = f.read(4)
            return magic == b'GGUF'
    except (IOError, OSError):
        return False


def get_gguf_layer_count(gguf_path: Path) -> Optional[int]:
    """
    Extract layer count (block_count) from GGUF model metadata.

    GGUF files store layer count as metadata keys like:
    - llama.block_count (Llama/Qwen models)
    - mistral.block_count (Mistral models)

    Args:
        gguf_path: Path to GGUF model file

    Returns:
        Number of transformer layers, or None if not found

    Example:
        >>> get_gguf_layer_count(Path("/models/qwen-30b-q4.gguf"))
        48
    """
    if not gguf_path.exists():
        logger.warning(f"GGUF file not found: {gguf_path}")
        return None

    try:
        import gguf

        with open(gguf_path, "rb") as f:
            try:
                reader = gguf.GGUFReader(f)  # type: ignore[arg-type]

                # Generic pattern: match any *.block_count key
                # (same approach as get_gguf_native_context for context_length)
                for field in reader.fields.values():
                    field_name = field.name.lower()
                    if field_name.endswith('.block_count') or field_name == 'block_count':
                        # Only parse numeric scalar fields. STRING (vtype 8) /
                        # ARRAY (9) would let int(value_array[0]) silently read
                        # the first byte (e.g. 'H'=72) as a "layer count".
                        if not _is_numeric_scalar(field):
                            logger.debug(f"Skipping non-numeric field {field.name}")
                            continue
                        try:
                            value_array = field.parts[-1]
                            layer_count = int(value_array[0]) if len(value_array) > 0 else None
                            if layer_count and layer_count > 0:
                                logger.info(f"✅ Layer count from GGUF metadata ({field.name}): {layer_count}")
                                return layer_count
                        except (IndexError, ValueError, TypeError) as e:
                            logger.debug(f"Failed to parse {field.name}: {e}")
                            continue

                logger.warning("No block_count key found in GGUF metadata")
                return None

            except (ValueError, IndexError) as e:
                logger.error(f"Error parsing GGUF metadata: {e}")
                return None

    except ImportError:
        logger.warning("gguf-py library not installed")
        return None
    except OSError as e:
        logger.error(f"Error reading GGUF file {gguf_path}: {e}")
        return None


def get_gguf_native_context(gguf_path: Path) -> Optional[int]:
    """
    Extract native context length from GGUF model metadata using gguf-py library.

    GGUF files store native context as metadata keys like:
    - llama.context_length (Llama/Qwen models)
    - mistral.context_length (Mistral models)
    - gpt2.context_length (GPT2 models)

    Args:
        gguf_path: Path to GGUF model file

    Returns:
        Native context length in tokens, or None if not found

    Example:
        >>> get_gguf_native_context(Path("/models/qwen-7b-q4.gguf"))
        32768
    """
    if not gguf_path.exists():
        logger.warning(f"GGUF file not found: {gguf_path}")
        return None

    try:
        # Try importing gguf-py library
        import gguf

        with open(gguf_path, "rb") as f:
            try:
                # Load GGUF metadata
                reader = gguf.GGUFReader(f)  # type: ignore[arg-type]

                # Search for ANY key that ends with 'context_length' or contains 'max_position_embeddings'
                # This is more robust than hardcoding all possible architecture names
                for field in reader.fields.values():
                    field_name = field.name.lower()

                    # Match patterns: *.context_length or *max_position_embeddings
                    if field_name.endswith('.context_length') or field_name == 'context_length' or \
                       'max_position_embeddings' in field_name:
                        if not _is_numeric_scalar(field):
                            logger.debug(f"Skipping non-numeric field {field.name}")
                            continue
                        try:
                            # Extract value from memmap array
                            # field.parts is a list where parts[-1] is a memmap with the actual value
                            # For uint32 values: parts[-1] is memmap([value], dtype=uint32)
                            value_array = field.parts[-1]
                            context = int(value_array[0]) if len(value_array) > 0 else None
                            if context and context > 0:
                                logger.info(f"✅ Native context from GGUF metadata ({field.name}): {context:,} tokens")
                                return context
                        except (IndexError, ValueError, TypeError) as e:
                            logger.debug(f"Failed to parse {field.name}: {e}")
                            continue

                # Log available keys for debugging
                all_keys = [f.name for f in reader.fields.values()]
                context_related_keys = [k for k in all_keys if 'context' in k.lower() or 'length' in k.lower()]
                logger.warning("No context_length key found in GGUF metadata")
                logger.warning(f"Available context-related keys: {context_related_keys}")
                logger.debug(f"All metadata keys (first 30): {all_keys[:30]}")
                return None

            except ValueError as e:
                logger.error(f"Error parsing GGUF metadata: {e}")
                return None

    except ImportError:
        logger.warning("gguf-py library not installed - cannot read native context")
        logger.info("Install with: pip install gguf")
        return None
    except (OSError, ValueError, IndexError) as e:
        logger.error(f"Error reading GGUF file {gguf_path}: {e}")
        return None


def get_gguf_chat_template(gguf_path: Path) -> Optional[str]:
    """
    Extract the embedded Jinja chat template from GGUF metadata
    (``tokenizer.chat_template``). For split GGUFs the metadata lives in
    part 1 — the path llama-swap/AIfred reference anyway.

    Returns:
        Template source text, or None if absent/unreadable.
    """
    if not gguf_path.exists():
        logger.warning(f"GGUF file not found: {gguf_path}")
        return None

    try:
        import gguf

        with open(gguf_path, "rb") as f:
            reader = gguf.GGUFReader(f)  # type: ignore[arg-type]
            field = reader.fields.get("tokenizer.chat_template")
            if field is None:
                logger.debug(f"No tokenizer.chat_template in {gguf_path.name}")
                return None
            raw = bytes(field.parts[field.data[0]])
            return raw.decode("utf-8")
    except ImportError:
        logger.warning("gguf-py library not installed - cannot read chat template")
        return None
    except (OSError, ValueError, IndexError, UnicodeDecodeError) as e:
        logger.error(f"Error reading chat template from {gguf_path}: {e}")
        return None


def detect_reasoning_levels(template: str) -> List[str]:
    """
    Detect which ``reasoning_effort`` levels a chat template understands.

    ``chat_template_kwargs`` are passed 1:1 as Jinja variables, so the
    template source is the authoritative capability description: a level
    is supported exactly when the template compares the
    ``reasoning_effort`` variable against its string literal (e.g.
    DeepSeek-V4: ``{%- if thinking and reasoning_effort == 'max' -%}``).

    Only direct comparisons and ``in [...]`` membership tests are
    matched, on the standalone variable (``reasoning_effort_max`` — the
    DeepSeek prompt-text variable — must not match).

    Returns:
        Level names in template order (deduplicated), e.g. ``["max"]``.
        Empty list = template has no steerable effort levels (plain
        on/off thinking or none at all).
    """
    import re

    levels: List[str] = []

    def _add(name: str) -> None:
        if name and name not in levels:
            levels.append(name)

    # reasoning_effort == 'max'  /  reasoning_effort != "high"
    for m in re.finditer(
        r"\breasoning_effort\s*[=!]=\s*['\"]([\w-]+)['\"]", template
    ):
        _add(m.group(1))
    # 'max' == reasoning_effort (reversed operands)
    for m in re.finditer(
        r"['\"]([\w-]+)['\"]\s*[=!]=\s*reasoning_effort\b", template
    ):
        _add(m.group(1))
    # reasoning_effort in ['high', 'max']
    for m in re.finditer(r"\breasoning_effort\s+in\s+\[([^\]]*)\]", template):
        for lit in re.finditer(r"['\"]([\w-]+)['\"]", m.group(1)):
            _add(lit.group(1))

    return levels


def get_gguf_reasoning_levels(gguf_path: Path) -> List[str]:
    """Reasoning-effort levels supported by the model's embedded chat
    template (see :func:`detect_reasoning_levels`). Empty list when the
    template is absent or has no steerable levels."""
    template = get_gguf_chat_template(gguf_path)
    if not template:
        return []
    levels = detect_reasoning_levels(template)
    if levels:
        logger.info(
            f"✅ Reasoning levels from chat template ({gguf_path.name}): {levels}"
        )
    return levels


def resolve_reasoning_levels(model_id: str, force: bool = False) -> List[str]:
    """
    Reasoning-effort levels for a llama.cpp model — cache-first, on miss
    analyzed from the model's GGUF chat template and persisted.

    SSOT for both the model-switch state load (lazy fill) and
    calibration (``force=True`` re-analyzes, e.g. after a re-download
    changed the embedded template).

    Returns [] when the model can't be resolved to an existing GGUF
    (not persisted, so a later attempt retries).
    """
    from .model_vram_cache import (
        get_reasoning_levels_for_model,
        set_reasoning_levels_for_model,
    )

    if not force:
        cached = get_reasoning_levels_for_model(model_id)
        if cached is not None:
            return cached

    from .calibration import parse_llamaswap_config
    from .config import LLAMASWAP_CONFIG_PATH

    try:
        config = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
    except (FileNotFoundError, OSError, ValueError) as e:
        logger.debug(f"Reasoning-level resolve: config unreadable: {e}")
        return []
    entry = config.get(model_id) or {}
    gguf = entry.get("gguf_path")
    if not gguf or not Path(gguf).exists():
        logger.debug(f"Reasoning-level resolve: no GGUF for '{model_id}'")
        return []

    levels = get_gguf_reasoning_levels(Path(gguf))
    set_reasoning_levels_for_model(model_id, levels)
    return levels


def extract_quantization_from_filename(filename: str) -> str:
    """
    Extract quantization level from GGUF filename

    Examples:
        "Qwen3-30B-Instruct-2507-Q4_K_M.gguf" -> "Q4_K_M"
        "model-IQ4_XS.gguf" -> "IQ4_XS"
        "model.gguf" -> "unknown"

    Args:
        filename: GGUF file name

    Returns:
        Quantization string (Q4_K_M, IQ4_XS, etc.)
    """
    import re

    # Match patterns like Q4_K_M, Q5_K_S, Q8_0, IQ4_XS, etc.
    patterns = [
        r'[IQ]+\d+_[A-Z]+',  # IQ4_XS, IQ3_M
        r'Q\d+_[KO]_[MSL]',  # Q4_K_M, Q5_K_S
        r'Q\d+_\d+',         # Q4_0, Q8_0
    ]

    for pattern in patterns:
        match = re.search(pattern, filename)
        if match:
            return match.group(0)

    return "unknown"


def find_gguf_in_huggingface_cache() -> List[GGUFModelInfo]:
    """
    Find GGUF models in HuggingFace cache

    Searches: ~/.cache/huggingface/hub/models--*/snapshots/*/*.gguf

    Returns:
        List of GGUFModelInfo objects
    """
    models: List[GGUFModelInfo] = []
    hf_cache = Path.home() / ".cache" / "huggingface" / "hub"

    if not hf_cache.exists():
        return models

    # Search for GGUF files in HF cache
    for model_dir in hf_cache.glob("models--*"):
        for snapshot_dir in (model_dir / "snapshots").glob("*"):
            for gguf_file in snapshot_dir.glob("*.gguf"):
                # Extract model name from directory
                # models--bartowski--Qwen3-30B-Instruct-2507-GGUF
                author_model = model_dir.name.replace("models--", "").replace("--", "/", 1)

                # Get file size
                size_bytes = gguf_file.stat().st_size
                size_gb = size_bytes / (1024**3)

                # Extract quantization
                quantization = extract_quantization_from_filename(gguf_file.name)

                # Create friendly name
                model_name = f"{author_model} ({quantization})"

                models.append(GGUFModelInfo(
                    path=gguf_file,
                    name=model_name,
                    size_gb=size_gb,
                    quantization=quantization
                ))

    return models


def find_gguf_in_custom_directory(directory: Path) -> List[GGUFModelInfo]:
    """
    Find GGUF models in custom directory

    Args:
        directory: Path to search (e.g., ~/models/)

    Returns:
        List of GGUFModelInfo objects
    """
    models: List[GGUFModelInfo] = []

    if not directory.exists():
        return models

    # Search recursively for .gguf files
    for gguf_file in directory.rglob("*.gguf"):
        # Get file size
        size_bytes = gguf_file.stat().st_size
        size_gb = size_bytes / (1024**3)

        # Extract quantization
        quantization = extract_quantization_from_filename(gguf_file.name)

        # Use filename as model name
        model_name = gguf_file.stem

        models.append(GGUFModelInfo(
            path=gguf_file,
            name=model_name,
            size_gb=size_gb,
            quantization=quantization
        ))

    return models


def find_all_gguf_models() -> List[GGUFModelInfo]:
    """
    Find all GGUF models on the system

    Searches:
    1. HuggingFace cache (~/.cache/huggingface/)
    2. Custom directory (~/models/)

    Returns:
        List of GGUFModelInfo objects sorted by size
    """
    all_models = []

    # 1. HuggingFace cache (primary source)
    all_models.extend(find_gguf_in_huggingface_cache())

    # 2. Custom directory (user downloads)
    custom_dir = Path.home() / "models"
    all_models.extend(find_gguf_in_custom_directory(custom_dir))

    # Remove duplicates (same path)
    seen_paths = set()
    unique_models = []
    for model in all_models:
        if model.path not in seen_paths:
            seen_paths.add(model.path)
            unique_models.append(model)

    # Sort by size (largest first)
    unique_models.sort(key=lambda m: m.size_gb, reverse=True)

    return unique_models


def get_model_info_by_name(model_name: str) -> Optional[GGUFModelInfo]:
    """
    Get GGUF model info by name

    Args:
        model_name: Model name (e.g., "bartowski/Qwen3-30B-Instruct-2507-GGUF (Q4_K_M)")

    Returns:
        GGUFModelInfo if found, None otherwise
    """
    all_models = find_all_gguf_models()

    for model in all_models:
        if model.name == model_name:
            return model

    return None


def estimate_vram_usage(model_size_gb: float, context_size: int, quantization: str) -> float:
    """
    Estimate VRAM usage for a GGUF model

    Args:
        model_size_gb: Model size in GB
        context_size: Context window size (e.g., 32768)
        quantization: Quantization level (Q4_K_M, Q8_0, etc.)

    Returns:
        Estimated VRAM usage in MB
    """
    # Model weights VRAM (convert GB to MB)
    model_vram_mb = model_size_gb * 1024

    # Context cache VRAM (depends on quantization)
    # Q4: ~0.15 MB/token
    # Q5: ~0.18 MB/token
    # Q8: ~0.30 MB/token

    mb_per_token = {
        "Q4": 0.15,
        "Q5": 0.18,
        "Q8": 0.30,
        "IQ4": 0.15,
        "IQ3": 0.12,
    }

    # Detect quantization level from string
    quant_level = "Q4"  # Default
    for key in mb_per_token.keys():
        if key in quantization:
            quant_level = key
            break

    context_vram_mb = context_size * mb_per_token[quant_level]

    # Safety margin (512MB for CUDA kernels, etc.)
    safety_margin_mb = 512

    total_vram_mb = model_vram_mb + context_vram_mb + safety_margin_mb

    return total_vram_mb


if __name__ == "__main__":
    # Test GGUF discovery
    print("=" * 60)
    print("GGUF Model Discovery Test")
    print("=" * 60)
    print()

    models = find_all_gguf_models()

    if not models:
        print("❌ No GGUF models found")
        print()
        print("Download models with:")
        print("  hf download bartowski/Qwen3-30B-Instruct-2507-GGUF \\")
        print("      Qwen3-30B-Instruct-2507-Q4_K_M.gguf --local-dir ~/models/")
    else:
        print(f"✅ Found {len(models)} GGUF model(s):")
        print()
        for model in models:
            print(f"  📦 {model.name}")
            print(f"     Path: {model.path}")
            print(f"     Size: {model.size_gb:.1f}GB")
            print(f"     Quantization: {model.quantization}")
            print()
