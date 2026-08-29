"""
GGUF Model Discovery and Metadata Utilities

Finds GGUF models on the filesystem and extracts metadata
for llama.cpp backend integration.
"""

import logging
from pathlib import Path
from typing import Callable, List, Optional

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


# llama.cpp liest Tensoren ab dieser Groesse zeilenweise von der Platte,
# statt sie in den VRAM zu laden -- ``auto_lazy_min_size`` in
# llama-model-loader.cpp, Standardmodus ``--tensor-read-lazy auto``.
# Solche Tensoren zaehlen NICHT zum VRAM-Bedarf. Betrifft die
# PLE-Tabelle der Qwen4-Architekturen (qwen4exp: 50,7 GiB als EIN Tensor).
# Voraussetzung ist mmap: ``--load-mode dio`` schaltet den Pfad ab und
# zwingt den Tensor in den Hauptspeicher.
GGUF_LAZY_READ_MIN_BYTES = 4 * 1024 ** 3


def get_gguf_part_paths(gguf_path: Path) -> List[Path]:
    """
    Alle Dateien eines GGUF-Modells, inklusive aller Split-Teile.

    Split-GGUFs folgen dem Muster: model-00001-of-00002.gguf,
    model-00002-of-00002.gguf. Fuer Einzeldateien ist das Ergebnis die
    Datei selbst.

    Args:
        gguf_path: Pfad zur GGUF-Datei (erster Teil bei Split-GGUFs)

    Returns:
        Liste der vorhandenen Teil-Pfade
    """
    import re

    # Match split pattern: *-00001-of-NNNNN.gguf
    match = re.match(r'^(.+)-(\d{5})-of-(\d{5})\.gguf$', gguf_path.name)
    if not match:
        return [gguf_path]

    prefix = match.group(1)
    total_parts = int(match.group(3))
    parts: List[Path] = []
    for i in range(1, total_parts + 1):
        part_path = gguf_path.parent / f"{prefix}-{i:05d}-of-{total_parts:05d}.gguf"
        if part_path.exists():
            parts.append(part_path)
        else:
            logger.warning(f"Split GGUF part missing: {part_path}")
    return parts


def get_gguf_total_size(gguf_path: Path) -> int:
    """
    Get total file size for a GGUF model, including all split parts.

    Args:
        gguf_path: Path to GGUF file (first part for split GGUFs)

    Returns:
        Total size in bytes across all parts
    """
    return sum(part.stat().st_size for part in get_gguf_part_paths(gguf_path))


def get_gguf_lazy_tensor_bytes(gguf_path: Path) -> int:
    """
    Bytes der Tensoren, die llama.cpp on-demand von der Platte liest.

    Diese Tensoren belegen kein VRAM. Wer den VRAM-Bedarf eines Modells
    schaetzt, muss sie von der Dateigroesse abziehen -- sonst wird der
    Bedarf um ihre volle Groesse ueberschaetzt (bei Qwen3.8-Flash-Next
    um 50,7 GiB von 157,5 GB).

    Args:
        gguf_path: Pfad zur GGUF-Datei (erster Teil bei Split-GGUFs)

    Returns:
        Summe der Bytes ueber alle Teile; 0 wenn kein Tensor die Schwelle
        erreicht. Ist die Datei nicht lesbar, wird 0 gemeldet (der
        VRAM-Bedarf wird dann wie bisher ueberschaetzt, also konservativ).
    """
    try:
        import gguf
    except ImportError:
        logger.warning("gguf module not available - lazy tensor size unknown")
        return 0

    total = 0
    for part in get_gguf_part_paths(gguf_path):
        try:
            reader = gguf.GGUFReader(part)
        except Exception as exc:
            logger.warning(f"Cannot read GGUF part {part.name}: {exc}")
            continue
        for tensor in reader.tensors:
            n_bytes = int(tensor.n_bytes)
            if n_bytes >= GGUF_LAZY_READ_MIN_BYTES:
                total += n_bytes
                logger.info(
                    f"Lazy-read tensor: {tensor.name} "
                    f"({n_bytes / 1024 ** 3:.1f} GiB) - excluded from VRAM demand"
                )
    return total


# Architektur-Cache: GGUFReader baut beim Öffnen die KOMPLETTE Feldtabelle
# (inkl. Tokenizer-Arrays) — ~12 s pro Großmodell. Der Autoscan liest die
# Architektur aber bei JEDEM llama-swap-Start für alle GGUFs → ohne Cache
# ~45 s Startverzögerung. Gecacht wird pro Pfad mit (size, mtime) als
# Invalidierung; ein neues/ersetztes GGUF wird genau einmal geparst.
_ARCH_CACHE_PATH = Path.home() / ".cache" / "gguf_arch_cache.json"


def _arch_cache_load() -> dict:
    import json
    try:
        return dict(json.loads(_ARCH_CACHE_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def _arch_cache_store(cache: dict) -> None:
    import json
    try:
        _ARCH_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ARCH_CACHE_PATH.write_text(
            json.dumps(cache, indent=1), encoding="utf-8"
        )
    except OSError as e:
        logger.debug(f"arch cache write failed: {e}")


def get_gguf_architecture(gguf_path: Path) -> Optional[str]:
    """``general.architecture`` aus den GGUF-Metadaten (z.B. "qwen3", "bert").

    Embedding-Modelle (bert-Familie) tragen hier ihre Nicht-Kausal-
    Architektur — der Autoscan nutzt das, um sie nicht als Chat-Profile
    anzulegen. Ergebnis wird pro (Pfad, Größe, mtime) gecacht — siehe
    ``_ARCH_CACHE_PATH``."""
    if not gguf_path.exists():
        return None
    stat = gguf_path.stat()
    key = str(gguf_path)
    cache = _arch_cache_load()
    entry = cache.get(key)
    if (
        isinstance(entry, dict)
        and entry.get("size") == stat.st_size
        and entry.get("mtime") == int(stat.st_mtime)
    ):
        return str(entry["arch"]) if entry.get("arch") else None
    try:
        import gguf

        with open(gguf_path, "rb") as f:
            reader = gguf.GGUFReader(f)  # type: ignore[arg-type]
            field = reader.fields.get("general.architecture")
            arch = (
                bytes(field.parts[-1]).decode("utf-8", errors="replace")
                if field is not None else None
            )
    except ImportError:
        logger.warning("gguf-py library not installed")
        return None
    except (OSError, ValueError, IndexError) as e:
        logger.error(f"Error reading GGUF architecture {gguf_path}: {e}")
        return None
    # Auch "kein Feld" (arch=None → "") cachen — für dieselbe Datei ist
    # das deterministisch; nur echte Lesefehler werden nicht gecacht.
    cache[key] = {
        "size": stat.st_size,
        "mtime": int(stat.st_mtime),
        "arch": arch or "",
    }
    _arch_cache_store(cache)
    return arch


def _scan_gguf_numeric_field(
    gguf_path: Path,
    key_match: Callable[[str], bool],
    description: str,
    not_found_hints: Optional[list[str]] = None,
) -> Optional[int]:
    """Shared GGUF metadata scan: return the first positive int value of a
    field whose lowercased name satisfies ``key_match``.

    Only numeric scalar fields are parsed — STRING (vtype 8) / ARRAY (9)
    would let ``int(value_array[0])`` silently read the first byte
    (e.g. 'H'=72) as a value.
    """
    if not gguf_path.exists():
        logger.warning(f"GGUF file not found: {gguf_path}")
        return None

    try:
        import gguf

        with open(gguf_path, "rb") as f:
            try:
                reader = gguf.GGUFReader(f)  # type: ignore[arg-type]

                for field in reader.fields.values():
                    if not key_match(field.name.lower()):
                        continue
                    if not _is_numeric_scalar(field):
                        logger.debug(f"Skipping non-numeric field {field.name}")
                        continue
                    try:
                        value_array = field.parts[-1]
                        value = int(value_array[0]) if len(value_array) > 0 else None
                        if value and value > 0:
                            logger.info(
                                f"✅ {description} from GGUF metadata ({field.name}): {value:,}"
                            )
                            return value
                    except (IndexError, ValueError, TypeError) as e:
                        logger.debug(f"Failed to parse {field.name}: {e}")
                        continue

                logger.warning(f"No {description} key found in GGUF metadata")
                if not_found_hints:
                    all_keys = [f.name for f in reader.fields.values()]
                    related = [
                        k for k in all_keys
                        if any(h in k.lower() for h in not_found_hints)
                    ]
                    logger.warning(f"Available related keys: {related}")
                    logger.debug(f"All metadata keys (first 30): {all_keys[:30]}")
                return None

            except (ValueError, IndexError) as e:
                logger.error(f"Error parsing GGUF metadata: {e}")
                return None

    except ImportError:
        logger.warning("gguf-py library not installed")
        return None
    except (OSError, ValueError, IndexError) as e:
        logger.error(f"Error reading GGUF file {gguf_path}: {e}")
        return None


def get_gguf_layer_count(gguf_path: Path) -> Optional[int]:
    """
    Extract layer count (block_count) from GGUF model metadata.

    GGUF files store layer count as metadata keys like
    ``llama.block_count`` / ``mistral.block_count`` — matched generically.

    Returns:
        Number of transformer layers, or None if not found
    """
    return _scan_gguf_numeric_field(
        gguf_path,
        lambda n: n.endswith(".block_count") or n == "block_count",
        "Layer count",
    )


def get_gguf_native_context(gguf_path: Path) -> Optional[int]:
    """
    Extract native context length from GGUF model metadata.

    Matches any ``*.context_length`` key (architecture-agnostic) plus
    ``max_position_embeddings`` variants.

    Returns:
        Native context length in tokens, or None if not found
    """
    return _scan_gguf_numeric_field(
        gguf_path,
        lambda n: n.endswith(".context_length") or n == "context_length"
        or "max_position_embeddings" in n,
        "Native context",
        not_found_hints=["context", "length"],
    )


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

    # Alias assignments first — Qwen3.8 renames the variable before every
    # comparison: {%- set resolved_reasoning_effort =
    # reasoning_effort|default('xhigh') %}. All comparisons then run on the
    # alias, so the patterns below must match alias names too. The
    # default() literal itself is a valid level (it is what the template
    # resolves to when the variable is unset).
    var_names = ["reasoning_effort"]
    for m in re.finditer(
        r"\bset\s+(\w+)\s*=\s*reasoning_effort\b"
        r"(?:\s*\|\s*default\(\s*['\"]([\w-]+)['\"])?",
        template,
    ):
        if m.group(1) not in var_names:
            var_names.append(m.group(1))
        if m.group(2):
            _add(m.group(2))

    for var in var_names:
        v = re.escape(var)
        # var == 'max'  /  var != "high"
        for m in re.finditer(
            rf"(?<!\w){v}\s*[=!]=\s*['\"]([\w-]+)['\"]", template
        ):
            _add(m.group(1))
        # 'max' == var (reversed operands)
        for m in re.finditer(
            rf"['\"]([\w-]+)['\"]\s*[=!]=\s*{v}(?!\w)", template
        ):
            _add(m.group(1))
        # var in ['high', 'max']  /  var not in ('xhigh', 'medium', 'low')
        # — Jinja tuples use round brackets, lists use square ones.
        for m in re.finditer(
            rf"(?<!\w){v}\s+(?:not\s+)?in\s*[\[\(]([^\]\)]*)[\]\)]", template
        ):
            for lit in re.finditer(r"['\"]([\w-]+)['\"]", m.group(1)):
                _add(lit.group(1))

    # Self-remapping aliases: {%- if X == 'A' %}{%- set X = 'B' %}{%- endif %}
    # collapses 'A' into 'B' before any behavior-branch is chosen — Qwen3.8
    # does this for "high" (silently promoted to "xhigh"). Offering both as
    # separate dropdown entries is misleading: they produce byte-identical
    # prompts. Drop the source name, keep the target (it was already
    # discovered independently via its own comparison/default() literal).
    for var in var_names:
        v = re.escape(var)
        # Bounded lookahead (not a {}-exclusion): Jinja tags themselves are
        # spelled with { and }, so excluding those chars — the first attempt
        # here — blocked matching through the tag delimiters separating the
        # comparison from its own {%- set %}. A short character cap keeps
        # the match local to "this if-branch" without needing to parse
        # actual Jinja block nesting.
        for m in re.finditer(
            rf"(?<!\w){v}\s*==\s*['\"]([\w-]+)['\"].{{0,60}}?"
            rf"set\s+{v}\s*=\s*['\"]([\w-]+)['\"]",
            template, re.S,
        ):
            source, target = m.group(1), m.group(2)
            if source in levels and source != target:
                levels.remove(source)

    # Stable low→high ordering for the UI dropdown (matches the DeepSeek
    # template's natural order: high before max). Template discovery order
    # is arbitrary (Qwen3.8 yields xhigh, high, low, medium); a fixed rank
    # of the common effort names sorts it — unknown names keep their
    # discovery order at the end.
    _rank = {"none": 0, "minimal": 1, "low": 2, "medium": 3,
             "high": 4, "xhigh": 5, "max": 6}
    return sorted(
        levels,
        key=lambda n: (_rank.get(n, len(_rank)), levels.index(n)),
    )


def detect_reasoning_default(template: str) -> Optional[str]:
    """The level the template resolves to when ``reasoning_effort`` is
    unset — the ``default()`` literal (Qwen3.8:
    ``reasoning_effort|default('xhigh')`` → ``xhigh``). ``None`` when the
    template has no such default (DeepSeek-V4: plain thinking without an
    effort instruction); the UI then shows a plain "On" label."""
    import re

    m = re.search(
        r"\breasoning_effort\s*\|\s*default\(\s*['\"]([\w-]+)['\"]", template
    )
    return m.group(1) if m else None


def get_gguf_reasoning_info(gguf_path: Path) -> tuple[List[str], Optional[str]]:
    """(effort levels, default level) from the model's embedded chat
    template. ``([], None)`` when the template is absent or has no
    steerable levels."""
    template = get_gguf_chat_template(gguf_path)
    if not template:
        return [], None
    levels = detect_reasoning_levels(template)
    default = detect_reasoning_default(template) if levels else None
    if levels:
        logger.info(
            f"✅ Reasoning levels from chat template ({gguf_path.name}): "
            f"{levels} (default: {default})"
        )
    return levels, default


def get_checkpoint_reasoning_info(checkpoint_dir: Path) -> tuple[List[str], Optional[str]]:
    """(effort levels, default level) from a vLLM checkpoint directory.

    Liest das Chat-Template aus ``chat_template.jinja`` (Standard bei
    Qwen-Checkpoints) oder ``tokenizer_config.json`` (``chat_template``-
    Schlüssel — die zweite übliche HF-Ablage) und schickt es durch
    denselben Detektor wie die GGUF-Templates.
    """
    template = None
    jinja = checkpoint_dir / "chat_template.jinja"
    if jinja.exists():
        template = jinja.read_text()
    else:
        tok_cfg = checkpoint_dir / "tokenizer_config.json"
        if tok_cfg.exists():
            import json
            template = json.loads(tok_cfg.read_text()).get("chat_template")
    if not template:
        return [], None
    levels = detect_reasoning_levels(template)
    default = detect_reasoning_default(template) if levels else None
    if levels:
        logger.info(
            f"✅ Reasoning levels from checkpoint template ({checkpoint_dir.name}): "
            f"{levels} (default: {default})"
        )
    return levels, default


def resolve_reasoning_levels(model_id: str, force: bool = False) -> List[str]:
    """
    Reasoning-effort levels for a llama-swap model — cache-first, on miss
    analyzed from the model's chat template and persisted. GGUF-Einträge
    lesen das eingebettete Template, ``-vllm``-Einträge die
    ``chat_template.jinja`` des Checkpoint-Verzeichnisses.

    SSOT for both the model-switch state load (lazy fill) and
    calibration (``force=True`` re-analyzes, e.g. after a re-download
    changed the embedded template).

    Returns [] when the model can't be resolved to an existing GGUF or
    checkpoint dir (not persisted, so a later attempt retries).
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
        logger.debug(f"Reasoning-level resolve: no model path for '{model_id}'")
        return []

    model_path = Path(gguf)
    if model_path.is_dir():
        # vLLM-Eintrag: --model zeigt auf ein Checkpoint-Verzeichnis
        levels, default = get_checkpoint_reasoning_info(model_path)
    else:
        levels, default = get_gguf_reasoning_info(model_path)
    set_reasoning_levels_for_model(model_id, levels, default)
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
