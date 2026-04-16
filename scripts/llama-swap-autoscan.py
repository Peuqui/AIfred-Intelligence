#!/usr/bin/env python3
"""
llama-swap Autoscan - Automatic model discovery and configuration

Scans for new GGUF models (Ollama blobs, HuggingFace cache, ~/models/)
and automatically:
1. Creates symlinks for Ollama blobs with descriptive filenames
2. Cleans up dead symlinks and stale config entries for removed models
3. Adds new model entries to llama-swap-config.yaml
4. Creates preliminary VRAM cache entries

Designed to run as ExecStartPre before llama-swap service starts.
"""

import json
import re
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Direct import of nvidia_smi module (NOT via aifred.lib — that triggers Reflex app init)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "aifred" / "lib"))
import nvidia_smi

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MODELS_DIR = Path.home() / "models"

OLLAMA_PATHS = [
    Path("/usr/share/ollama/.ollama/models"),   # System-Service
    Path.home() / ".ollama" / "models",         # User-Installation
]

HF_CACHE_DIR = Path.home() / ".cache" / "huggingface" / "hub"

LLAMASWAP_CONFIG = Path.home() / ".config" / "llama-swap" / "config.yaml"
# Persists models that failed the compatibility test — not re-tested on subsequent runs.
# Delete an entry manually to re-test after a llama.cpp update.
AUTOSCAN_SKIP_FILE = LLAMASWAP_CONFIG.parent / "autoscan-skip.json"


# ---------------------------------------------------------------------------
# Config IO — SSOT for llama-swap config.yaml read/write
# ---------------------------------------------------------------------------
#
# The config has strict structural invariants that must hold after every write:
#   1. Content ends with a newline
#   2. A 'models:' header exists before any model entries
#   3. No duplicate model keys under 'models:'
#
# All writes to LLAMASWAP_CONFIG go through _write_config(). Any other code
# path that writes the file directly is a bug. The previous design allowed
# multiple functions (write_gpu_fingerprint, append_models_to_yaml, …) to
# write independently, which produced broken files that caused an append-
# spiral of duplicates when autoscan re-read them.

_MODELS_HEADER_RE = re.compile(r'^models:\s*$', re.MULTILINE)
_MODEL_KEY_RE = re.compile(r'^  ([A-Za-z0-9][A-Za-z0-9._-]*):\s*$', re.MULTILINE)
_GPU_FINGERPRINT_PREFIX = "# gpu_hardware:"


def _enforce_config_invariants(content: str) -> str:
    """Return ``content`` normalized so all structural invariants hold.

    - Trailing newline
    - ``models:`` header present (inserted after the GPU fingerprint comment
      if that exists on line 1, otherwise prepended)
    """
    if not content.endswith("\n"):
        content += "\n"

    if _MODELS_HEADER_RE.search(content):
        return content

    # No 'models:' header — heal by inserting one at the correct position.
    lines = content.splitlines(keepends=True)
    if lines and lines[0].startswith(_GPU_FINGERPRINT_PREFIX):
        lines.insert(1, "models:\n")
    else:
        lines.insert(0, "models:\n")
    return "".join(lines)


def _assert_no_duplicate_model_keys(content: str, config_path: Path) -> None:
    """Raise if any 2-space-indent model key appears more than once.

    This catches the append-spiral bug class at write time rather than at
    llama-swap parse time (which only surfaces after systemd restart).
    """
    keys = _MODEL_KEY_RE.findall(content)
    seen: set[str] = set()
    duplicates: list[str] = []
    for key in keys:
        if key in seen and key not in duplicates:
            duplicates.append(key)
        seen.add(key)
    if duplicates:
        raise RuntimeError(
            f"Refusing to write {config_path}: duplicate model keys "
            f"{duplicates} — this indicates a bug in the caller."
        )


def _write_config(config_path: Path, content: str) -> None:
    """Write ``content`` to ``config_path`` after enforcing all invariants.

    This is the **only** function that should call ``write_text`` on
    LLAMASWAP_CONFIG. Raises ``RuntimeError`` on structural corruption
    rather than silently writing a broken file.
    """
    content = _enforce_config_invariants(content)
    _assert_no_duplicate_model_keys(content, config_path)
    config_path.write_text(content)

# VRAM cache lives in the AIfred data directory
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
VRAM_CACHE_FILE = PROJECT_ROOT / "data" / "model_vram_cache.json"

LLAMA_SERVER_BIN = Path.home() / "llama.cpp" / "build" / "bin" / "llama-server"
LLAMA_FIT_PARAMS_BIN = Path.home() / "llama.cpp" / "build" / "bin" / "llama-fit-params"
DEFAULT_TTL_SMALL = 900   # < 20 GB models (15 min)
DEFAULT_TTL_MEDIUM = 1200  # 20-50 GB models (20 min)
DEFAULT_TTL_LARGE = 1800   # > 50 GB models (30 min)
DEFAULT_NGL = 99
DEFAULT_FLAGS_BASE = "--flash-attn on -np 1 -t 4 --mlock --direct-io --jinja --no-context-shift"
DEFAULT_CONTEXT = 32768  # Fallback if GGUF metadata unreadable
FALLBACK_CONTEXT = 32768  # Reduced context when native context doesn't fit

# If GGUF file size exceeds this fraction of the largest GPU's VRAM → use tensor-split
MULTI_GPU_VRAM_THRESHOLD = 0.80

# Minimum free VRAM per GPU after model load (MiB) — must cover KV cache allocation blocks
VRAM_SAFETY_MARGIN_MB = 1024

# No size filter - even tiny LLMs (135M edge models) should be loadable


# ---------------------------------------------------------------------------
# VRAM and model size helpers
# ---------------------------------------------------------------------------

def get_total_vram_mb() -> int:
    """Query total VRAM across all NVIDIA GPUs via nvidia-smi. Returns 0 if unavailable."""
    rows = nvidia_smi.query("memory.total")
    if not rows:
        return 0
    return sum(int(row["memory.total"]) for row in rows)


def get_per_gpu_vram_mb() -> list[int]:
    """Query VRAM per GPU, sorted by size descending (matches CUDA_DEVICE_ORDER=FASTEST_FIRST)."""
    rows = nvidia_smi.query("memory.total")
    if not rows:
        return []
    gpus = [int(row["memory.total"]) for row in rows]
    gpus.sort(reverse=True)
    return gpus


def get_gguf_total_size(gguf_path: Path) -> int:
    """Get total file size in bytes. Sums all parts for split GGUFs."""
    resolved = gguf_path.resolve()
    name = resolved.name

    # Split GGUF: name-00001-of-00005.gguf
    match = re.match(r'^(.+)-(\d{5})-of-(\d{5})\.gguf$', name)
    if match:
        base, _, total_parts = match.groups()
        total_size = 0
        for i in range(1, int(total_parts) + 1):
            part = resolved.parent / f"{base}-{i:05d}-of-{total_parts}.gguf"
            if part.exists():
                total_size += part.stat().st_size
        return total_size

    return resolved.stat().st_size


def build_gpu_flags(gguf_path: Path, per_gpu_vram: list[int]) -> str:
    """Build GPU distribution flags based on model size vs available GPUs.

    Returns:
        "" — single GPU or model fits on one GPU (no extra flags needed)
        "-sm layer --tensor-split 2,1 -fit off" — large model spread across GPUs
    """
    if len(per_gpu_vram) <= 1:
        return ""

    largest_gpu_mb = per_gpu_vram[0]
    gguf_size_mb = get_gguf_total_size(gguf_path) / (1024 * 1024)
    ratio = gguf_size_mb / largest_gpu_mb

    if ratio <= MULTI_GPU_VRAM_THRESHOLD:
        print(f"    GPU: single (model {gguf_size_mb:.0f} MB = {ratio:.0%} of largest GPU {largest_gpu_mb} MB)")
        return ""

    # Model needs multiple GPUs — calculate tensor-split from VRAM proportions
    min_vram = min(per_gpu_vram)
    split_parts = [max(1, round(v / min_vram)) for v in per_gpu_vram]
    split_str = ",".join(str(p) for p in split_parts)

    total_vram_mb = sum(per_gpu_vram)
    print(
        f"    GPU: tensor-split {split_str} "
        f"(model {gguf_size_mb:.0f} MB = {ratio:.0%} of largest {largest_gpu_mb} MB, "
        f"total {total_vram_mb} MB)"
    )
    return f"-sm layer --tensor-split {split_str} -fit off -b 512 -ub 256"


# ---------------------------------------------------------------------------
# GPU hardware fingerprint — detect hardware changes across restarts
# ---------------------------------------------------------------------------

def get_gpu_names() -> list[str]:
    """Query GPU model names, sorted by VRAM descending (matches FASTEST_FIRST)."""
    rows = nvidia_smi.query("memory.total,name")
    if not rows:
        return []
    gpus = [(int(row["memory.total"]), row["name"]) for row in rows]
    gpus.sort(key=lambda x: x[0], reverse=True)
    return [name for _, name in gpus]


def _short_gpu_name(name: str) -> str:
    """Shorten GPU name for fingerprint (remove vendor prefixes, collapse spaces)."""
    for prefix in ("NVIDIA ", "GeForce ", "Quadro "):
        name = name.replace(prefix, "")
    return name.strip().replace(" ", "_")


def build_gpu_fingerprint() -> str:
    """Build hardware fingerprint string from current GPUs.

    Format: "RTX_8000:48564,P40:24576" — sorted by VRAM descending.
    """
    names = get_gpu_names()
    vrams = get_per_gpu_vram_mb()
    parts = []
    for name, vram in zip(names, vrams):
        parts.append(f"{_short_gpu_name(name)}:{vram}")
    return ",".join(parts)


def read_gpu_fingerprint(config_path: Path) -> Optional[str]:
    """Read stored GPU fingerprint from config header comment."""
    if not config_path.exists():
        return None
    first_line = config_path.read_text().split("\n", 1)[0]
    m = re.match(r'^#\s*gpu_hardware:\s*(.+)$', first_line)
    return m.group(1).strip() if m else None


def write_gpu_fingerprint(config_path: Path, fingerprint: str) -> None:
    """Write or update GPU fingerprint as first line comment in config."""
    new_line = f"# gpu_hardware: {fingerprint}\n"
    if not config_path.exists():
        _write_config(config_path, new_line)
        return
    content = config_path.read_text()
    if content.startswith(_GPU_FINGERPRINT_PREFIX):
        # Replace existing fingerprint line
        rest = content.split("\n", 1)[1] if "\n" in content else ""
        _write_config(config_path, new_line + rest)
    else:
        _write_config(config_path, new_line + content)


def _parse_fingerprint_vrams(fingerprint: str) -> list[int]:
    """Extract VRAM values from fingerprint string."""
    vrams = []
    for part in fingerprint.split(","):
        if ":" in part:
            vram_str = part.rsplit(":", 1)[1]
            try:
                vrams.append(int(vram_str))
            except ValueError:
                pass
    return vrams


def gpu_hardware_changed(stored: str, current: str) -> bool:
    """Compare GPU fingerprints. Tolerant to ±512 MB VRAM fluctuation per GPU."""
    stored_vrams = _parse_fingerprint_vrams(stored)
    current_vrams = _parse_fingerprint_vrams(current)
    if len(stored_vrams) != len(current_vrams):
        return True
    for s, c in zip(stored_vrams, current_vrams):
        if abs(s - c) > 512:
            return True
    return False


def update_all_tensor_splits(config_path: Path, per_gpu_vram: list[int]) -> int:
    """Update tensor-split in ALL local model profiles to match current GPU layout.

    Skips RPC profiles (--rpc in cmd). Does NOT touch context (-c) or NGL (-ngl).
    Returns count of updated model profiles.
    """
    if not config_path.exists():
        return 0

    lines = config_path.read_text().splitlines(keepends=True)
    updated_count = 0

    # Regex patterns for parsing cmd strings
    ts_pattern = re.compile(r'(--tensor-split|-ts)\s+[\d.,]+')
    sm_pattern = re.compile(r'-sm\s+\w+')
    fit_pattern = re.compile(r'-fit\s+\w+')
    model_pattern = re.compile(r'--model\s+(\S+)')
    rpc_pattern = re.compile(r'--rpc\s+')
    dev_pattern = re.compile(r'-dev\s+\S+')

    i = 0
    while i < len(lines):
        line = lines[i]

        # Detect cmd lines (single-line or multi-line YAML)
        # Match: "    cmd: '...'" or "    cmd: |" followed by indented content
        if not re.match(r'\s+cmd:', line):
            i += 1
            continue

        # Collect full cmd text (may span multiple lines)
        cmd_start = i
        cmd_text = line
        if re.search(r"cmd:\s*[|>]", line):
            # YAML block scalar (| or >) — collect indented continuation lines
            j = i + 1
            while j < len(lines) and lines[j].startswith("      "):
                cmd_text += lines[j]
                j += 1
            cmd_end = j - 1
        elif re.search(r"cmd:\s*'", line):
            # Quoted string — find the closing quote
            if line.rstrip().endswith("'") and line.count("'") >= 2:
                # Opening and closing quote on same line
                cmd_end = i
            else:
                # Multi-line quoted string — collect until closing quote
                j = i + 1
                while j < len(lines):
                    cmd_text += lines[j]
                    if lines[j].rstrip().endswith("'"):
                        break
                    j += 1
                cmd_end = j
        else:
            # Unquoted single-line cmd
            cmd_end = i

        # Skip RPC profiles
        if rpc_pattern.search(cmd_text):
            i = cmd_end + 1
            continue

        # Extract GGUF path
        model_match = model_pattern.search(cmd_text)
        if not model_match:
            i = cmd_end + 1
            continue

        gguf_path = Path(model_match.group(1))

        # Resolve symlinks and check existence
        try:
            resolved = gguf_path.resolve()
            if not resolved.exists():
                i = cmd_end + 1
                continue
        except Exception:
            i = cmd_end + 1
            continue

        # Calculate what the tensor-split SHOULD be
        new_gpu_flags = build_gpu_flags_silent(gguf_path, per_gpu_vram)
        has_old_ts = bool(ts_pattern.search(cmd_text))

        if new_gpu_flags:
            # Model needs multi-GPU
            new_ts_match = re.search(r'--tensor-split\s+([\d.,]+)', new_gpu_flags)
            new_ts_val = new_ts_match.group(1) if new_ts_match else ""

            if has_old_ts:
                # Replace existing tensor-split value
                old_ts_match = ts_pattern.search(cmd_text)
                if old_ts_match:
                    old_ts_text = old_ts_match.group(0)
                    new_ts_text = f"{old_ts_match.group(1)} {new_ts_val}"
                    for idx in range(cmd_start, cmd_end + 1):
                        if old_ts_text in lines[idx]:
                            lines[idx] = lines[idx].replace(old_ts_text, new_ts_text)
                            updated_count += 1
                            break
            else:
                # No tensor-split yet — need to add multi-GPU flags
                # Remove -dev if present (was single-GPU)
                for idx in range(cmd_start, cmd_end + 1):
                    if dev_pattern.search(lines[idx]):
                        lines[idx] = dev_pattern.sub("", lines[idx])

                # Insert new flags before --flash-attn or at end of first cmd line
                inserted = False
                for idx in range(cmd_start, cmd_end + 1):
                    if "--flash-attn" in lines[idx]:
                        lines[idx] = lines[idx].replace(
                            "--flash-attn",
                            f"-sm layer --tensor-split {new_ts_val} -fit off --flash-attn",
                        )
                        inserted = True
                        updated_count += 1
                        break
                if not inserted:
                    # Fallback: append to last cmd line
                    lines[cmd_end] = lines[cmd_end].rstrip("\n") + f" -sm layer --tensor-split {new_ts_val} -fit off\n"
                    updated_count += 1
        else:
            # Model fits on single GPU — remove tensor-split if present
            if has_old_ts:
                for idx in range(cmd_start, cmd_end + 1):
                    lines[idx] = ts_pattern.sub("", lines[idx])
                    lines[idx] = sm_pattern.sub("", lines[idx])
                    lines[idx] = fit_pattern.sub("", lines[idx])
                    # Clean up double spaces
                    lines[idx] = re.sub(r'  +', ' ', lines[idx])
                updated_count += 1

        i = cmd_end + 1

    _write_config(config_path, "".join(lines))
    return updated_count


def build_gpu_flags_silent(gguf_path: Path, per_gpu_vram: list[int]) -> str:
    """Like build_gpu_flags() but without print output. For use in update_all_tensor_splits."""
    if len(per_gpu_vram) <= 1:
        return ""

    largest_gpu_mb = per_gpu_vram[0]
    try:
        gguf_size_mb = get_gguf_total_size(gguf_path) / (1024 * 1024)
    except Exception:
        return ""

    if gguf_size_mb / largest_gpu_mb <= MULTI_GPU_VRAM_THRESHOLD:
        return ""

    min_vram = min(per_gpu_vram)
    split_parts = [max(1, round(v / min_vram)) for v in per_gpu_vram]
    split_str = ",".join(str(p) for p in split_parts)
    return f"-sm layer --tensor-split {split_str} -fit off"


# ---------------------------------------------------------------------------
# VRAM calibration via llama-fit-params
# ---------------------------------------------------------------------------

def _build_fit_params_cmd(
    gguf_path: Path,
    context: int,
    ngl: int,
    gpu_flags: str,
    kv_quant: Optional[str] = None,
    mmproj_path: Optional[Path] = None,
) -> list[str]:
    """Build llama-fit-params command for per-GPU VRAM projection."""
    cmd = [
        str(LLAMA_FIT_PARAMS_BIN),
        "--model", str(gguf_path.resolve()),
        "-ngl", str(ngl),
        "-c", str(context),
        "--flash-attn", "on",
        "-np", "1",
    ]
    if mmproj_path:
        cmd.extend(["--mmproj", str(mmproj_path.resolve())])
    if kv_quant:
        cmd.extend(["-ctk", kv_quant, "-ctv", kv_quant])
    # GPU flags: -sm, --tensor-split, -b, -ub (skip -fit which is server-only)
    if gpu_flags:
        parts = gpu_flags.split()
        i = 0
        while i < len(parts):
            if parts[i] == "-fit":
                i += 2  # skip -fit and its value
            else:
                cmd.append(parts[i])
                i += 1
    return cmd


def _fit_params_per_gpu(
    gguf_path: Path,
    context: int,
    ngl: int,
    gpu_flags: str,
    kv_quant: Optional[str] = None,
    mmproj_path: Optional[Path] = None,
) -> dict[str, dict[str, int]]:
    """
    Run llama-fit-params and parse per-GPU VRAM projections.

    Returns dict like {"CUDA0": {"total": 45355, "used": 43710, "free": 1478}}.
    Empty dict if fit-params unavailable or parsing fails.
    """
    cmd = _build_fit_params_cmd(gguf_path, context, ngl, gpu_flags, kv_quant, mmproj_path)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}

    # Per-GPU lines appear in stderr (present even on exit code 1)
    output = result.stderr + result.stdout

    # Multi-GPU: per-GPU memory lines
    pattern = re.compile(
        r'(CUDA\d+)\s+\([^)]+\)\s*:\s*(\d+)\s+total\s*,\s*(\d+)\s+used\s*,\s*(-?\d+)\s+free'
    )
    gpus: dict[str, dict[str, int]] = {}
    for m in pattern.finditer(output):
        gpus[m.group(1)] = {
            "total": int(m.group(2)),
            "used": int(m.group(3)),
            "free": int(m.group(4)),
        }

    # Single-GPU fallback: parse summary lines
    if not gpus:
        proj_match = re.search(
            r'projected to use\s+(\d+)\s+MiB.*?vs\.\s+(\d+)\s+MiB',
            output,
        )
        leave_match = re.search(r'will leave\s+(-?\d+)', output)
        if proj_match:
            used = int(proj_match.group(1))
            free_after = int(leave_match.group(1)) if leave_match else int(proj_match.group(2)) - used
            gpus["CUDA0"] = {
                "total": used + free_after,
                "used": used,
                "free": free_after,
            }

    return gpus


def _find_best_ngl(
    gguf_path: Path,
    context: int,
    gpu_flags: str,
    kv_quant: Optional[str] = None,
    mmproj_path: Optional[Path] = None,
) -> tuple[int, dict[str, dict[str, int]]]:
    """
    Binary search for the highest NGL where all GPUs have free >= safety margin.

    Returns (best_ngl, gpu_projections). Returns (0, {}) if no NGL works.
    """
    ngl_low = 1
    ngl_high = 99
    best_ngl = 0
    best_gpus: dict[str, dict[str, int]] = {}

    while ngl_low <= ngl_high:
        ngl_mid = (ngl_low + ngl_high) // 2
        gpus = _fit_params_per_gpu(gguf_path, context, ngl_mid, gpu_flags, kv_quant, mmproj_path)
        if not gpus:
            ngl_high = ngl_mid - 1
            continue
        min_free = min(g["free"] for g in gpus.values())
        if min_free >= VRAM_SAFETY_MARGIN_MB:
            best_ngl = ngl_mid
            best_gpus = gpus
            ngl_low = ngl_mid + 1
        else:
            ngl_high = ngl_mid - 1

    return best_ngl, best_gpus


def calibrate_model_fit_params(
    model: dict,
    server_bin: Path,
    per_gpu_vram: list[int],
) -> bool:
    """
    Calibrate a model using llama-fit-params for per-GPU VRAM projection.

    Sets calibrated_context, calibrated_kv_quant, calibrated_gpu_flags, calibrated_ngl
    on the model dict.

    Returns True if calibration succeeded.
    """
    gguf_path = model["path"]
    native_context = get_native_context(gguf_path)
    gpu_flags = build_gpu_flags(gguf_path, per_gpu_vram)
    model_mb = get_gguf_total_size(gguf_path) / (1024 * 1024)
    mmproj = model.get("mmproj_path")

    print(f"  {model['name']} ({model_mb:,.0f} MB, native context: {native_context:,}):")

    if not LLAMA_FIT_PARAMS_BIN.exists():
        print("    ! llama-fit-params not found, using safe defaults")
        model["calibrated_context"] = min(native_context, FALLBACK_CONTEXT)
        model["calibrated_kv_quant"] = "q4_0"
        model["calibrated_gpu_flags"] = gpu_flags
        model["calibrated_ngl"] = DEFAULT_NGL
        return True

    # Strategy: always try ngl=99 first (full GPU offload).
    # If model doesn't fit at ngl=99 even with minimal context, reduce NGL (hybrid mode).
    # AIfred calibration handles fine-tuning of context afterwards.
    for kv in [None, "q8_0", "q4_0"]:
        kv_label = kv or "f16"

        # Binary search for highest context that fits at ngl=99
        ctx_low = min(FALLBACK_CONTEXT, native_context)
        ctx_high = native_context
        best_ctx = 0
        best_ctx_ngl = DEFAULT_NGL
        best_ctx_gpus: dict[str, dict[str, int]] = {}

        while ctx_low <= ctx_high:
            ctx_mid = ((ctx_low + ctx_high) // 2 + 255) & ~255  # round up to 256

            gpus = _fit_params_per_gpu(
                gguf_path, ctx_mid, DEFAULT_NGL, gpu_flags, kv, mmproj,
            )
            if gpus:
                min_free = min(g["free"] for g in gpus.values())
                fits = min_free >= VRAM_SAFETY_MARGIN_MB
            else:
                fits = False

            if fits:
                best_ctx = ctx_mid
                best_ctx_gpus = gpus
                ctx_low = ctx_mid + 256
            else:
                ctx_high = ctx_mid - 256

        # ngl=99 doesn't fit at all — fall back to NGL search (hybrid CPU/GPU)
        if best_ctx == 0:
            print(f"    ⚠ KV={kv_label}, ngl=99 doesn't fit — searching for best NGL...")
            ctx_low = min(FALLBACK_CONTEXT, native_context)
            ctx_high = native_context
            while ctx_low <= ctx_high:
                ctx_mid = ((ctx_low + ctx_high) // 2 + 255) & ~255
                ngl_result, gpus = _find_best_ngl(
                    gguf_path, ctx_mid, gpu_flags, kv, mmproj,
                )
                if ngl_result > 0:
                    best_ctx = ctx_mid
                    best_ctx_ngl = ngl_result
                    best_ctx_gpus = gpus
                    ctx_low = ctx_mid + 256
                else:
                    ctx_high = ctx_mid - 256

        if best_ctx > 0:
            min_free = min(g["free"] for g in best_ctx_gpus.values())
            ngl_info = f", ngl={best_ctx_ngl}" if best_ctx_ngl != DEFAULT_NGL else ""
            print(f"    ✓ KV={kv_label}, context={best_ctx:,}{ngl_info} (min free: {min_free} MB)")
            model["calibrated_context"] = best_ctx
            model["calibrated_kv_quant"] = kv
            model["calibrated_gpu_flags"] = gpu_flags
            model["calibrated_ngl"] = best_ctx_ngl
            return True
        else:
            print(f"    ✗ KV={kv_label} — doesn't fit even at context {FALLBACK_CONTEXT:,}")

    print("    ✗ Cannot fit on available hardware")
    return False


# ---------------------------------------------------------------------------
# Ollama manifest scanning
# ---------------------------------------------------------------------------

def find_ollama_base() -> Optional[Path]:
    """Find the active Ollama models directory."""
    for path in OLLAMA_PATHS:
        manifest_dir = path / "manifests" / "registry.ollama.ai" / "library"
        if manifest_dir.exists():
            return path
    return None


def is_embedding_model(manifest_data: dict) -> bool:
    """
    Check if a model is an embedding model (not usable for text generation).

    Detection: Embedding models have no 'params' layer in their manifest.
    All LLMs and VLMs have params (temperature, top_p, etc.),
    embedding models don't need inference parameters.
    """
    has_params = any(
        layer.get("mediaType") == "application/vnd.ollama.image.params"
        for layer in manifest_data.get("layers", [])
    )
    return not has_params


def build_symlink_name(model_name: str, tag: str, config_data: dict) -> str:
    """
    Build a descriptive GGUF filename from Ollama model name, tag, and config.

    Strategy:
    - Start with model_name + tag as the basis
    - Append file_type (quantization) if not already in the tag
    - Title-case and use hyphens as separators

    Examples:
        qwen3, 8b, Q4_K_M           → Qwen3-8B-Q4_K_M.gguf
        qwen3, 14b-q8_0, Q8_0       → Qwen3-14B-Q8_0.gguf
        qwen3-coder, 30b, Q4_K_M    → Qwen3-Coder-30B-Q4_K_M.gguf
        deepseek-ocr, 3b, F16       → DeepSeek-OCR-3B-F16.gguf
    """
    file_type = config_data.get("file_type", "")

    # Combine model name and tag
    # model_name: "qwen3-coder", tag: "30b" → "qwen3-coder-30b"
    raw = f"{model_name}-{tag}"

    # Check if quant info is already in the tag (case-insensitive)
    has_quant = False
    if file_type:
        has_quant = file_type.lower().replace("_", "") in raw.lower().replace("_", "")

    # Append quant if missing
    if file_type and not has_quant:
        raw = f"{raw}-{file_type}"

    # Known abbreviations that should always be uppercase
    UPPERCASE_WORDS = {"vl", "ocr", "rl", "a3b", "moe"}

    # Title-case each segment separated by hyphens
    # But preserve: uppercase sequences (Q4_K_M, OCR, VL, A3B), numbers, version strings (2507)
    parts = raw.split("-")
    formatted_parts = []
    for part in parts:
        # Already uppercase (Q4_K_M, OCR, VL, F16) or contains underscore → uppercase
        if part.isupper() or "_" in part:
            formatted_parts.append(part.upper())
        # Known abbreviations → uppercase
        elif part.lower() in UPPERCASE_WORDS:
            formatted_parts.append(part.upper())
        # Pure number or size like "8b", "30b", "1.7b" → uppercase
        elif re.match(r'^\d+\.?\d*[bB]$', part):
            formatted_parts.append(part.upper())
        # Version string like "2507" → keep as-is
        elif part.isdigit():
            formatted_parts.append(part)
        # Quant notation like "q8_0", "q4" → uppercase
        elif re.match(r'^[qQ]\d+', part):
            formatted_parts.append(part.upper())
        # Normal word → capitalize first letter
        else:
            formatted_parts.append(part.capitalize())

    filename = "-".join(formatted_parts)

    # Clean up: remove duplicate quant patterns that may appear
    # e.g., "Q4_K_M-Q4_K_M" from tag already containing it
    # (shouldn't happen with has_quant check, but safety net)

    return f"{filename}.gguf"


def scan_ollama_manifests(ollama_base: Path) -> list[dict]:
    """
    Scan all Ollama manifests and return model info.

    Returns list of dicts with keys:
        model_name, tag, blob_path, blob_size, config_data, symlink_name
    """
    manifest_base = ollama_base / "manifests" / "registry.ollama.ai" / "library"
    blobs_base = ollama_base / "blobs"

    # Collect best entry per blob inline (longest symlink name wins)
    best_per_blob: dict[str, dict] = {}
    skipped = 0

    for model_dir in sorted(manifest_base.iterdir()):
        if not model_dir.is_dir():
            continue

        model_name = model_dir.name

        for tag_file in sorted(model_dir.iterdir()):
            if not tag_file.is_file():
                continue

            tag = tag_file.name

            try:
                manifest = json.loads(tag_file.read_text())
            except (json.JSONDecodeError, OSError):
                print(f"  ! Error reading manifest: {model_name}:{tag}")
                continue

            # Find GGUF blob layer
            blob_digest = None
            blob_size = 0
            config_digest = None

            for layer in manifest.get("layers", []):
                media_type = layer.get("mediaType", "")
                if media_type == "application/vnd.ollama.image.model":
                    blob_digest = layer["digest"]
                    blob_size = layer.get("size", 0)
                elif media_type == "application/vnd.docker.container.image.v1+json":
                    config_digest = layer.get("digest") or manifest.get("config", {}).get("digest")

            # Config digest might be in manifest.config instead of layers
            if not config_digest:
                config_digest = manifest.get("config", {}).get("digest")

            if not blob_digest:
                continue

            # Read config blob for metadata
            config_data: dict = {}
            if config_digest:
                config_blob_path = blobs_base / config_digest.replace(":", "-")
                if config_blob_path.exists():
                    try:
                        config_data = json.loads(config_blob_path.read_text())
                    except (json.JSONDecodeError, OSError):
                        pass

            # Filter embedding models
            if is_embedding_model(manifest):
                print(f"  ~ Skip:    {model_name}:{tag} (embedding model)")
                continue

            # Resolve blob path
            blob_path = blobs_base / blob_digest.replace(":", "-")
            if not blob_path.exists():
                print(f"  ! Blob missing: {model_name}:{tag} → {blob_digest[:24]}...")
                continue

            symlink_name = build_symlink_name(model_name, tag, config_data)

            entry = {
                "model_name": model_name,
                "tag": tag,
                "ollama_id": f"{model_name}:{tag}",
                "blob_digest": blob_digest,
                "blob_path": blob_path,
                "blob_size": blob_size,
                "config_data": config_data,
                "symlink_name": symlink_name,
            }

            # Multiple Ollama tags can point to the same blob - keep longest name
            existing = best_per_blob.get(blob_digest)
            if existing is None or len(symlink_name) > len(existing["symlink_name"]):
                if existing is not None:
                    skipped += 1
                best_per_blob[blob_digest] = entry
            else:
                skipped += 1

    if skipped:
        print(f"  ~ {skipped} shorter tag(s) skipped (same blob, less descriptive name)")

    return list(best_per_blob.values())


def create_symlinks(ollama_models: list[dict]) -> list[dict]:
    """
    Create symlinks in MODELS_DIR for Ollama models.

    Skips creation if another file/symlink already points to the same blob
    (e.g. a manually created symlink with a more descriptive name).

    Returns list of newly created symlink entries.
    """
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    # Build map of existing resolved targets → filenames
    existing_targets: dict[str, str] = {}
    for existing_file in MODELS_DIR.glob("*.gguf"):
        resolved = str(existing_file.resolve())
        existing_targets[resolved] = existing_file.name

    new_symlinks = []

    for model in ollama_models:
        symlink_path = MODELS_DIR / model["symlink_name"]

        if symlink_path.exists() or symlink_path.is_symlink():
            print(f"  = Exists:  {model['symlink_name']}")
            continue

        # Check if another file already points to the same blob
        blob_resolved = str(model["blob_path"].resolve())
        existing_name = existing_targets.get(blob_resolved)
        if existing_name:
            print(f"  = Covered: {model['symlink_name']} (already via {existing_name})")
            continue

        symlink_path.symlink_to(model["blob_path"])
        print(f"  + Symlink: {model['symlink_name']} → {model['blob_path'].name[:24]}...")
        existing_targets[blob_resolved] = model["symlink_name"]
        new_symlinks.append(model)

    return new_symlinks


# ---------------------------------------------------------------------------
# HuggingFace cache scanning
# ---------------------------------------------------------------------------

def _hf_latest_snapshot(repo_dir: Path) -> Optional[Path]:
    """
    Return the active snapshot directory for an HF repo.

    Prefers the commit referenced by refs/main; falls back to the
    lexicographically last entry in snapshots/ (newest by sort order).
    """
    refs_main = repo_dir / "refs" / "main"
    if refs_main.exists():
        commit = refs_main.read_text().strip()
        snapshot = repo_dir / "snapshots" / commit
        if snapshot.exists():
            return snapshot

    snapshots_dir = repo_dir / "snapshots"
    if not snapshots_dir.exists():
        return None
    entries = sorted(snapshots_dir.iterdir())
    return entries[-1] if entries else None


def scan_hf_cache() -> list[dict]:
    """
    Scan HuggingFace cache for GGUF files.

    Only considers the active snapshot (refs/main or latest commit hash).
    Returns list of dicts: name (stem), hf_path (Path inside the snapshot).
    """
    if not HF_CACHE_DIR.exists():
        return []

    results = []
    for repo_dir in sorted(HF_CACHE_DIR.glob("models--*")):
        snapshot = _hf_latest_snapshot(repo_dir)
        if not snapshot:
            continue
        # Scan top-level AND subdirectories (split GGUFs in quant-named subdirs)
        for gguf_file in sorted(snapshot.rglob("*.gguf")):
            # Skip split-GGUF parts (only count the first part or single files)
            if re.match(r'.*-\d{5}-of-\d{5}\.gguf$', gguf_file.name):
                if not gguf_file.name.endswith("-00001-of-" + gguf_file.name.split("-of-")[-1]):
                    continue
            stem = gguf_file.stem
            split_match = re.match(r'^(.+)-\d{5}-of-\d{5}$', stem)
            if split_match:
                stem = split_match.group(1)
            # Strip HF repo owner prefix from filename (e.g. "Qwen_Qwen3..." → "Qwen3...")
            # Some uploaders (bartowski) embed the org name in the GGUF filename.
            # Owner prefix is pure-alpha before the first underscore (vs quant Q4_K_XL).
            prefix, sep, rest = stem.partition("_")
            if sep and rest and prefix.isalpha():
                stem = rest
            results.append({
                "name": stem,
                "hf_path": gguf_file,
            })

    return results


def create_hf_symlinks(hf_models: list[dict]) -> list[dict]:
    """
    Create symlinks in MODELS_DIR for HuggingFace GGUFs.

    Skips creation if the same blob is already covered by an existing
    file or symlink in ~/models/ (Ollama or manual).

    Returns list of newly created symlink entries (as scan_gguf_models dicts).
    """
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    existing_targets: dict[str, str] = {}
    for existing_file in MODELS_DIR.glob("*.gguf"):
        resolved = str(existing_file.resolve())
        existing_targets[resolved] = existing_file.name

    new_symlinks = []
    for model in hf_models:
        hf_path = model["hf_path"]

        # Detect split GGUF: hf_path points to the first part
        split_match = re.match(
            r'^(.+)-(\d{5})-of-(\d{5})\.gguf$', hf_path.name
        )

        if split_match:
            # Split GGUF: create symlinks for ALL parts so llama-server
            # can find siblings relative to the symlink directory.
            base, _, total = split_match.groups()
            first_symlink = MODELS_DIR / hf_path.name

            if first_symlink.exists() or first_symlink.is_symlink():
                print(f"  = Exists:  {first_symlink.name}")
                continue

            # Resolve through HF's own symlinks for dedup
            hf_resolved = str(hf_path.resolve())
            existing_name = existing_targets.get(hf_resolved)
            if existing_name:
                print(f"  = Covered: {first_symlink.name} (already via {existing_name})")
                continue

            for i in range(1, int(total) + 1):
                part_name = f"{base}-{i:05d}-of-{total}.gguf"
                part_hf = hf_path.parent / part_name
                part_symlink = MODELS_DIR / part_name
                if part_symlink.exists() or part_symlink.is_symlink():
                    continue
                if part_hf.exists():
                    part_symlink.symlink_to(part_hf)

            print(f"  + Symlink: {first_symlink.name} (+{int(total)-1} parts) → HuggingFace cache")
            existing_targets[hf_resolved] = first_symlink.name
            new_symlinks.append(model)
        else:
            # Single-file GGUF: create one symlink with clean name
            symlink_path = MODELS_DIR / (model["name"] + ".gguf")

            if symlink_path.exists() or symlink_path.is_symlink():
                print(f"  = Exists:  {symlink_path.name}")
                continue

            hf_resolved = str(hf_path.resolve())
            existing_name = existing_targets.get(hf_resolved)
            if existing_name:
                print(f"  = Covered: {symlink_path.name} (already via {existing_name})")
                continue

            symlink_path.symlink_to(hf_path)
            print(f"  + Symlink: {symlink_path.name} → HuggingFace cache")
            existing_targets[hf_resolved] = symlink_path.name
            new_symlinks.append(model)

    return new_symlinks


# ---------------------------------------------------------------------------
# GGUF scanning and delta detection
# ---------------------------------------------------------------------------

def _strip_quant_suffix(stem: str) -> str:
    """
    Strip quantization suffix from a GGUF stem.

    Examples:
        Qwen3VL-8B-Instruct-Q4_K_M  → Qwen3VL-8B-Instruct
        mmproj-Qwen3VL-8B-Instruct-F16 → mmproj-Qwen3VL-8B-Instruct
    """
    return re.sub(r'-(?:BF|[QqFf])\d[0-9_A-Za-z]*$', '', stem, flags=re.IGNORECASE)


def _find_mmproj(model_stem: str, mmproj_files: dict[str, Path]) -> Optional[Path]:
    """
    Match a model stem to a mmproj file by common base name (quantization-stripped).

    Example: model "Qwen3VL-8B-Instruct-Q4_K_M" matches mmproj "Qwen3VL-8B-Instruct-F16"
    because both share the base "qwen3vl-8b-instruct".
    """
    model_base = _strip_quant_suffix(model_stem).lower()
    for mmproj_stem, mmp_path in mmproj_files.items():
        mmproj_base = _strip_quant_suffix(mmproj_stem).lower()
        if model_base == mmproj_base:
            return mmp_path
    return None


def scan_gguf_models() -> list[dict]:
    """
    Scan ~/models/ for all GGUF files.

    Detects mmproj-*.gguf files and pairs them with their corresponding VL model.

    Returns list of dicts with keys: name (stem), path, mmproj_path (Optional[Path])
    """
    if not MODELS_DIR.exists():
        return []

    # Collect mmproj files first: stem-without-prefix → path
    mmproj_files: dict[str, Path] = {}
    for mmp in sorted(MODELS_DIR.glob("mmproj-*.gguf")):
        mmproj_files[mmp.stem[len("mmproj-"):]] = mmp

    models = []
    # Scan top-level AND subdirectories (hf download --local-dir creates nested dirs)
    for gguf_file in sorted(MODELS_DIR.rglob("*.gguf")):
        # mmproj files are not standalone models
        if gguf_file.name.startswith("mmproj-"):
            continue

        # Skip split-GGUF parts (only count the first part or single files)
        if re.match(r'.*-\d{5}-of-\d{5}\.gguf$', gguf_file.name):
            if not gguf_file.name.endswith("-00001-of-" + gguf_file.name.split("-of-")[-1]):
                continue

        model_stem = gguf_file.stem
        # Strip split-GGUF part suffix: "Model-00001-of-00003" → "Model"
        split_match = re.match(r'^(.+)-\d{5}-of-\d{5}$', model_stem)
        if split_match:
            model_stem = split_match.group(1)
            # Strip HuggingFace "-split" suffix: "Model-split" → "Model"
            model_stem = re.sub(r'-split$', '', model_stem, flags=re.IGNORECASE)
        models.append({
            "name": model_stem,
            "path": gguf_file,
            "mmproj_path": _find_mmproj(model_stem, mmproj_files),
        })

    return models


def parse_existing_yaml_models(config_path: Path) -> set[str]:
    """
    Extract model names from existing llama-swap-config.yaml.

    Simple regex-based parsing to avoid YAML library dependency.
    Returns set of model names (keys under 'models:').
    """
    if not config_path.exists():
        return set()

    content = config_path.read_text()
    # Match lines like "  ModelName:" that are model entries under "models:"
    # They are indented by 2 spaces and followed by a colon
    model_names = set()
    in_models_section = False

    for line in content.splitlines():
        if line.strip() == "models:":
            in_models_section = True
            continue

        if in_models_section:
            # Model entry: exactly 2 spaces indent, then name, then colon (optional trailing whitespace)
            match = re.match(r'^  ([A-Za-z0-9][A-Za-z0-9._-]*):\s*$', line)
            if match:
                model_names.add(match.group(1))
            # Sub-keys have 4+ spaces, skip those
            # A line with 0 indent means we left the models section
            elif line and not line.startswith(" "):
                break

    return model_names


def find_new_models(
    all_ggufs: list[dict],
    existing_models: set[str],
    existing_paths: Optional[set[str]] = None,
) -> list[dict]:
    """Find GGUFs that are not yet in the llama-swap config.

    Checks both model names (case-insensitive) and GGUF file paths to avoid
    duplicates when a model was manually renamed in the config.
    """
    existing_lower = {name.lower() for name in existing_models}
    paths = existing_paths or set()
    new = []
    for gguf in all_ggufs:
        if gguf["name"].lower() in existing_lower:
            continue
        # Check if this GGUF path is already used by an existing (renamed) entry
        if paths and str(gguf["path"].resolve()) in paths:
            continue
        new.append(gguf)
    return new


# ---------------------------------------------------------------------------
# GGUF metadata reading
# ---------------------------------------------------------------------------

def _read_gguf_field(gguf_path: Path, suffix: str) -> Optional[int]:
    """Read a single integer field from GGUF metadata by key suffix."""
    try:
        real_path = gguf_path.resolve()
        import gguf
        reader = gguf.GGUFReader(str(real_path))
        for field in reader.fields.values():
            if field.name.lower().endswith(suffix):
                value_array = field.parts[-1]
                if len(value_array) > 0:
                    val = int(value_array[0])
                    if val > 0:
                        return val
    except ImportError:
        pass
    except Exception:
        pass
    return None


def get_native_context(gguf_path: Path) -> int:
    """Read native context length from GGUF metadata."""
    val = _read_gguf_field(gguf_path, ".context_length")
    if val is None:
        val = _read_gguf_field(gguf_path, "context_length")
    if val is None:
        print("  ! Cannot read context from GGUF, using default")
        return DEFAULT_CONTEXT
    return val


def get_gguf_sampling_params(gguf_path: Path) -> dict[str, float]:
    """Read recommended sampling parameters from GGUF metadata.

    Newer GGUFs (Qwen3-Next, MiniMax, etc.) embed official sampling defaults
    as general.sampling.temp/top_k/top_p/min_p fields.

    Returns dict with keys: temp, top_k, top_p, min_p, repeat_penalty.
    Falls back to llama.cpp defaults for missing fields.
    """
    # llama.cpp defaults (used when GGUF has no sampling metadata)
    defaults = {
        "temp": 0.8,
        "top_k": 40,
        "top_p": 0.95,
        "min_p": 0.05,
        "repeat_penalty": 1.0,
    }

    try:
        from gguf import GGUFReader
        reader = GGUFReader(str(gguf_path.resolve()))
        for field in reader.fields.values():
            if field.name.startswith("general.sampling."):
                key = field.name.split(".")[-1]
                if key in defaults and field.parts:
                    val = field.parts[-1].tolist()
                    if val:
                        defaults[key] = round(float(val[0]), 4)
    except Exception:
        pass

    return defaults


# ---------------------------------------------------------------------------
# YAML generation
# ---------------------------------------------------------------------------

def detect_llama_server_bin(config_path: Path) -> Path:
    """
    Detect llama-server binary path from existing config entries.

    Falls back to LLAMA_SERVER_BIN constant.
    """
    if not config_path.exists():
        return LLAMA_SERVER_BIN

    content = config_path.read_text()
    # Extract first llama-server path from any cmd line
    match = re.search(r'(/\S+/llama-server)\s', content)
    if match:
        return Path(match.group(1))

    return LLAMA_SERVER_BIN


def append_models_to_yaml(
    config_path: Path,
    new_models: list[dict],
    server_bin: Path,
) -> int:
    """
    Append new model entries to llama-swap-config.yaml.

    VL models (with mmproj_path set) get a --mmproj argument in the cmd.

    Returns number of models added.
    """
    if not new_models:
        return 0

    # Read existing content
    if config_path.exists():
        content = config_path.read_text()
        # Ensure trailing newline
        if not content.endswith("\n"):
            content += "\n"
    else:
        content = "models:\n"

    # Build all new model blocks first
    # Models must have calibrated_context, calibrated_gpu_flags, calibrated_kv_quant,
    # calibrated_ngl set by calibration before calling this function.
    added = 0
    new_blocks = ""
    for model in new_models:
        name = model["name"]
        path = model["path"].absolute()
        context = model["calibrated_context"]
        mmproj = model.get("mmproj_path")
        kv_quant = model["calibrated_kv_quant"]
        gpu_flags = model["calibrated_gpu_flags"]
        ngl = model.get("calibrated_ngl", DEFAULT_NGL)

        if kv_quant:
            flags = f"-ctk {kv_quant} -ctv {kv_quant} {DEFAULT_FLAGS_BASE}"
        else:
            flags = DEFAULT_FLAGS_BASE

        if gpu_flags:
            flags = f"{gpu_flags} {flags}"

        # Read sampling parameters from GGUF metadata (or use llama.cpp defaults)
        sampling = get_gguf_sampling_params(path)
        sampling_flags = (
            f"--temp {sampling['temp']} --top-k {int(sampling['top_k'])} "
            f"--top-p {sampling['top_p']} --min-p {sampling['min_p']} "
            f"--repeat-penalty {sampling['repeat_penalty']}"
        )

        kv_label = f", KV: {kv_quant}" if kv_quant else ""
        ngl_label = f", ngl: {ngl}" if ngl != DEFAULT_NGL else ""
        sampling_label = f", temp={sampling['temp']}" if sampling['temp'] != 0.8 else ""
        if mmproj:
            cmd_line = (
                f"{server_bin} --port ${{PORT}} "
                f"--model {path} "
                f"--mmproj {mmproj.absolute()} "
                f"-ngl {ngl} -c {context} {flags} {sampling_flags}"
            )
            print(f"  + Added: {name} (VL, context: {context:,}{kv_label}{ngl_label}{sampling_label}, mmproj: {mmproj.name})")
        else:
            cmd_line = (
                f"{server_bin} --port ${{PORT}} "
                f"--model {path} "
                f"-ngl {ngl} -c {context} {flags} {sampling_flags}"
            )
            print(f"  + Added: {name} (context: {context:,}{kv_label}{ngl_label}{sampling_label})")

        model_size_gb = get_gguf_total_size(Path(path)) / (1024 ** 3)
        if model_size_gb >= 50:
            ttl = DEFAULT_TTL_LARGE
        elif model_size_gb >= 20:
            ttl = DEFAULT_TTL_MEDIUM
        else:
            ttl = DEFAULT_TTL_SMALL

        new_blocks += "  # [autoscan]\n"
        new_blocks += f"  {name}:\n"
        new_blocks += f"    cmd: {cmd_line}\n"
        new_blocks += f"    ttl: {ttl}\n"

        added += 1

    # Insert before groups: section (if present) so update_groups_in_yaml
    # doesn't accidentally delete newly added models via its EOF regex.
    groups_match = re.search(r'^groups:', content, re.MULTILINE)
    if groups_match:
        insert_pos = groups_match.start()
        content = content[:insert_pos] + new_blocks + content[insert_pos:]
    else:
        content += new_blocks

    _write_config(config_path, content)
    return added


# ---------------------------------------------------------------------------
# VRAM cache
# ---------------------------------------------------------------------------

def update_vram_cache(new_models: list[dict]) -> int:
    """
    Add preliminary VRAM cache entries for new models.

    Returns number of entries added.
    """
    if not new_models:
        return 0

    # Load existing cache
    cache: dict = {}
    if VRAM_CACHE_FILE.exists():
        try:
            cache = json.loads(VRAM_CACHE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            cache = {}

    added = 0
    for model in new_models:
        name = model["name"]

        if name in cache:
            continue

        native_context = get_native_context(model["path"])
        ngl = model.get("calibrated_ngl", DEFAULT_NGL)
        cal_context = model.get("calibrated_context", native_context)
        mode = "hybrid" if ngl != DEFAULT_NGL else "gpu"

        # Extract quantization from GGUF stem (e.g. "Qwen3-8B-Q4_K_M" → "Q4_K_M")
        quant_match = re.search(r'[_-]([QqBbFf]\d[0-9_A-Za-z]*)$', name, re.IGNORECASE)
        quantization = quant_match.group(1).upper() if quant_match else ""

        gguf_resolved = model["path"].resolve()

        calibration_entry = {
            "max_context": cal_context,
            "ngl": ngl,
            "mode": mode,
            "measured_at": datetime.now().isoformat(),
        }

        cache[name] = {
            "backend": "llamacpp",
            "native_context": native_context,
            "quantization": quantization,
            "model_size_gb": round(get_gguf_total_size(gguf_resolved) / (1024 ** 3), 3),
            "gpu_model": "",
            "gguf_path": str(gguf_resolved),
            "llamacpp_calibrations": [calibration_entry],
        }

        print(f"  + Added: {name}")
        added += 1

    # Save cache
    if added > 0:
        VRAM_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        VRAM_CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n")

    return added


# ---------------------------------------------------------------------------
# Groups section
# ---------------------------------------------------------------------------

def update_groups_in_yaml(config_path: Path) -> None:
    """
    Write or replace the groups.main.members section in llama-swap-config.yaml.

    Lists all configured models except -speed variants as members of 'main'.
    The swap:true flag tells llama-swap that only one model from this group
    can be loaded at a time, enforcing VRAM exclusivity.
    """
    if not config_path.exists():
        return

    all_models = parse_existing_yaml_models(config_path)
    members = sorted(
        name for name in all_models
        if not name.endswith("-speed")
    )

    if not members:
        return

    content = config_path.read_text()

    # Remove ALL existing groups sections (can appear anywhere in the file)
    # Match "groups:" at line start through to the next top-level key or EOF
    content = re.sub(
        r'^groups:.*?(?=^\S|\Z)', '', content,
        flags=re.MULTILINE | re.DOTALL,
    )
    content = content.rstrip("\n") + "\n"

    members_yaml = "\n".join(f"      - {m}" for m in members)
    content += (
        "\ngroups:\n"
        "  main:\n"
        "    exclusive: true\n"
        "    swap: true\n"
        "    members:\n"
        f"{members_yaml}\n"
    )

    _write_config(config_path, content)


# ---------------------------------------------------------------------------
# YAML indentation normalization
# ---------------------------------------------------------------------------

# Sub-keys that belong under a model entry (2-space indent) and MUST be at 4-space indent
_MODEL_SUB_KEYS = re.compile(r'^(\s*)(cmd|ttl|healthCheckTimeout|env|proxy|aliases)\s*:')


def normalize_yaml_indentation(config_path: Path) -> int:
    """Fix indentation of model sub-keys (cmd, ttl, etc.) to exactly 4 spaces.

    Returns count of fixed lines.
    """
    if not config_path.exists():
        return 0

    lines = config_path.read_text().splitlines(keepends=True)
    fixed = 0
    in_models = False

    for i, line in enumerate(lines):
        stripped = line.rstrip('\n')

        # Track models: section
        if stripped == "models:":
            in_models = True
            continue
        if in_models and stripped and not stripped.startswith(" ") and not stripped.startswith("#"):
            in_models = False

        if not in_models:
            continue

        m = _MODEL_SUB_KEYS.match(stripped)
        if not m:
            continue

        current_indent = m.group(1)
        if current_indent != "    ":
            # Fix: replace whatever indent with exactly 4 spaces
            lines[i] = "    " + stripped.lstrip() + "\n"
            fixed += 1

    if fixed:
        _write_config(config_path, "".join(lines))

    return fixed


# ---------------------------------------------------------------------------
# Incompatibility skip list
# ---------------------------------------------------------------------------

def load_skip_list() -> dict[str, str]:
    """Load models that previously failed the compatibility test (name → reason)."""
    if not AUTOSCAN_SKIP_FILE.exists():
        return {}
    try:
        data: dict[str, str] = json.loads(AUTOSCAN_SKIP_FILE.read_text())
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def save_skip_list(skip: dict[str, str]) -> None:
    """Persist the skip list to disk."""
    AUTOSCAN_SKIP_FILE.write_text(json.dumps(skip, indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Cleanup of removed models
# ---------------------------------------------------------------------------

def cleanup_dead_symlinks() -> list[str]:
    """
    Remove broken symlinks from ~/models/.

    Returns list of removed symlink names (stems).
    """
    if not MODELS_DIR.exists():
        return []

    removed = []
    symlink_count = 0
    for gguf_file in sorted(MODELS_DIR.glob("*.gguf")):
        if gguf_file.is_symlink():
            symlink_count += 1
            if not gguf_file.exists():
                target = gguf_file.readlink()
                gguf_file.unlink()
                print(f"  ✗ {gguf_file.name} → {target} (target missing, removed)")
                removed.append(gguf_file.stem)

    if symlink_count and not removed:
        print(f"  {symlink_count} symlink(s) checked — all targets valid")

    return removed


def _extract_model_path(cmd: str) -> Optional[Path]:
    """Extract the --model file path from a llama-server command line."""
    match = re.search(r'--model\s+(\S+)', cmd)
    return Path(match.group(1)) if match else None


def _parse_model_cmds(config_path: Path) -> dict[str, str]:
    """
    Parse model entries with their cmd lines from config YAML.

    Returns dict: model_name → cmd string.
    """
    if not config_path.exists():
        return {}

    content = config_path.read_text()
    entries: dict[str, str] = {}
    in_models = False
    current_name: Optional[str] = None
    cmd_lines: list[str] = []
    collecting_cmd = False

    for line in content.splitlines():
        if line.strip() == "models:":
            in_models = True
            continue

        if not in_models:
            continue

        match = re.match(r'^  ([A-Za-z0-9][A-Za-z0-9._-]*):$', line)
        if match:
            if current_name and cmd_lines:
                entries[current_name] = " ".join(cmd_lines)
            current_name = match.group(1)
            cmd_lines = []
            collecting_cmd = False
            continue

        cmd_match = re.match(r'^\s+cmd:\s+(.+)$', line)
        if cmd_match and current_name:
            raw = cmd_match.group(1).strip()
            if raw in ("|", ">", "|-", ">-"):
                # YAML block scalar (cmd: | or cmd: >) — collect indented lines
                cmd_lines = []
                collecting_cmd = True
            elif raw.startswith("'") or raw.startswith('"'):
                quote = raw[0]
                if raw.endswith(quote) and len(raw) > 1:
                    cmd_lines = [raw[1:-1]]
                else:
                    cmd_lines = [raw[1:]]
                    collecting_cmd = True
            else:
                # Plain scalar — may span multiple lines as YAML folded scalar.
                # Enable continuation collection so lines with 6+ space indent
                # are appended to the cmd.
                cmd_lines = [raw]
                collecting_cmd = True
            continue

        if collecting_cmd and current_name:
            stripped = line.strip()
            if stripped.endswith("'") or stripped.endswith('"'):
                cmd_lines.append(stripped[:-1])
                collecting_cmd = False
            elif line.startswith("      "):
                # Block scalar continuation (6+ spaces indent)
                cmd_lines.append(stripped)
            else:
                # Less indent = block scalar ended, re-process this line
                collecting_cmd = False
                # Don't continue — let the line be processed by other matchers
                # Check if it's a new model entry or other key
                match2 = re.match(r'^  ([A-Za-z0-9][A-Za-z0-9._-]*):$', line)
                if match2:
                    if current_name and cmd_lines:
                        entries[current_name] = " ".join(cmd_lines)
                    current_name = match2.group(1)
                    cmd_lines = []
                elif line and not line.startswith(" "):
                    if current_name and cmd_lines:
                        entries[current_name] = " ".join(cmd_lines)
                    break
            continue

        if line and not line.startswith(" "):
            if current_name and cmd_lines:
                entries[current_name] = " ".join(cmd_lines)
            break
    else:
        if current_name and cmd_lines:
            entries[current_name] = " ".join(cmd_lines)

    return entries


def _remove_model_block(content: str, name: str) -> str:
    """Remove a single model entry block from YAML content, including leading comments."""
    # Match optional comment lines (# ...) directly before the model entry
    pattern = rf'(?:  #[^\n]*\n)*  {re.escape(name)}:\n(?:    .+\n)*'
    return re.sub(pattern, '', content, count=1, flags=re.MULTILINE)


def cleanup_stale_config(config_path: Path) -> list[str]:
    """
    Remove config entries for models whose GGUF files no longer exist.

    Returns list of removed model names.
    """
    model_cmds = _parse_model_cmds(config_path)
    if not model_cmds:
        return []

    stale = []
    for name, cmd in model_cmds.items():
        model_path = _extract_model_path(cmd)
        if model_path and not model_path.exists():
            stale.append((name, model_path))

    if not stale:
        print(f"  {len(model_cmds)} config entry/entries checked — all GGUF files present")
        return []

    content = config_path.read_text()
    for name, missing_path in stale:
        content = _remove_model_block(content, name)
        print(f"  ✗ {name} — GGUF missing: {missing_path}")

    # Remove orphaned autoscan markers (consecutive markers with no model between them)
    content = re.sub(r'(  # \[autoscan\]\n)+  # \[autoscan\]\n', '', content)
    content = re.sub(r'\n{3,}', '\n\n', content)
    _write_config(config_path, content)

    return [name for name, _ in stale]


def cleanup_skip_list() -> int:
    """Remove skip-list entries for models whose files no longer exist."""
    skip = load_skip_list()
    if not skip:
        return 0

    to_remove = [
        name for name in skip
        if not (MODELS_DIR / f"{name}.gguf").exists()
        and not (MODELS_DIR / f"{name}.gguf").is_symlink()
    ]

    if not to_remove:
        print(f"  {len(skip)} skip-list entry/entries checked — all still relevant")
        return 0

    for name in to_remove:
        del skip[name]
        print(f"  ✗ Skip list: {name} (GGUF removed, entry cleaned)")

    save_skip_list(skip)

    return len(to_remove)


def cleanup_vram_cache(active_models: set[str]) -> int:
    """
    Remove VRAM cache entries for models no longer in the config.

    Takes the set of currently configured model names and removes
    any cache entries that don't match.

    Returns number of entries removed.
    """
    if not VRAM_CACHE_FILE.exists():
        return 0

    try:
        cache = json.loads(VRAM_CACHE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return 0

    active_lower = {name.lower() for name in active_models}
    # All models in the VRAM cache are managed via llama-swap (including
    # Ollama-sourced GGUFs that autoscan symlinks into ~/models/).
    # Remove any entry not matching an active config model.
    to_remove = [
        name for name in cache
        if name.lower() not in active_lower
    ]

    if not to_remove:
        print(f"  {len(cache)} VRAM cache entry/entries checked — all match active config")
        return 0

    for name in to_remove:
        del cache[name]
        print(f"  ✗ VRAM cache: {name} (no longer in config, removed)")

    VRAM_CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n")

    return len(to_remove)


# ---------------------------------------------------------------------------
# Compatibility test
# ---------------------------------------------------------------------------

# How long to wait for llama-server to crash with a startup error.
# Compatible models keep running (serving HTTP), so they always hit this timeout.
# Incompatible models fail within 1-2 s → we catch the error before the timeout.
COMPAT_TEST_TIMEOUT = 6


def _free_port() -> int:
    """Return an available TCP port on localhost."""
    with socket.socket() as s:
        s.bind(('', 0))
        port: int = s.getsockname()[1]
        return port


def test_model_compatibility(
    gguf_path: Path,
    server_bin: Path,
    mmproj_path: Optional[Path] = None,
) -> tuple[bool, str]:
    """
    Quick llama-server startup test for a new model.

    Starts llama-server with minimal flags and waits COMPAT_TEST_TIMEOUT seconds.
    - If the process exits within the timeout → reads stdout for known error patterns.
    - If the process is still running after the timeout → model is loading fine (compatible).

    For VL models, pass mmproj_path so llama-server gets the required --mmproj argument.

    Returns:
        (True, "")              — compatible (or test inconclusive)
        (False, "reason str")   — known incompatibility detected
    """
    if not server_bin.exists():
        return True, ""

    port = _free_port()
    cmd = [
        str(server_bin),
        "--port", str(port),
        "--model", str(gguf_path.resolve()),
        # Minimal settings — architecture errors occur before any weight loading
        "-ngl", "99",
        "-c", "512",
        "-np", "1",
    ]
    if mmproj_path:
        cmd += ["--mmproj", str(mmproj_path.resolve())]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except Exception:
        return True, ""  # Can't run the binary — not the model's fault

    try:
        stdout_data, _ = proc.communicate(timeout=COMPAT_TEST_TIMEOUT)
        output = stdout_data.decode('utf-8', errors='replace')
    except subprocess.TimeoutExpired:
        # Still running after timeout → server is up, model is compatible
        proc.kill()
        proc.communicate()
        return True, ""

    # Process exited before timeout — check for known permanent failures
    if "unknown model architecture" in output:
        m = re.search(r"unknown model architecture: '([^']+)'", output)
        arch = m.group(1) if m else "unknown"
        return False, f"unsupported architecture '{arch}'"

    if "key not found in model" in output:
        m = re.search(r"key not found in model: (\S+)", output)
        key = m.group(1) if m else "unknown key"
        # Detect Ollama blobs by resolving the symlink
        hint = ""
        if gguf_path.is_symlink() and ".ollama" in str(gguf_path.resolve()):
            hint = " — Ollama blob missing llama.cpp metadata; download official GGUF from HuggingFace"
        return False, f"missing GGUF metadata key '{key}'{hint}"

    # Other early exits (e.g. port conflict) — don't block the model
    return True, ""


# ---------------------------------------------------------------------------
# Recalibrate
# ---------------------------------------------------------------------------

def _recalibrate_reset() -> None:
    """Remove autoscan-generated YAML entries and VRAM cache so main() re-calibrates.

    Creates a timestamped backup of the YAML before any changes.
    Autoscan entries are marked with '# [autoscan]' comment above the model block.
    """
    print("=== Recalibrate Mode ===")
    if not LLAMASWAP_CONFIG.exists():
        print("  No config found, nothing to reset.")
        return

    # Backup YAML
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = LLAMASWAP_CONFIG.with_suffix(f".yaml.bak-{ts}")
    backup.write_text(LLAMASWAP_CONFIG.read_text())
    print(f"  Backup: {backup.name}")

    # Backup VRAM cache
    if VRAM_CACHE_FILE.exists():
        cache_backup = VRAM_CACHE_FILE.with_suffix(f".json.bak-{ts}")
        cache_backup.write_text(VRAM_CACHE_FILE.read_text())
        print(f"  Backup: {cache_backup.name}")

    # Remove lines between '# [autoscan]' markers and their model blocks
    content = LLAMASWAP_CONFIG.read_text()
    lines = content.splitlines(keepends=True)
    removed: list[str] = []
    result_lines: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        if line.strip() == "# [autoscan]":
            # Next non-empty line should be the model entry
            i += 1
            if i < len(lines):
                name_match = re.match(r'^  ([A-Za-z0-9][A-Za-z0-9._-]*):[ \t]*$', lines[i])
                if name_match:
                    removed.append(name_match.group(1))
                    i += 1
                    # Skip indented children (cmd, ttl, etc.)
                    while i < len(lines) and lines[i].startswith("    "):
                        i += 1
                    continue
            # Marker without valid model entry — skip just the marker
            continue

        result_lines.append(line)
        i += 1

    _write_config(LLAMASWAP_CONFIG, "".join(result_lines))

    # Remove only autoscan entries from VRAM cache (keep AIfred calibration data)
    if removed and VRAM_CACHE_FILE.exists():
        try:
            cache = json.loads(VRAM_CACHE_FILE.read_text())
            removed_lower = {n.lower() for n in removed}
            pruned = {k: v for k, v in cache.items() if k.lower() not in removed_lower}
            VRAM_CACHE_FILE.write_text(json.dumps(pruned, indent=2))
            print(f"  VRAM cache: {len(cache) - len(pruned)} entries removed, {len(pruned)} kept")
        except (json.JSONDecodeError, OSError):
            pass

    if removed:
        print(f"  Removed {len(removed)} autoscan entries: {', '.join(removed)}")
    else:
        print("  No autoscan entries found to remove")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=== llama-swap Autoscan ===")
    print()

    config_changed = False

    # Step 0: GPU hardware fingerprint — detect hardware changes
    per_gpu_vram = get_per_gpu_vram_mb()
    current_fp = build_gpu_fingerprint()

    if LLAMASWAP_CONFIG.exists():
        stored_fp = read_gpu_fingerprint(LLAMASWAP_CONFIG)
        if stored_fp is None:
            # First run with fingerprint support — store current hardware
            write_gpu_fingerprint(LLAMASWAP_CONFIG, current_fp)
            print(f"GPU fingerprint stored: {current_fp}")
        elif gpu_hardware_changed(stored_fp, current_fp):
            print("⚠️  GPU HARDWARE CHANGED!")
            print(f"   Stored:  {stored_fp}")
            print(f"   Current: {current_fp}")
            updated = update_all_tensor_splits(LLAMASWAP_CONFIG, per_gpu_vram)
            write_gpu_fingerprint(LLAMASWAP_CONFIG, current_fp)
            if updated:
                config_changed = True
                print(f"   Updated tensor-split in {updated} model(s)")
            print("   → Run 'Context kalibrieren' in AIfred to optimize context sizes")
        else:
            print(f"GPU hardware: {current_fp}")
    else:
        print(f"GPU detected: {current_fp}")

    print()

    # Step 1a: Scan Ollama and create symlinks
    ollama_base = find_ollama_base()
    if ollama_base:
        print("Scanning Ollama models...")
        ollama_models = scan_ollama_manifests(ollama_base)
        new_symlinks = create_symlinks(ollama_models)
        print(f"  {len(ollama_models)} Ollama models found, {len(new_symlinks)} new symlinks created")
    else:
        print("No Ollama installation found, skipping.")

    print()

    # Step 1b: Scan HuggingFace cache and create symlinks
    print("Scanning HuggingFace cache...")
    hf_models = scan_hf_cache()
    if hf_models:
        new_hf_symlinks = create_hf_symlinks(hf_models)
        print(f"  {len(hf_models)} HF GGUFs found, {len(new_hf_symlinks)} new symlinks created")
    else:
        print("  No HuggingFace cache found or empty.")

    print()

    # Step 1c: Clean up dead symlinks and stale config entries
    print("Cleaning up...")
    removed_symlinks = cleanup_dead_symlinks()
    stale_models = cleanup_stale_config(LLAMASWAP_CONFIG)
    stale_skip = cleanup_skip_list()
    if removed_symlinks or stale_models or stale_skip:
        total = len(removed_symlinks) + len(stale_models) + stale_skip
        print(f"  → {total} item(s) cleaned up")
        if stale_models:
            config_changed = True

    print()

    # Step 2: Scan for all GGUFs and find new ones
    print("Scanning ~/models/ for GGUFs...")
    all_ggufs = scan_gguf_models()
    existing = parse_existing_yaml_models(LLAMASWAP_CONFIG)
    skip_list = load_skip_list()

    # Models in the skip list are known-incompatible — treat as already handled
    if skip_list:
        skipped = [m["name"] for m in all_ggufs if m["name"] in skip_list and m["name"] not in existing]
        if skipped:
            print(f"  {len(skipped)} model(s) skipped (known incompatible, remove from {AUTOSCAN_SKIP_FILE.name} to re-test):")
            for name in skipped:
                print(f"    ~ {name}: {skip_list[name]}")

    # Extract GGUF paths from existing config entries to detect renamed models
    existing_paths: set[str] = set()
    model_cmds = _parse_model_cmds(LLAMASWAP_CONFIG)
    for cmd in model_cmds.values():
        model_path = _extract_model_path(cmd)
        if model_path:
            try:
                existing_paths.add(str(model_path.resolve()))
            except OSError:
                pass

    new_models = find_new_models(all_ggufs, existing | set(skip_list), existing_paths)
    vl_models = [m for m in new_models if m.get("mmproj_path")]
    if vl_models:
        print(f"  {len(vl_models)} VL model(s) detected with mmproj:")
        for m in vl_models:
            print(f"    ◆ {m['name']} + {m['mmproj_path'].name}")  # type: ignore[union-attr]
    print(f"  Found {len(all_ggufs)} GGUFs, {len(new_models)} new")
    print()

    # Step 3: Compatibility test — only for genuinely new models, result cached in skip list
    yaml_added = 0
    cache_added = 0

    if new_models:
        server_bin = detect_llama_server_bin(LLAMASWAP_CONFIG)
        print("Testing new models for llama-server compatibility...")
        compatible_models = []
        for model in new_models:
            compat, reason = test_model_compatibility(model["path"], server_bin, model.get("mmproj_path"))
            if compat:
                compatible_models.append(model)
                mmproj = model.get("mmproj_path")
                label = f"VL + {mmproj.name}" if mmproj else "OK"
                print(f"  ✓ {model['name']} ({label})")
            else:
                skip_list[model["name"]] = reason
                save_skip_list(skip_list)
                print(f"  ✗ {model['name']}: {reason} — skipping")
        new_models = compatible_models
        print()

        # Step 4: Calibrate using llama-fit-params (per-GPU VRAM projection)
        if new_models:
            print("Calibrating new models (llama-fit-params)...")
            calibrated_models = []
            for model in new_models:
                if calibrate_model_fit_params(model, server_bin, per_gpu_vram):
                    calibrated_models.append(model)
            new_models = calibrated_models
            print()

        # Step 5: Add to llama-swap config
        if new_models:
            print("Updating llama-swap-config.yaml...")
            yaml_added = append_models_to_yaml(LLAMASWAP_CONFIG, new_models, server_bin)
            config_changed = True

            # Step 6: Update VRAM cache
            print("Updating VRAM cache...")
            cache_added = update_vram_cache(new_models)
            print()

    # Update groups if config was modified (cleanup or new models)
    if config_changed:
        update_groups_in_yaml(LLAMASWAP_CONFIG)
        all_members = sorted(
            n for n in parse_existing_yaml_models(LLAMASWAP_CONFIG)
            if not n.endswith("-speed")
        )
        print(f"Groups updated: main → [{', '.join(all_members)}]")

        # Clean up VRAM cache for models no longer in config
        stale_vram = cleanup_vram_cache(parse_existing_yaml_models(LLAMASWAP_CONFIG))
        if stale_vram:
            print(f"  {stale_vram} stale VRAM cache entry/entries removed")
        print()

    # Always normalize indentation (fixes manually edited entries)
    indent_fixes = normalize_yaml_indentation(LLAMASWAP_CONFIG)
    if indent_fixes:
        print(f"Fixed {indent_fixes} YAML indentation issue(s)")

    # Summary
    parts = []
    if stale_models:
        parts.append(f"{len(stale_models)} removed")
    if yaml_added:
        parts.append(f"{yaml_added} added")
    if cache_added:
        parts.append(f"{cache_added} VRAM cache entries added")
    if parts:
        print(f"Done. {', '.join(parts)}.")
    else:
        print("No changes. Done.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="llama-swap autoscan")
    parser.add_argument(
        "--recalibrate", action="store_true",
        help="Remove all autoscan-generated entries and VRAM cache, then re-scan and re-calibrate everything",
    )
    args = parser.parse_args()
    if args.recalibrate:
        _recalibrate_reset()
    main()
