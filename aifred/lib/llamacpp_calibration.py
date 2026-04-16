"""
llama.cpp Context & NGL Calibration via llama-swap

Projection-based calibration using llama-fit-params for exact VRAM estimation:
1. GPU-only: VRAM projection (0.3s each) → calculate max context → verify with server
2. Speed: Tensor-split optimization for multi-GPU setups
3. Hybrid: If GPU-only yields too little context, reduce -ngl to offload
   layers to CPU, freeing VRAM for KV cache (more context, slower inference)

Results are cached in model_vram_cache.json and written back to the
llama-swap YAML config (both -c and -ngl).
"""

import asyncio
import copy
import logging
import re
import shlex
import signal
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

import httpx
import yaml

from .config import (
    CALIBRATION_MIN_CONTEXT,
    LLAMACPP_CALIBRATION_PORT,
    LLAMACPP_CALIBRATION_PRECISION,
    LLAMACPP_HEALTH_TIMEOUT,
    LLAMACPP_VRAM_SAFETY_MARGIN,
    LLAMACPP_VISION_VRAM_RESERVE,
    MIN_FREE_RAM_MB,
    MIN_USEFUL_CONTEXT_TOKENS,
)
from .formatting import format_number
from .logging_utils import log_message
from .gguf_utils import (
    extract_quantization_from_filename,
    get_gguf_layer_count,
    get_gguf_native_context,
)
from .gpu_utils import get_all_gpus_memory_info
from .model_vram_cache import add_llamacpp_calibration

logger = logging.getLogger(__name__)

_GPU_NAME_PREFIXES = ("NVIDIA GeForce ", "NVIDIA ", "Quadro ", "Tesla ")

# Safety margin per GPU (MiB) when checking if model weights fit.
# Must leave room for KV cache, CUDA context, and runtime overhead.
_MIN_GPU_SAFETY_MARGIN_MB = 1024


def _short_gpu_name(name: str) -> str:
    """Shorten GPU name by stripping common prefixes."""
    for prefix in _GPU_NAME_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def _to_cuda_order(gpu_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort GPU list by total VRAM descending to match CUDA_DEVICE_ORDER=FASTEST_FIRST.

    Calibration processes set FASTEST_FIRST explicitly, so CUDA0 = largest GPU.
    Adds cuda_id labels (CUDA0, CUDA1, ...) to each GPU dict.
    """
    sorted_gpus = sorted(gpu_list, key=lambda g: g["total_mb"], reverse=True)
    for i, g in enumerate(sorted_gpus):
        g["cuda_id"] = f"CUDA{i}"
    return sorted_gpus


def _find_min_gpus(model_size_mb: float, per_gpu_total_mb: list[int]) -> int:
    """Find minimum number of GPUs needed to hold model weights.

    Tries 1, 2, 3, ... GPUs (largest-first, matching FASTEST_FIRST order).
    "Fits" means GGUF size + safety margin per GPU < combined total VRAM.

    Args:
        model_size_mb: Total GGUF file size in MiB.
        per_gpu_total_mb: Total VRAM per GPU in MiB, sorted descending.

    Returns:
        Minimum GPU count (1 to len(per_gpu_total_mb)).
    """
    for n in range(1, len(per_gpu_total_mb) + 1):
        available = sum(per_gpu_total_mb[:n])
        if model_size_mb < available - (_MIN_GPU_SAFETY_MARGIN_MB * n):
            return n
    return len(per_gpu_total_mb)


def _fmt_test(
    iteration: int | str,
    context: int,
    fits: bool | None = None,
    gpu_list: list[dict[str, Any]] | None = None,
    cuda_gpu_names: list[str] | None = None,
    split: str = "",
    label: str = "",
) -> str:
    """Central formatter for calibration test results.

    Produces consistent output like:
      [3] 2:1:1:1 | ctx 180.800 | ✓ | RTX8000: 947 MB, P40: 1.049 MB, P40: 717 MB
      [A.2] 37:6:5:0 | ctx 32.768 | ✗ OOM

    Args:
        iteration: Step number or label (e.g. 3, "A.2", "B1")
        context: Context tokens being tested
        fits: True/False/None (None = testing, no result yet)
        gpu_list: Per-GPU VRAM info dicts
        cuda_gpu_names: CUDA-ordered GPU names for consistent labeling
        split: Tensor-split string (e.g. "26:11:11:0") — omitted if empty
        label: Optional extra label (e.g. "native", "projected")
    """
    parts = [f"[{iteration}]"]

    if split:
        parts.append(split)
    parts.append(f"ctx {format_number(context)}")

    if label:
        parts.append(f"({label})")

    if fits is None:
        return " | ".join(parts) + " ..."

    status = "✓" if fits else "✗"
    parts.append(status)

    # VRAM detail
    # gpu_list is expected to be in CUDA order (produced by _to_cuda_order);
    # cuda_gpu_names is parallel to that. We iterate positionally — matching
    # by name would collapse same-named GPUs (e.g. 2x RTX 8000) onto a single
    # entry and mis-report per-GPU VRAM.
    if gpu_list:
        vram_parts = []
        if cuda_gpu_names and len(cuda_gpu_names) == len(gpu_list):
            for name, g in zip(cuda_gpu_names, gpu_list):
                vram_parts.append(f"{name}: {format_number(g['free_mb'])} MB")
        else:
            for g in gpu_list:
                name = g.get("name", f"GPU{g.get('index', '?')}")
                vram_parts.append(f"{name}: {format_number(g['free_mb'])} MB")
        if vram_parts:
            parts.append(", ".join(vram_parts))
    elif fits is False:
        parts.append("OOM")

    return " | ".join(parts)


def _format_cuda_detail(
    gpu_list: list[dict[str, Any]],
    cuda_gpu_names: list[str] | None = None,
) -> str:
    """Format GPU VRAM detail string — generic function for all calibration phases.

    If cuda_gpu_names is provided, reorder gpu_list to match CUDA order
    and label as CUDA0, CUDA1, etc. Otherwise use gpu_list as-is with
    cuda_id field (if present) or GPU index as label.
    """
    # gpu_list is expected to be in CUDA order; cuda_gpu_names parallel to it.
    # Matching by name (as previously done) silently collapses same-named GPUs
    # (e.g. 2x RTX 8000) onto a single entry — iterate positionally instead.
    if cuda_gpu_names and len(cuda_gpu_names) == len(gpu_list):
        parts: list[str] = []
        for i, (name, g) in enumerate(zip(cuda_gpu_names, gpu_list)):
            parts.append(
                f"{name} (CUDA{i}): {format_number(g['free_mb'])} MB free"
            )
        if parts:
            return ", ".join(parts)
    # Use cuda_id if available, else GPU index
    parts = []
    for i, g in enumerate(gpu_list):
        cuda_label = g.get("cuda_id", f"GPU{g.get('index', i)}")
        parts.append(
            f"{g['name']} ({cuda_label}): {format_number(g['free_mb'])} MB free"
        )
    return ", ".join(parts)


def parse_llamaswap_config(config_path: Path) -> Dict[str, Dict]:
    """
    Parse llama-swap YAML config and extract per-model info.

    Returns:
        Dict mapping model_id to:
        - gguf_path: path to GGUF file (from --model flag)
        - llama_server_bin: path to llama-server binary
        - current_context: current -c value
        - ngl: current -ngl value
        - full_cmd: the complete cmd string
    """
    if not config_path.exists():
        logger.warning(f"llama-swap config not found: {config_path}")
        return {}

    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    if not config or not config.get("models"):
        return {}

    result: Dict[str, Dict] = {}

    for model_id, model_config in config["models"].items():
        cmd = model_config.get("cmd", "")
        if not cmd:
            continue

        # Extract llama-server binary (first part of cmd)
        parts = cmd.split()
        llama_server_bin = parts[0] if parts else ""

        # Extract --model path
        gguf_path = ""
        for i, part in enumerate(parts):
            if part == "--model" and i + 1 < len(parts):
                gguf_path = parts[i + 1]
                break

        # Extract -c (context) value
        current_context = 0
        for i, part in enumerate(parts):
            if part == "-c" and i + 1 < len(parts):
                try:
                    current_context = int(parts[i + 1])
                except ValueError:
                    pass
                break

        # Extract -ngl value
        ngl = 99
        for i, part in enumerate(parts):
            if part == "-ngl" and i + 1 < len(parts):
                try:
                    ngl = int(parts[i + 1])
                except ValueError:
                    pass
                break

        # Extract -ctk (KV cache key quantization)
        kv_cache_quant = ""
        for i, part in enumerate(parts):
            if part == "-ctk" and i + 1 < len(parts):
                kv_cache_quant = parts[i + 1]
                break

        # Extract --reasoning-format value (deepseek, none, or absent)
        reasoning_format = ""
        for i, part in enumerate(parts):
            if part == "--reasoning-format" and i + 1 < len(parts):
                reasoning_format = parts[i + 1]
                break

        # Extract env variables (e.g. CUDA_VISIBLE_DEVICES)
        env_list = model_config.get("env", [])
        env_dict: Dict[str, str] = {}
        for env_entry in env_list:
            if isinstance(env_entry, str) and "=" in env_entry:
                k, v = env_entry.split("=", 1)
                env_dict[k.strip()] = v.strip()

        result[model_id] = {
            "gguf_path": gguf_path,
            "llama_server_bin": llama_server_bin,
            "current_context": current_context,
            "ngl": ngl,
            "kv_cache_quant": kv_cache_quant,
            "reasoning_format": reasoning_format,
            "full_cmd": cmd,
            "env": env_dict,
        }

    return result


def parse_sampling_from_cmd(cmd: str) -> Dict[str, float]:
    """
    Extract sampling parameters from a llama-server cmd string.

    Returns only explicitly set values. Missing params = not in dict.
    Mapping: --temp → temperature, --top-k → top_k, --top-p → top_p, etc.
    """
    flag_map = {
        "--temp": "temperature",
        "--top-k": "top_k",
        "--top-p": "top_p",
        "--min-p": "min_p",
        "--repeat-penalty": "repeat_penalty",
    }
    parts = cmd.split()
    result: Dict[str, float] = {}
    for i, part in enumerate(parts):
        if part in flag_map and i + 1 < len(parts):
            try:
                result[flag_map[part]] = float(parts[i + 1])
            except ValueError:
                pass
    return result


def _ensure_in_group(config: dict, model_id: str, group_name: str = "main") -> None:
    """Ensure a model is in the specified llama-swap group.

    Adds the model to the group's members list if not already present.
    Creates the group if it doesn't exist.
    """
    groups = config.setdefault("groups", {})
    group = groups.setdefault(group_name, {"exclusive": True, "swap": True, "members": []})
    members = group.setdefault("members", [])
    if model_id not in members:
        members.append(model_id)


def _read_llamaswap_yaml(config_path: Path) -> dict:
    """Read llama-swap config as Python dict."""
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def _write_llamaswap_yaml(config_path: Path, config: dict) -> None:
    """Write llama-swap config from Python dict."""
    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _get_model_cmd(config: dict, model_id: str) -> str | None:
    """Get cmd string for a model from parsed config dict."""
    models = config.get("models", {})
    model = models.get(model_id)
    if not model:
        return None
    return model.get("cmd", "")


def _set_model_cmd(config: dict, model_id: str, cmd: str) -> None:
    """Set cmd string for a model in config dict."""
    config["models"][model_id]["cmd"] = cmd


def update_llamaswap_context(
    config_path: Path,
    model_id: str,
    new_context: int
) -> bool:
    """Update the -c value in llama-swap YAML for a specific model."""
    if not config_path.exists():
        logger.error(f"llama-swap config not found: {config_path}")
        return False

    config = _read_llamaswap_yaml(config_path)
    cmd = _get_model_cmd(config, model_id)
    if cmd is None:
        logger.error(f"Model {model_id} not found in llama-swap config")
        return False

    new_cmd = re.sub(r'-c\s+\d+', f'-c {new_context}', cmd)
    if new_cmd == cmd:
        logger.info(f"llama-swap config already up to date: {model_id} -c {new_context}")
        return True

    _set_model_cmd(config, model_id, new_cmd)
    _write_llamaswap_yaml(config_path, config)
    logger.info(f"Updated llama-swap config: {model_id} → -c {new_context}")
    return True


def update_llamaswap_ngl(
    config_path: Path,
    model_id: str,
    new_ngl: int
) -> bool:
    """Update the -ngl value in llama-swap YAML for a specific model."""
    if not config_path.exists():
        logger.error(f"llama-swap config not found: {config_path}")
        return False

    config = _read_llamaswap_yaml(config_path)
    cmd = _get_model_cmd(config, model_id)
    if cmd is None:
        logger.error(f"Model {model_id} not found in llama-swap config")
        return False

    new_cmd = re.sub(r'-ngl\s+\d+', f'-ngl {new_ngl}', cmd)
    if new_cmd == cmd:
        logger.info(f"llama-swap config already up to date: {model_id} -ngl {new_ngl}")
        return True

    _set_model_cmd(config, model_id, new_cmd)
    _write_llamaswap_yaml(config_path, config)
    logger.info(f"Updated llama-swap config: {model_id} → -ngl {new_ngl}")
    return True


def update_llamaswap_tensor_split(
    config_path: Path,
    model_id: str,
    ratios: list[float],
) -> bool:
    """Update --tensor-split / -ts in llama-swap YAML for a specific model.

    Trims trailing zeros (inactive GPUs hidden by CUDA_VISIBLE_DEVICES).
    If tensor-split is absent, inserts it after -ngl with -sm layer.
    """
    if not config_path.exists():
        logger.error(f"llama-swap config not found: {config_path}")
        return False

    config = _read_llamaswap_yaml(config_path)
    cmd = _get_model_cmd(config, model_id)
    if cmd is None:
        logger.error(f"Model {model_id} not found in llama-swap config")
        return False

    # Trim trailing zeros (CUDA_VISIBLE_DEVICES limits visible GPUs)
    trimmed = list(ratios)
    while len(trimmed) > 1 and trimmed[-1] == 0:
        trimmed.pop()

    new_cmd = _ensure_tensor_split(cmd, trimmed)
    if new_cmd == cmd:
        logger.info(f"llama-swap config already up to date: {model_id} tensor-split")
        return True

    _set_model_cmd(config, model_id, new_cmd)
    _write_llamaswap_yaml(config_path, config)
    logger.info(f"Updated llama-swap config: {model_id} → tensor-split {trimmed}")
    return True


def update_llamaswap_cuda_visible(
    config_path: Path,
    model_id: str,
    num_active_gpus: int,
    total_gpus: int,
) -> bool:
    """Set CUDA_VISIBLE_DEVICES env in llama-swap YAML for a model.

    Also trims tensor-split trailing zeros to match visible GPU count.
    If all GPUs are active, removes the env restriction entirely.
    """
    if not config_path.exists():
        return False

    config = _read_llamaswap_yaml(config_path)
    models = config.get("models", {})
    model = models.get(model_id)
    if not model:
        logger.error(f"Model {model_id} not found in llama-swap config")
        return False

    cuda_vis = ",".join(str(i) for i in range(num_active_gpus))

    # Trim trailing zeros from tensor-split in cmd
    cmd = model.get("cmd", "")
    if num_active_gpus < total_gpus:
        ts_match = re.search(r'(--tensor-split|-ts)\s+([\d.,]+)', cmd)
        if ts_match:
            ts_vals = [v for v in ts_match.group(2).split(',') if v]
            while len(ts_vals) > 1 and ts_vals[-1] in ('0', '0.0'):
                ts_vals.pop()
            trimmed = ','.join(ts_vals)
            cmd = cmd[:ts_match.start(2)] + trimmed + cmd[ts_match.end(2):]
            model["cmd"] = cmd

    if num_active_gpus >= total_gpus:
        # Remove env restriction
        if "env" in model:
            del model["env"]
            logger.info(f"Removed CUDA_VISIBLE_DEVICES for {model_id} (all GPUs active)")
    else:
        model["env"] = [f"CUDA_VISIBLE_DEVICES={cuda_vis}"]
        logger.info(f"Set CUDA_VISIBLE_DEVICES={cuda_vis} for {model_id}")

    _write_llamaswap_yaml(config_path, config)
    return True


def update_llamaswap_kv_cache_quant(
    config_path: Path,
    model_id: str,
    kv_quant: str,
) -> bool:
    """Update or insert -ctk/-ctv quantization in llama-swap YAML for a specific model.

    Replaces existing -ctk/-ctv values or inserts them before --port if absent.
    Returns True if the config was modified.
    """
    if not config_path.exists():
        logger.error(f"llama-swap config not found: {config_path}")
        return False

    config = _read_llamaswap_yaml(config_path)
    cmd = _get_model_cmd(config, model_id)
    if cmd is None:
        logger.error(f"Model {model_id} not found in llama-swap config")
        return False

    new_cmd = _inject_kv_quant(cmd, kv_quant)
    if new_cmd == cmd:
        return False

    _set_model_cmd(config, model_id, new_cmd)
    _write_llamaswap_yaml(config_path, config)
    logger.info(f"Updated llama-swap config: {model_id} → KV cache {kv_quant}")
    return True


def update_llamaswap_reasoning_format(
    config_path: Path,
    model_id: str,
    fmt: str = "deepseek",
) -> bool:
    """Add or update --reasoning-format in llama-swap YAML for a model.

    Models that produce reasoning_content (not <think> tags in content)
    need --reasoning-format deepseek for llama-server to split the output.
    If the flag already exists with the correct value, no change is made.
    """
    if not config_path.exists():
        return False

    config = _read_llamaswap_yaml(config_path)
    cmd = _get_model_cmd(config, model_id)
    if cmd is None:
        return False

    if '--reasoning-format ' in cmd:
        if f'--reasoning-format {fmt}' in cmd:
            return False  # Already correct
        new_cmd = re.sub(r'--reasoning-format\s+\S+', f'--reasoning-format {fmt}', cmd)
    else:
        new_cmd = cmd.replace(' --jinja', f' --jinja --reasoning-format {fmt}')

    if new_cmd == cmd:
        return False

    _set_model_cmd(config, model_id, new_cmd)
    _write_llamaswap_yaml(config_path, config)
    logger.info(f"Updated llama-swap config: {model_id} → --reasoning-format {fmt}")
    return True


def _remove_llamaswap_kv_cache_quant(config_path: Path, model_id: str) -> bool:
    """Remove -ctk/-ctv flags from llama-swap YAML for a specific model.

    Used when calibration determines f16 KV is optimal (no quantization needed).
    """
    if not config_path.exists():
        return False

    config = _read_llamaswap_yaml(config_path)
    cmd = _get_model_cmd(config, model_id)
    if cmd is None:
        return False

    new_cmd = _inject_kv_quant(cmd, "f16")  # "f16" removes -ctk/-ctv
    if new_cmd == cmd:
        return False

    _set_model_cmd(config, model_id, new_cmd)
    _write_llamaswap_yaml(config_path, config)
    logger.info(f"Removed KV cache quant from llama-swap config: {model_id}")
    return True


def _read_server_log(process: subprocess.Popen) -> str:
    """Read output from the server's log file (replaces stdout pipe reading)."""
    log_path = getattr(process, '_server_log', None)
    if not log_path:
        return ""
    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
    except OSError:
        return ""



def _parse_memory_breakdown(
    log_path: str,
) -> Optional[Dict[str, Dict[str, int]]]:
    """Parse llama_memory_breakdown_print from server exit log.

    This line is printed when llama-server exits and gives physical VRAM totals:
        llama_memory_breakdown_print: |   - CUDA0 (RTX 3090 Ti) | 24563 = 1886 + (18233 = 17524 +     408 +     300) +        4444 |
    Format: | GPU_NAME | total = free + (self = model + context + compute) + unaccounted |

    Returns dict per GPU: {"CUDA0": {"total": 24563, "free": 1886, "self": 18233}}
    Returns None if breakdown not found (server didn't exit cleanly, old llama.cpp build).
    """
    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
    except OSError:
        return None

    result: Dict[str, Dict[str, int]] = {}
    # Match CUDA GPU lines from breakdown:
    #   |   - CUDA0 (RTX 3090 Ti) | 24563 = 1886 + (18233 = 17524 + ...
    for match in re.finditer(
        r'llama_memory_breakdown_print:\s*\|\s+-\s+(CUDA\d+)\s+\([^)]+\)\s*\|\s*'
        r'(\d+)\s*=\s*(\d+)\s*\+\s*\(\s*(\d+)',
        content,
    ):
        gpu_id = match.group(1)
        result[gpu_id] = {
            "total": int(match.group(2)),
            "free": int(match.group(3)),
            "self": int(match.group(4)),
        }

    return result if result else None


def _measure_process_ram_mb(pid: int) -> Optional[int]:
    """Read physical RAM usage (VmRSS) from /proc/<pid>/status.

    Returns RSS in MB, or None if unreadable.
    """
    try:
        with open(f"/proc/{pid}/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    # Format: "VmRSS:   21045632 kB"
                    kb = int(line.split()[1])
                    return kb // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _get_fit_params_binary(full_cmd: str) -> Path:
    """Derive llama-fit-params binary path from llama-server command.

    Both binaries are built in the same CMake build directory.
    """
    server_bin = shlex.split(full_cmd)[0]
    return Path(server_bin).parent / "llama-fit-params"


# GPU-relevant flags that affect VRAM projection.
# These must be forwarded to llama-fit-params for accurate results.
_GPU_FLAGS = {
    "-ngl", "--flash-attn", "-ctk", "-ctv",
    "-ts", "--tensor-split", "-sm", "--split-mode",
    "-np", "-ub", "-b",
    "--rpc",
}


def _build_fit_params_cmd(
    full_cmd: str, gguf_path: Path, context: int, ngl: Optional[int] = None,
) -> list[str]:
    """Build llama-fit-params command from llama-server command.

    Extracts only GPU-relevant flags (that affect VRAM usage).
    Omits port, threads, mlock, fit — fit-params runs its own fitting.

    Args:
        ngl: If set, overrides -ngl from full_cmd.
    """
    fit_bin = str(_get_fit_params_binary(full_cmd))
    cmd = [fit_bin, "--model", str(gguf_path), "-c", str(context)]

    parts = shlex.split(full_cmd)
    i = 1  # skip binary path
    while i < len(parts):
        if parts[i] in _GPU_FLAGS and i + 1 < len(parts):
            if ngl is not None and parts[i] == "-ngl":
                i += 2
                continue
            cmd.extend([parts[i], parts[i + 1]])
            i += 2
        else:
            i += 1

    if ngl is not None:
        cmd.extend(["-ngl", str(ngl)])

    return cmd


def _calculate_max_context_per_gpu(
    per_gpu_low: dict[str, dict[str, int]],
    per_gpu_high: dict[str, dict[str, int]],
    ctx_low: int,
    ctx_high: int,
    safety_margin: int = LLAMACPP_VRAM_SAFETY_MARGIN,
) -> tuple[int, float]:
    """Calculate maximum context from per-GPU VRAM projections (multi-GPU aware).

    For each GPU independently:
      gpu_rate = (used_high - used_low) / (ctx_high - ctx_low)
      max_ctx  = ctx_low + (free_at_ctx_low - SAFETY_MARGIN) / gpu_rate

    The bottleneck GPU (lowest max_ctx) determines the overall limit.
    This avoids the overestimate of combined VRAM calculations where
    a larger GPU masks the bottleneck of a smaller one.

    Returns:
        (max_context, combined_rate) where combined_rate is total MiB per token.
    """
    gpu_limits: list[tuple[str, int, int]] = []  # (gpu_id, max_ctx, free_at_low)
    combined_rate = 0.0

    for gpu_id in sorted(per_gpu_low):
        if gpu_id not in per_gpu_high:
            continue

        free_low = per_gpu_low[gpu_id]["free"]
        used_low = per_gpu_low[gpu_id]["used"]
        used_high = per_gpu_high[gpu_id]["used"]

        gpu_rate = (used_high - used_low) / (ctx_high - ctx_low)

        if gpu_rate <= 0:
            # GPU has no context-dependent VRAM growth (e.g. tensor-split 0)
            # — not participating in context scaling, skip as bottleneck
            continue

        combined_rate += gpu_rate

        headroom = free_low - safety_margin
        if headroom <= 0:
            gpu_limits.append((gpu_id, CALIBRATION_MIN_CONTEXT, free_low))
            continue

        gpu_max = ctx_low + int(headroom / gpu_rate)
        gpu_limits.append((gpu_id, max(gpu_max, CALIBRATION_MIN_CONTEXT), free_low))

    if not gpu_limits:
        return CALIBRATION_MIN_CONTEXT, combined_rate

    # Bottleneck GPU determines overall max
    bottleneck_id, max_ctx, _ = min(gpu_limits, key=lambda x: x[1])
    for gpu_id, gpu_max, free_low in gpu_limits:
        marker = " ← bottleneck" if gpu_id == bottleneck_id else ""
        logger.info(
            f"Per-GPU projection: {gpu_id}: max ~{gpu_max} tokens "
            f"(free={free_low} MiB, margin={safety_margin} MiB){marker}"
        )

    # Fallback: if per-GPU bottleneck is at minimum (likely wrong tensor-split),
    # use total VRAM across all GPUs for a more realistic estimate.
    if max_ctx <= CALIBRATION_MIN_CONTEXT and combined_rate > 0:
        total_free_low = sum(per_gpu_low[gid]["free"] for gid in per_gpu_low)
        total_headroom = total_free_low - safety_margin * len(per_gpu_low)
        if total_headroom > 0:
            total_max = ctx_low + int(total_headroom / combined_rate)
            logger.info(
                f"Per-GPU bottleneck at minimum — fallback to total VRAM: "
                f"~{total_max} tokens (total free={total_free_low} MiB)"
            )
            return min(total_max, ctx_high), combined_rate

    return max_ctx, combined_rate


def _project_max_context(
    full_cmd: str, gguf_path: Path, native_context: int,
    ngl: int = 99, safety_margin: int = 0,
) -> Optional[int]:
    """Quick projection: can this model fit native context in VRAM?

    Uses llama-fit-params (~0.3s) to project VRAM usage at native context.
    Returns projected max context (based on MB/token ratio), or None on error.
    """
    try:
        projections = _fit_params_per_gpu_projection(full_cmd, gguf_path, native_context, ngl)
        # Check if ALL GPUs have enough free VRAM
        all_fit = all(
            gpu["free"] >= safety_margin for gpu in projections.values()
        )
        if all_fit:
            return native_context

        # Calculate max context from the tightest GPU
        # Find the MiB/token ratio from the projection
        min_ctx_cmd = _inject_kv_quant(full_cmd, "f16")
        low_projections = _fit_params_per_gpu_projection(min_ctx_cmd, gguf_path, 2048, ngl)
        # Estimate ratio from difference
        for gpu_id in projections:
            if gpu_id in low_projections:
                used_high = projections[gpu_id]["used"]
                used_low = low_projections[gpu_id]["used"]
                ctx_diff = native_context - 2048
                if ctx_diff > 0 and used_high > used_low:
                    mb_per_tok = (used_high - used_low) / ctx_diff
                    free_at_low = low_projections[gpu_id]["free"]
                    max_extra_tokens = int((free_at_low - safety_margin) / mb_per_tok) if mb_per_tok > 0 else 0
                    return min(2048 + max(0, max_extra_tokens), native_context)
        return None
    except (RuntimeError, subprocess.TimeoutExpired, OSError):
        return None


def _fit_params_per_gpu_projection(
    full_cmd: str, gguf_path: Path, context: int, ngl: int,
) -> dict[str, dict[str, int]]:
    """Get per-GPU VRAM projections from llama-fit-params.

    Multi-GPU format (parsed first):
        CUDA0 (Quadro RTX 8000):  45355 total,  43710 used,   1478 free vs. target of   1024

    Single-GPU format (fallback):
        projected to use 15011 MiB of device memory vs. 23285 MiB of free device memory
        will leave 8273 >= 1024 MiB of free device memory

    Returns: {"CUDA0": {"name": "...", "total": ..., "used": ..., "free": ...}, ...}
    Raises RuntimeError if no GPU projections found in output.
    """
    cmd = _build_fit_params_cmd(full_cmd, gguf_path, context, ngl=ngl)
    # Ensure CUDA0 = largest GPU (matches our VRAM-descending sorting)
    fit_env = os.environ.copy()
    fit_env["CUDA_DEVICE_ORDER"] = "FASTEST_FIRST"
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, env=fit_env)
    output = result.stdout + result.stderr
    log_message(f"fit-params ctx={context} ngl={ngl}: exit={result.returncode}", category="stats")
    # Log per-GPU lines and errors for diagnostics
    for line in output.splitlines():
        if "CUDA" in line or "total" in line or "free" in line or "error" in line.lower() or "failed" in line.lower():
            log_message(f"fit-params: {line.strip()}", category="stats")

    per_gpu: dict[str, dict[str, Any]] = {}

    # Multi-GPU: per-GPU memory lines
    for match in re.finditer(
        r'(CUDA\d+)\s+\(([^)]+)\)\s*:\s*(\d+)\s+total\s*,\s*(\d+)\s+used\s*,\s*(-?\d+)\s+free',
        output,
    ):
        gpu_id = match.group(1)
        per_gpu[gpu_id] = {
            "name": match.group(2).strip(),
            "total": int(match.group(3)),
            "used": int(match.group(4)),
            "free": int(match.group(5)),
        }

    # Single-GPU fallback: parse summary lines
    if not per_gpu:
        proj_match = re.search(
            r'projected to use\s+(\d+)\s+MiB.*?vs\.\s+(\d+)\s+MiB',
            output,
        )
        leave_match = re.search(r'will leave\s+(-?\d+)', output)

        if proj_match:
            used = int(proj_match.group(1))
            free_before = int(proj_match.group(2))
            free_after = int(leave_match.group(1)) if leave_match else free_before - used

            # GPU name from "Device 0: NAME, compute capability"
            dev_match = re.search(r'Device\s+0:\s+(.+?),\s+compute', output)
            gpu_name = dev_match.group(1).strip() if dev_match else "GPU"

            per_gpu["CUDA0"] = {
                "name": gpu_name,
                "total": used + free_after,
                "used": used,
                "free": free_after,
            }

    if not per_gpu:
        raise RuntimeError(
            f"llama-fit-params: no GPU projections in output:\n{output[:500]}"
        )
    return per_gpu


def _find_max_ngl_for_context(
    full_cmd: str,
    gguf_path: Path,
    context: int,
    total_layers: int,
    safety_margin: int = LLAMACPP_VRAM_SAFETY_MARGIN,
) -> tuple[int, dict[str, dict[str, int]]]:
    """Binary search for highest NGL where all GPUs have free >= safety margin.

    Uses fit-params projections (~0.5s per iteration, ~6 iterations = ~3s).
    Returns (best_ngl, per_gpu_info) or (0, {}) if nothing fits.
    """
    ngl_low = 1
    ngl_high = total_layers
    best_ngl = 0
    best_info: dict[str, dict[str, int]] = {}

    while ngl_low <= ngl_high:
        ngl_mid = (ngl_low + ngl_high) // 2
        try:
            per_gpu = _fit_params_per_gpu_projection(
                full_cmd, gguf_path, context, ngl_mid,
            )
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            ngl_high = ngl_mid - 1
            continue

        min_free = min(info["free"] for info in per_gpu.values())
        if min_free >= safety_margin:
            best_ngl = ngl_mid
            best_info = per_gpu
            ngl_low = ngl_mid + 1
        else:
            ngl_high = ngl_mid - 1

    return best_ngl, best_info


# Module-level env for current calibration model (set by calibrate_llamacpp_model)
_calibration_env: Optional[Dict[str, str]] = None


async def _start_llama_server(
    full_cmd: str,
    context: int,
    port: int,
    ngl: Optional[int] = None,
) -> Optional[subprocess.Popen]:
    """Start a llama-server process using original llama-swap cmd.

    Replaces -c and --port/${PORT} in the original command,
    preserving all GPU flags (tensor-split, flash-attn, mlock etc.).
    Injects -np 1 and -fit off for stable calibration on Pascal GPUs.

    Uses _calibration_env (set by calibrate_llamacpp_model) for model-specific
    environment variables like CUDA_VISIBLE_DEVICES.

    Args:
        ngl: If set, replaces -ngl value in cmd (for hybrid calibration)
    """
    # Replace ${PORT} placeholder and --port value
    cmd_str = full_cmd.replace("${PORT}", str(port))

    # Replace -c value with test context
    cmd_str = re.sub(r'(-c\s+)\d+', rf'\g<1>{context}', cmd_str)

    # Replace -ngl value for hybrid calibration
    if ngl is not None:
        cmd_str = re.sub(r'(-ngl\s+)\d+', rf'\g<1>{ngl}', cmd_str)

    # Inject calibration-safe flags if not already present
    # -np 1: single slot (auto defaults to 4, wastes VRAM)
    # -fit off: fitting routine crashes on Pascal with tight VRAM
    if '-np ' not in cmd_str:
        cmd_str = cmd_str.replace(' --port', ' -np 1 --port')
    if '-fit ' not in cmd_str:
        cmd_str = cmd_str.replace(' --port', ' -fit off --port')

    args = shlex.split(cmd_str)
    ts_in_cmd = _parse_tensor_split_ratios(cmd_str)
    log_message(
        f"Starting llama-server: ts={ts_in_cmd}, ctx={context}, ngl={ngl}",
        category="stats",
    )
    log_message(f"llama-server cmd: {cmd_str}", category="stats")

    try:
        # Build process environment: inherit current env + model-specific overrides
        proc_env = os.environ.copy()
        proc_env["CUDA_DEVICE_ORDER"] = "FASTEST_FIRST"  # CUDA0 = largest GPU
        if _calibration_env:
            proc_env.update(_calibration_env)

        # Write stdout to tempfile (parseable for VRAM info, readable after crash)
        fd, log_path = tempfile.mkstemp(suffix='.log', prefix='llama_')
        process = subprocess.Popen(
            args,
            stdout=fd,
            stderr=subprocess.STDOUT,  # merge stderr into stdout — llama-server logs to stdout
            env=proc_env,
            preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN)
        )
        os.close(fd)  # Popen has dup'd the fd
        process._server_log = log_path  # type: ignore[attr-defined]
        return process
    except Exception as e:
        logger.error(f"Failed to start llama-server: {e}")
        if 'fd' in locals():
            try:
                os.close(fd)
            except OSError:
                pass
        if 'log_path' in locals():
            try:
                os.unlink(log_path)
            except OSError:
                pass
        return None


async def _wait_for_ready(
    port: int,
    timeout: float,
    process: subprocess.Popen
) -> bool:
    """Wait for llama-server to be ready (health endpoint)."""
    url = f"http://localhost:{port}/health"
    start = asyncio.get_event_loop().time()

    while (asyncio.get_event_loop().time() - start) < timeout:
        if process.poll() is not None:
            return False  # Process died — caller handles logging

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=2.0)
                if response.status_code == 200:
                    return True
        except (httpx.RequestError, httpx.TimeoutException):
            pass

        await asyncio.sleep(1.0)

    return False


async def _test_inference(
    port: int,
    process: subprocess.Popen,
    timeout: float = 120.0,
) -> bool:
    """Test actual inference — health check alone is insufficient.

    On tight VRAM, the server starts and passes health checks but crashes
    on the first real request due to additional CUDA kernel allocations.
    Timeout is generous (120s) because thinking models reason before responding.
    """
    url = f"http://localhost:{port}/v1/chat/completions"
    payload = {
        "model": "test",
        "messages": [{"role": "user", "content": "say ok"}],
        "max_tokens": 2,
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=timeout)
            if response.status_code == 200:
                data = response.json()
                # Verify we got actual content back (thinking models use reasoning_content)
                choices = data.get("choices", [])
                if choices:
                    msg = choices[0].get("message", {})
                    if msg.get("content") or msg.get("reasoning_content"):
                        return True
        return False
    except Exception:
        # Process may have crashed during inference
        return False


async def test_thinking_on_port(port: int) -> bool:
    """Test reasoning capability directly against a running llama-server.

    Used during calibration to avoid reloading the model through llama-swap.
    Checks if the model produces reasoning_content (OpenAI-compatible thinking).
    """
    from .config import THINKING_PROBE_TEMPERATURE
    url = f"http://localhost:{port}/v1/chat/completions"
    payload = {
        "model": "test",
        "messages": [{"role": "user", "content": "What is 2+3? Think step by step."}],
        "max_tokens": 200,
        "temperature": THINKING_PROBE_TEMPERATURE,
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=30.0)
            if response.status_code != 200:
                return False
            data = response.json()
            choices = data.get("choices", [])
            if not choices:
                return False
            msg = choices[0].get("message", {})
            content = msg.get("content", "")
            reasoning = msg.get("reasoning_content", "")
            return bool(reasoning) or "<think>" in content
    except Exception:
        return False


def _kill_process(process: subprocess.Popen, keep_log: bool = False) -> None:
    """Kill a llama-server process and wait for cleanup.

    Args:
        keep_log: If True, preserve the server log file for breakdown parsing.
                  Caller must call _cleanup_server_log() after reading the log.
    """
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
    if not keep_log:
        _cleanup_server_log(process)


def _cleanup_server_log(process: subprocess.Popen) -> None:
    """Remove the server's temporary log file."""
    log_path = getattr(process, '_server_log', None)
    if log_path:
        try:
            os.unlink(log_path)
        except OSError:
            pass


async def _start_and_verify(
    full_cmd: str,
    context: int,
    port: int,
    ngl: Optional[int] = None,
) -> Optional[subprocess.Popen]:
    """Start server, verify it works via health check + inference, return process.

    Returns the running process on success (caller must kill it), or None on failure.
    Health check alone is insufficient: on tight VRAM, the server starts OK but
    crashes on the first real inference request due to additional CUDA kernel
    allocations. This function tests actual inference to confirm the context fits.
    """
    process = await _start_llama_server(full_cmd, context, port, ngl=ngl)
    if not process:
        return None

    try:
        ready = await _wait_for_ready(port, LLAMACPP_HEALTH_TIMEOUT, process)
        if not ready:
            # Read log before killing (kill cleans up the tempfile)
            output = _read_server_log(process)
            _kill_process(process)
            if output.strip():
                logger.error(f"llama-server failed to start. output:\n{output[:2000]}")
            from aifred.backends.ollama import wait_for_vram_stable
            await wait_for_vram_stable(max_wait_seconds=10.0)
            return None

        if not await _test_inference(port, process):
            _kill_process(process)
            from aifred.backends.ollama import wait_for_vram_stable
            await wait_for_vram_stable(max_wait_seconds=10.0)
            return None

        return process
    except BaseException:
        # GeneratorExit (async generator abandoned, e.g. double-click calibrate) or
        # CancelledError: process is orphaned if not cleaned up here — it would hold VRAM.
        _kill_process(process)
        raise


async def _test_context_physical(
    full_cmd: str, context: int, port: int,
    run_thinking_test: bool = False,
    safety_margin: int = LLAMACPP_VRAM_SAFETY_MARGIN,
) -> tuple[bool, str, list[dict[str, Any]], Optional[bool]]:
    """Test if a context size fits in physical VRAM.

    Starts server, verifies health, measures free VRAM via nvidia-smi.
    If run_thinking_test=True, tests reasoning capability before killing
    the server (piggyback — no extra reload needed).

    Returns:
        (fits, detail, gpu_list, thinking_result)
        thinking_result: True/False if tested, None if not tested or server failed.
    """
    thinking_result: Optional[bool] = None

    process = await _start_and_verify(full_cmd, context, port)
    if not process:
        log_message(
            f"VRAM ctx={format_number(context)}: OOM (server failed to start)",
            category="stats",
        )
        return (False, "OOM", [], None)

    # GPU-wide free VRAM per GPU (multi-GPU: check ALL GPUs)
    all_gpus = get_all_gpus_memory_info()
    gpu_list: list[dict[str, Any]] = []
    min_free_mb: Optional[int] = None

    # Raw nvidia-smi + CUDA process list for VRAM discrepancy analysis
    try:
        raw = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,memory.total,memory.used,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5.0, check=False,
        )
        log_message(
            f"VRAM ctx={format_number(context)}: raw nvidia-smi: {raw.stdout.strip()}",
            category="stats",
        )
        procs = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_gpu_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5.0, check=False,
        )
        log_message(
            f"VRAM ctx={format_number(context)}: CUDA procs: {procs.stdout.strip()}",
            category="stats",
        )
    except Exception:
        pass

    if all_gpus and all_gpus["per_gpu"]:
        for g in all_gpus["per_gpu"]:
            gpu_list.append({
                "index": g["index"],
                "name": _short_gpu_name(g.get("gpu_model", f"GPU{g['index']}")),
                "total_mb": g["total_mb"],
                "free_mb": g["free_mb"],
            })
        min_free_mb = min(g["free_mb"] for g in gpu_list)
        gpu_list = _to_cuda_order(gpu_list)
        per_gpu_detail = _format_cuda_detail(gpu_list)
        log_message(
            f"VRAM ctx={format_number(context)}: nvidia-smi {per_gpu_detail}",
            category="stats",
        )
    else:
        per_gpu_detail = "VRAM unknown"
        log_message(
            f"VRAM ctx={format_number(context)}: nvidia-smi unavailable",
            category="stats",
        )

    # Decision: min free VRAM across ALL GPUs vs safety margin
    fits = not (min_free_mb is not None and min_free_mb < safety_margin)
    detail = per_gpu_detail if min_free_mb is not None else "VRAM unknown"

    if fits:
        log_message(f"VRAM ctx={format_number(context)}: OK — {detail}", category="stats")
    else:
        log_message(f"VRAM ctx={format_number(context)}: FAIL — {detail}", category="stats")

    # Piggyback thinking test while server is still running
    if run_thinking_test:
        thinking_result = await test_thinking_on_port(port)

    # Kill server, wait for VRAM to fully release
    from aifred.backends.ollama import wait_for_vram_stable
    _kill_process(process, keep_log=True)
    await wait_for_vram_stable(max_wait_seconds=10.0)

    # Breakdown: only for diagnostics
    log_path = getattr(process, '_server_log', None)
    if log_path:
        breakdown = _parse_memory_breakdown(log_path)
        if breakdown:
            for gpu_id, info in breakdown.items():
                log_message(
                    f"VRAM ctx={format_number(context)}: "
                    f"breakdown {gpu_id}: self={info['self']} MB, "
                    f"unaccounted={info['total'] - info['free'] - info['self']} MB",
                    category="stats",
                )
        _cleanup_server_log(process)
    else:
        _cleanup_server_log(process)

    return (fits, detail, gpu_list, thinking_result)


async def _binary_search_context(
    full_cmd: str,
    port: int,
    low: int,
    high: int,
    precision: int = LLAMACPP_CALIBRATION_PRECISION,
    cuda_gpu_names: list[str] | None = None,
    run_thinking_test: bool = False,
    safety_margin: int = LLAMACPP_VRAM_SAFETY_MARGIN,
    start_iteration: int = 0,
    early_abort_threshold: int = 0,
) -> AsyncIterator[str | int | dict]:
    """Binary search for max fitting context between low and high.

    Yields status strings during search. Final yield is the integer result
    (best fitting context found, or 0 if nothing fits).
    If run_thinking_test=True, piggybacks the thinking test on the first
    server start where the server comes up (regardless of fits).
    Yields {"thinking_result": bool} once captured.
    Yields {"iteration": int} with final counter for continuous numbering.

    early_abort_threshold: when > 0 and search upper bound drops to/below
    this value after a failing test, abort early — any further result
    would also be ≤ threshold, so the caller will try the next fallback
    (e.g. lower KV quantization) anyway.
    """
    result = 0
    iteration = start_iteration
    thinking_done = False
    last_success_gpus: list[dict] | None = None

    while high - low > precision:
        mid = (low + high) // 2
        iteration += 1
        want_thinking = run_thinking_test and not thinking_done
        mid_fits, _, mid_gpus, thinking_result = await _test_context_physical(
            full_cmd, mid, port, run_thinking_test=want_thinking,
            safety_margin=safety_margin,
        )
        if thinking_result is not None:
            thinking_done = True
            yield {"thinking_result": thinking_result}
        yield _fmt_test(iteration, mid, fits=mid_fits, gpu_list=mid_gpus,
                        cuda_gpu_names=cuda_gpu_names)
        if mid_fits:
            low = mid
            result = mid
            last_success_gpus = mid_gpus
        else:
            high = mid
            # Early abort: further search can only yield results ≤ high.
            # If high is already ≤ threshold, any further test is wasted time.
            if early_abort_threshold > 0 and high <= early_abort_threshold:
                yield (
                    f"Ceiling {format_number(high)} ≤ threshold "
                    f"{format_number(early_abort_threshold)} — aborting search"
                )
                break

    yield {"iteration": iteration, "final_gpu_list": last_success_gpus}
    yield result


async def _physical_context_search(
    full_cmd: str,
    gguf_path: Path,
    native_context: int,
    ngl: int,
    port: int,
    skip_balance: bool = False,
    run_thinking_test: bool = False,
    safety_margin: int = LLAMACPP_VRAM_SAFETY_MARGIN,
    projected_hint: Optional[int] = None,
    early_abort_threshold: int = 0,
) -> AsyncIterator[str | int | dict]:
    """Fit-params projection + physical binary search for max context.

    Generic core used by both GPU-only (Phase 1) and hybrid calibration.
    If multi-GPU: detects VRAM asymmetry after first test and adjusts tensor-split
    before binary search (avoids searching with unbalanced split).
    skip_balance: skip layer balance step (when speed-split will follow anyway).
    projected_hint: if provided, skip fit-params projection and use this value.
    early_abort_threshold: passed through to binary search — abort when the
    search upper bound falls below this value (skip the futile tail).

    Yields: progress strings, metadata dict, final int result.
    Dict: {"gpu_list": [...], "balanced_cmd": str | None}
    """
    # 1. Always test native context first — no fit-params projection.
    # Projection was unreliable with varying VRAM conditions (TTS loaded etc.).
    # Physical test is the ground truth; binary search refines from there.
    #
    # --- DISABLED: fit-params projection ---
    # per_gpu_low_proj: dict[str, dict[str, int]] = {}
    # if projected_hint is not None:
    #     projected = min(projected_hint, native_context)
    # else:
    #     ctx_low = min(CALIBRATION_MIN_CONTEXT, native_context // 2)
    #     try:
    #         per_gpu_low_proj = _fit_params_per_gpu_projection(
    #             full_cmd, gguf_path, ctx_low, ngl=ngl,
    #         )
    #         per_gpu_high = _fit_params_per_gpu_projection(
    #             full_cmd, gguf_path, native_context, ngl=ngl,
    #         )
    #         projected, rate = _calculate_max_context_per_gpu(
    #             per_gpu_low_proj, per_gpu_high, ctx_low, native_context,
    #             safety_margin=safety_margin,
    #         )
    #         projected = min(projected, native_context)
    #         yield (
    #             f"Projection: {format_number(rate, 4)} MiB/tok, "
    #             f"max ~{format_number(projected)} tokens"
    #         )
    #     except (RuntimeError, OSError, subprocess.TimeoutExpired):
    #         yield "VRAM projection failed"
    #         yield 0
    #         return
    # --- END DISABLED ---

    projected = native_context

    # 2. Physical test at native context

    iteration = 0
    ctx_label = "native"

    thinking_result: Optional[bool] = None
    iteration += 1
    fits, detail, gpu_list, thinking_result = await _test_context_physical(
        full_cmd, projected, port, run_thinking_test=run_thinking_test,
        safety_margin=safety_margin,
    )
    # gpu_list is already in CUDA order (_to_cuda_order tagged it with cuda_id
    # labels inside _test_context_physical). The previous VRAM-projection code
    # populated per_gpu_low and did a name-based remap here, but that block is
    # disabled; per_gpu_low is permanently empty, so the remap never runs and
    # would collapse same-named GPUs (2x RTX 8000) onto gpu_list[0] anyway.
    cuda_gpu_list: list[dict[str, Any]] = gpu_list

    # CUDA-ordered GPU names for consistent formatting everywhere
    cuda_gpu_names = [g["name"] for g in cuda_gpu_list]

    # Show first test result
    yield _fmt_test(iteration, projected, fits=fits, gpu_list=cuda_gpu_list,
                    cuda_gpu_names=cuda_gpu_names, label=ctx_label)

    # 3. Multi-GPU layer optimization (N-GPU compatible).
    # Only speed-optimize when native fits. Balance (if needed) is deferred
    # until AFTER the binary search has found a fitting ctx — testing balance
    # at a ctx that causes OOM is wasted work (balance can only help if the
    # test at least loads).
    balance_adjusted = False
    skip_speed = False
    current_ratios = _parse_tensor_split_ratios(full_cmd)
    total_layers = 0
    mb_per_layer = 0
    layers: list[int] = []
    old_layers_fmt = ""
    # Balance eligibility depends on the command having a multi-GPU split —
    # NOT on cuda_gpu_list length (which is empty when the initial test OOMs,
    # but we may still be able to balance after binary search finds a fitting ctx).
    balance_eligible = (
        not skip_balance
        and current_ratios
        and len(current_ratios) >= 2
    )

    if balance_eligible:
        total_layers = get_gguf_layer_count(gguf_path) or 0
        if total_layers:
            from .gguf_utils import get_gguf_total_size

            model_size_mb = get_gguf_total_size(gguf_path) / (1024 ** 2)
            mb_per_layer = int(model_size_mb / total_layers)

            # Current layer distribution from ratios
            sum_ratios = sum(current_ratios)
            layers = [
                round(total_layers * r / sum_ratios)
                for r in current_ratios
            ]
            layers[-1] = total_layers - sum(layers[:-1])
            old_layers_fmt = _format_layer_split([float(n) for n in layers])

            # Phase 3A (speed-optimize at native) requires a fitting native test
            # AND cuda_gpu_list populated — which implies the test succeeded.
            if fits and projected == native_context and len(cuda_gpu_list) >= 2:
                # --- 3A: Speed-optimize (native context fits with headroom) ---
                fastest_free = cuda_gpu_list[0]["free_mb"]
                fastest_name = cuda_gpu_list[0]["name"]
                extra_possible = int(fastest_free / mb_per_layer)
                estimated_max = min(layers[0] + extra_possible, total_layers)

                yield (
                    f"VRAM headroom: {_format_cuda_detail(cuda_gpu_list)} — "
                    f"layers {old_layers_fmt} (~{format_number(mb_per_layer)} MB/layer)"
                )

                if estimated_max > layers[0]:
                    yield (
                        f"Speed-optimizing base: pushing layers to {fastest_name} "
                        f"at native ctx={format_number(native_context)}"
                    )

                    best_cuda0 = layers[0]

                    # Test estimated split first
                    est_ratios = _build_layer_split(
                        total_layers, estimated_max, current_ratios,
                    )
                    est_split = _format_layer_split(est_ratios)
                    est_cmd = _replace_tensor_split(full_cmd, est_ratios)
                    iteration += 1
                    est_fits, _, est_gpus, _ = await _test_context_physical(
                        est_cmd, native_context, port,
                        safety_margin=safety_margin,
                    )
                    yield _fmt_test(iteration, native_context, fits=est_fits,
                                    gpu_list=est_gpus, cuda_gpu_names=cuda_gpu_names,
                                    split=est_split)

                    if est_fits:
                        best_cuda0 = estimated_max
                        low, high = estimated_max + 1, total_layers
                    else:
                        low, high = layers[0] + 1, estimated_max - 1

                    # Binary search for exact boundary
                    while low <= high:
                        mid = (low + high) // 2
                        mid_ratios = _build_layer_split(
                            total_layers, mid, current_ratios,
                        )
                        mid_split = _format_layer_split(mid_ratios)
                        mid_cmd = _replace_tensor_split(full_cmd, mid_ratios)
                        iteration += 1
                        mid_fits, _, mid_gpus, _ = await _test_context_physical(
                            mid_cmd, native_context, port,
                            safety_margin=safety_margin,
                        )
                        yield _fmt_test(iteration, native_context, fits=mid_fits,
                                        gpu_list=mid_gpus, cuda_gpu_names=cuda_gpu_names,
                                        split=mid_split)
                        if mid_fits:
                            best_cuda0 = mid
                            low = mid + 1
                        else:
                            high = mid - 1

                    if best_cuda0 > layers[0]:
                        new_ratios = _build_layer_split(
                            total_layers, best_cuda0, current_ratios,
                        )
                        new_split = _format_layer_split(new_ratios)
                        full_cmd = _replace_tensor_split(full_cmd, new_ratios)
                        balance_adjusted = True
                        yield f"Base optimized: {old_layers_fmt} → {new_split} at native context"

                        if best_cuda0 >= total_layers:
                            skip_speed = True

    # 4. Binary search with (possibly adjusted) full_cmd and projected
    result = 0
    # GPU list from the last successful test (used for post-search balance)
    post_search_gpu_list: list[dict] | None = None

    if fits:
        result = projected
        post_search_gpu_list = cuda_gpu_list

        # 4a. If projected < native, test native directly first
        if result < native_context:
            iteration += 1
            want_thinking = run_thinking_test and thinking_result is None
            native_fits, _, native_gpus, native_thinking = await _test_context_physical(
                full_cmd, native_context, port, run_thinking_test=want_thinking,
                safety_margin=safety_margin,
            )
            if native_thinking is not None:
                thinking_result = native_thinking
            yield _fmt_test(iteration, native_context, fits=native_fits,
                            gpu_list=native_gpus, cuda_gpu_names=cuda_gpu_names,
                            label="native")

            if native_fits:
                # Native fits — done, no binary search needed
                result = native_context
                if native_gpus:
                    post_search_gpu_list = native_gpus
            else:
                # Native doesn't fit — binary search between projected and native
                async for item in _binary_search_context(
                    full_cmd, port, result, native_context,
                    cuda_gpu_names=cuda_gpu_names,
                    run_thinking_test=run_thinking_test and thinking_result is None,
                    safety_margin=safety_margin,
                    start_iteration=iteration,
                    early_abort_threshold=early_abort_threshold,
                ):
                    if isinstance(item, int):
                        if item > result:
                            result = item
                    elif isinstance(item, dict):
                        if item.get("iteration") is not None:
                            iteration = item["iteration"]
                        if item.get("thinking_result") is not None:
                            thinking_result = item["thinking_result"]
                        if item.get("final_gpu_list"):
                            post_search_gpu_list = item["final_gpu_list"]
                    else:
                        yield item

        # No "Optimum" message needed when native fits — result IS native
    else:
        # Early exit: projected context is already at/below minimum useful.
        # Any downward search result would also be ≤ projected ≤ MIN_USEFUL —
        # skip physical tests and let the caller try the next KV level instead.
        if projected <= MIN_USEFUL_CONTEXT_TOKENS:
            yield (
                f"Projected ceiling {format_number(projected)} "
                f"≤ min-useful {format_number(MIN_USEFUL_CONTEXT_TOKENS)} — "
                f"skipping downward search"
            )
            yield {
                "gpu_list": cuda_gpu_list,
                "balanced_cmd": None,
                "skip_speed": skip_speed,
                "thinking_result": thinking_result,
            }
            yield 0
            return

        yield "Searching downward..."

        # 4b. Binary search between minimum useful and native context.
        async for item in _binary_search_context(
            full_cmd, port, CALIBRATION_MIN_CONTEXT, native_context,
            cuda_gpu_names=cuda_gpu_names,
            run_thinking_test=run_thinking_test and thinking_result is None,
            safety_margin=safety_margin,
            start_iteration=iteration,
            early_abort_threshold=early_abort_threshold,
        ):
            if isinstance(item, int):
                result = item
            elif isinstance(item, dict):
                if item.get("iteration") is not None:
                    iteration = item["iteration"]
                if item.get("thinking_result") is not None:
                    thinking_result = item["thinking_result"]
                if item.get("final_gpu_list"):
                    post_search_gpu_list = item["final_gpu_list"]
            else:
                yield item

    # 5. Post-search balance: at the found ctx, check VRAM imbalance and try
    # a single layer shift. Unlike the old pre-search balance, this tests at
    # an actually-loadable ctx — so the result is meaningful. If the shift
    # improves min-free, search upward for a higher ctx with the new split.
    # Checks against post_search_gpu_list (populated by the last successful
    # test), NOT cuda_gpu_list — that list is empty when the native test OOMed.
    if (
        result > 0
        and balance_eligible
        and total_layers
        and not balance_adjusted
        and post_search_gpu_list
        and len(post_search_gpu_list) == len(current_ratios)
    ):
        ordered = _to_cuda_order(post_search_gpu_list)
        if ordered and len(ordered) == len(current_ratios):
            post_search_gpu_list = ordered

        # Propagate to cuda_gpu_list if the initial test OOMed (left it empty)
        if not cuda_gpu_list:
            cuda_gpu_list = post_search_gpu_list

        free_values = [g["free_mb"] for g in post_search_gpu_list]
        diff = max(free_values) - min(free_values)

        if diff >= 1024:
            from aifred.backends.ollama import wait_for_vram_stable

            bottleneck_idx = free_values.index(min(free_values))
            most_free_idx = free_values.index(max(free_values))
            old_min_free = min(free_values)

            yield (
                f"Post-search balance: {_format_cuda_detail(post_search_gpu_list)} "
                f"(diff {format_number(diff)} MB) at ctx={format_number(result)} — "
                f"layers {old_layers_fmt} (~{format_number(mb_per_layer)} MB/layer)"
            )

            new_layers = list(layers)
            new_layers[bottleneck_idx] -= 1
            new_layers[most_free_idx] += 1
            new_ratios = [float(n) for n in new_layers]
            new_layers_fmt = _format_layer_split(new_ratios)

            yield f"Moving 1 layer: {old_layers_fmt} → {new_layers_fmt}"

            candidate_cmd = _replace_tensor_split(full_cmd, new_ratios)
            await wait_for_vram_stable(max_wait_seconds=10.0)

            iteration += 1
            bal_fits, _, bal_gpus, _ = await _test_context_physical(
                candidate_cmd, result, port,
                safety_margin=safety_margin,
            )
            ordered_bal = _to_cuda_order(bal_gpus) if bal_gpus else None
            bal_display = ordered_bal if ordered_bal and len(ordered_bal) == len(cuda_gpu_list) else bal_gpus
            yield _fmt_test(iteration, result, fits=bal_fits,
                            gpu_list=bal_display,
                            cuda_gpu_names=cuda_gpu_names,
                            split=new_layers_fmt, label="balanced")

            if bal_fits and bal_display:
                new_free = [g["free_mb"] for g in bal_display]
                new_min_free = min(new_free)
                new_diff = max(new_free) - new_min_free

                if new_min_free > old_min_free:
                    balance_adjusted = True
                    full_cmd = candidate_cmd
                    current_ratios = new_ratios
                    cuda_gpu_list = bal_display
                    yield (
                        f"Balance improved (min free "
                        f"{format_number(old_min_free)}→{format_number(new_min_free)} MB, "
                        f"diff {format_number(new_diff)} MB)"
                    )

                    # Upward search: try higher ctx with the balanced split
                    if result < native_context:
                        yield "Searching upward with balanced split..."
                        async for item in _binary_search_context(
                            full_cmd, port, result, native_context,
                            cuda_gpu_names=cuda_gpu_names,
                            safety_margin=safety_margin,
                            start_iteration=iteration,
                            early_abort_threshold=early_abort_threshold,
                        ):
                            if isinstance(item, int):
                                if item > result:
                                    result = item
                            elif isinstance(item, dict):
                                if item.get("iteration") is not None:
                                    iteration = item["iteration"]
                                if item.get("final_gpu_list"):
                                    new_gpus = item["final_gpu_list"]
                                    ordered_new = _to_cuda_order(new_gpus)
                                    if ordered_new and len(ordered_new) == len(cuda_gpu_list):
                                        cuda_gpu_list = ordered_new
                            else:
                                yield item
                else:
                    yield (
                        f"Layer shift didn't improve balance "
                        f"(min free {format_number(old_min_free)}→"
                        f"{format_number(new_min_free)} MB) — keeping original split"
                    )
                    # Bottleneck on dominant GPU → pushing more onto it won't help
                    dominant_idx = current_ratios.index(max(current_ratios))
                    if bottleneck_idx == dominant_idx:
                        skip_speed = True
            else:
                yield (
                    f"Balance candidate {new_layers_fmt} doesn't fit at "
                    f"ctx={format_number(result)} — keeping original split"
                )

    if result and result < native_context:
        yield f"Optimum: {format_number(result)} tokens (native: {format_number(native_context)})"

    # Final metadata for caller
    yield {
        "gpu_list": cuda_gpu_list,
        "balanced_cmd": full_cmd if balance_adjusted else None,
        "skip_speed": skip_speed,
        "thinking_result": thinking_result,
    }

    yield result


def _copy_model_entry(config: dict, source_id: str, target_id: str) -> dict | None:
    """Deep-copy a model entry from config and return it under a new name.

    Returns None if source model not found. The copy is NOT inserted
    into config — caller decides where to put it.
    """
    models = config.get("models", {})
    source = models.get(source_id)
    if not source:
        return None
    return copy.deepcopy(source)


def add_llamaswap_speed_variant(
    config_path: Path,
    model_id: str,
    speed_split_cuda0: int,
    speed_split_rest: int,
    speed_context: int,
    num_gpus: int = 0,
    kv_quant: str = "f16",
    speed_layer_split: str = "",
) -> bool:
    """
    Add (or update) a -speed variant YAML entry in the llama-swap config.

    Copies the existing model_id dict entry, modifies cmd for speed,
    and writes it as model_id-speed.

    Args:
        speed_split_cuda0: Layer count for fastest GPU (CUDA0) — legacy,
                           ignored when speed_layer_split is provided.
        speed_split_rest: Layer count for remaining GPU(s) — legacy,
                          ignored when speed_layer_split is provided.
        speed_context: Context size for speed variant
        num_gpus: Number of active GPUs for speed variant (0 = use all,
                  same as base). Unused GPUs get tensor-split ratio 0.
        kv_quant: KV cache quantization for speed variant (independent
                  from base). "f16" means no -ctk/-ctv flags.
        speed_layer_split: Full balanced layer split as "A:B:C:D" string.
                           If provided, used directly instead of reconstructing
                           from cuda0/rest.
    """
    if not config_path.exists():
        logger.error(f"llama-swap config not found: {config_path}")
        return False

    config = _read_llamaswap_yaml(config_path)
    speed_model_id = f"{model_id}-speed"

    speed_entry = _copy_model_entry(config, model_id, speed_model_id)
    if speed_entry is None:
        logger.error(f"Model {model_id} not found in llama-swap config")
        return False

    cmd = speed_entry.get("cmd", "")
    original_ratios = _parse_tensor_split_ratios(cmd)

    # Use full layer split directly if provided (balanced distribution)
    if speed_layer_split:
        speed_ratios = [float(x) for x in speed_layer_split.split(":")]
    else:
        # Legacy path: reconstruct from cuda0/rest
        total_layers = speed_split_cuda0 + speed_split_rest
        if num_gpus > 0 and num_gpus < len(original_ratios):
            active_ratios = original_ratios[:num_gpus]
            speed_ratios = _build_layer_split(total_layers, speed_split_cuda0, active_ratios)
        else:
            speed_ratios = _build_layer_split(total_layers, speed_split_cuda0, original_ratios)

    # Trim trailing zeros (CUDA_VISIBLE_DEVICES limits visible GPUs)
    while len(speed_ratios) > 1 and speed_ratios[-1] == 0:
        speed_ratios.pop()

    cmd = _ensure_tensor_split(cmd, speed_ratios)
    cmd = re.sub(r'-c\s+\d+', f'-c {speed_context}', cmd)
    cmd = _inject_kv_quant(cmd, kv_quant)
    speed_entry["cmd"] = cmd

    # Set CUDA_VISIBLE_DEVICES if fewer GPUs than base
    if num_gpus > 0 and num_gpus < len(original_ratios):
        cuda_vis = ",".join(str(i) for i in range(num_gpus))
        speed_entry["env"] = [f"CUDA_VISIBLE_DEVICES={cuda_vis}"]

    # Insert/update in models dict
    models = config["models"]
    already_exists = speed_model_id in models

    if already_exists:
        models[speed_model_id] = speed_entry
    else:
        # Insert right after original model (preserving order)
        new_models: dict[str, Any] = {}
        for key, val in models.items():
            new_models[key] = val
            if key == model_id:
                new_models[speed_model_id] = speed_entry
        config["models"] = new_models

    _ensure_in_group(config, speed_model_id)
    _write_llamaswap_yaml(config_path, config)
    logger.info(f"{'Updated' if already_exists else 'Added'} speed variant: {speed_model_id}")
    return True


def add_llamaswap_tts_variant(
    config_path: Path,
    model_id: str,
    tts_context: int,
    tts_backend: str,
    kv_quant: str = "f16",
    tensor_split: str = "",
    num_gpus: int = 0,
    cuda_visible_devices: str = "",
    source_model_id: str | None = None,
) -> bool:
    """
    Add (or update) a TTS variant YAML entry in the llama-swap config.

    Copies the existing model_id dict entry, modifies cmd context, KV quantization,
    and optionally tensor-split/CUDA_VISIBLE_DEVICES from TTS calibration.

    Args:
        config_path: Path to llama-swap config.yaml
        model_id: Base model ID (e.g. "GPT-OSS-120B-A5B-UD-Q8_K_XL")
        tts_context: Calibrated context with TTS VRAM reservation
        tts_backend: TTS backend name ("xtts" or "moss")
        kv_quant: KV cache quantization level ("f16", "q8_0", "q4_0")
        tensor_split: Calibrated tensor-split for TTS variant (e.g. "18,9,9")
        num_gpus: Number of GPUs for TTS variant. When non-zero and
            cuda_visible_devices is empty, emits CUDA_VISIBLE_DEVICES=0,1,...
            (shared mode — LLM occupies the first num_gpus GPUs).
        cuda_visible_devices: Explicit CUDA_VISIBLE_DEVICES value (e.g. "1").
            Takes precedence over num_gpus. Used in isolated mode when the
            LLM should sit on a specific GPU that isn't where TTS runs.
        source_model_id: Model entry to copy as template. Defaults to model_id
            (the base variant). In isolated mode pass the speed variant
            ``f"{model_id}-speed"`` so the TTS variant inherits the already-
            tuned single-GPU tensor-split and context.
    """
    if not config_path.exists():
        logger.error(f"llama-swap config not found: {config_path}")
        return False

    config = _read_llamaswap_yaml(config_path)
    tts_model_id = f"{model_id}-tts-{tts_backend}"
    src_id = source_model_id or model_id

    tts_entry = _copy_model_entry(config, src_id, tts_model_id)
    if tts_entry is None:
        logger.error(f"Source model {src_id} not found in llama-swap config")
        return False

    cmd = tts_entry.get("cmd", "")
    cmd = re.sub(r'-c\s+\d+', f'-c {tts_context}', cmd)
    cmd = _inject_kv_quant(cmd, kv_quant)

    # Apply TTS-specific tensor-split if calibration found a different one
    if tensor_split:
        cmd = re.sub(r'(--tensor-split|-ts)\s+[\d.,]+', f'-ts {tensor_split}', cmd)

    tts_entry["cmd"] = cmd

    # Apply CUDA_VISIBLE_DEVICES: explicit value wins (isolated mode),
    # otherwise derive from num_gpus (shared mode legacy behavior).
    if cuda_visible_devices:
        tts_entry["env"] = [f"CUDA_VISIBLE_DEVICES={cuda_visible_devices}"]
    elif num_gpus > 0:
        cuda_vis = ",".join(str(i) for i in range(num_gpus))
        tts_entry["env"] = [f"CUDA_VISIBLE_DEVICES={cuda_vis}"]

    # Insert/update in models dict
    models = config["models"]
    already_exists = tts_model_id in models

    if already_exists:
        models[tts_model_id] = tts_entry
    else:
        # Insert right after original model (preserving order)
        new_models: dict[str, Any] = {}
        for key, val in models.items():
            new_models[key] = val
            if key == model_id:
                new_models[tts_model_id] = tts_entry
        config["models"] = new_models

    _ensure_in_group(config, tts_model_id)
    _write_llamaswap_yaml(config_path, config)
    logger.info(f"{'Updated' if already_exists else 'Added'} TTS variant: {tts_model_id}")
    return True


def _inject_kv_quant(cmd: str, kv_quant: str) -> str:
    """Set -ctk/-ctv quantization in a llama-server command string.

    Replaces existing -ctk/-ctv values or inserts them before --port.
    Use kv_quant="" or "f16" to remove -ctk/-ctv (restore f16 default).
    """
    if not kv_quant or kv_quant == "f16":
        # Remove -ctk/-ctv to restore f16 default
        cmd = re.sub(r'\s*-ctk\s+\S+', '', cmd)
        cmd = re.sub(r'\s*-ctv\s+\S+', '', cmd)
        return cmd

    if '-ctk ' in cmd:
        cmd = re.sub(r'-ctk\s+\S+', f'-ctk {kv_quant}', cmd)
    else:
        cmd = cmd.replace(' --port', f' -ctk {kv_quant} --port')

    if '-ctv ' in cmd:
        cmd = re.sub(r'-ctv\s+\S+', f'-ctv {kv_quant}', cmd)
    else:
        cmd = cmd.replace(' --port', f' -ctv {kv_quant} --port')

    return cmd


def _has_tensor_split(full_cmd: str) -> bool:
    """Return True if the cmd uses multi-GPU tensor split."""
    return bool(re.search(r'(--tensor-split|-ts)\s+[\d.,]+', full_cmd))


def _check_single_gpu_fit(
    full_cmd: str,
    gguf_path: Path,
    native_context: int,
    num_gpus: int,
    safety_margin: int = LLAMACPP_VRAM_SAFETY_MARGIN,
) -> tuple[str, str, str] | None:
    """Check if model fits entirely on a single GPU at full native context.

    Tries each GPU in CUDA order (CUDA0 first = fastest with FASTEST_FIRST).
    Uses llama-fit-params with --tensor-split 1,0,...,0 to project single-GPU
    VRAM usage.  If the target GPU has enough free VRAM after the projection,
    single-GPU calibration is feasible.

    Returns:
        (cuda_id, gpu_name, modified_cmd) if single-GPU fits, None otherwise.
    """
    # Quick check: if model doesn't fit on the largest GPU, skip all tests
    from .gpu_utils import get_all_gpus_memory_info
    gpu_info = get_all_gpus_memory_info()
    if gpu_info:
        max_gpu_mb = max(g["total_mb"] for g in gpu_info["per_gpu"])
        from .gguf_utils import get_gguf_total_size
        model_mb = get_gguf_total_size(gguf_path) / (1024 ** 2)
        if model_mb > max_gpu_mb * 0.95:  # 95% — leave room for KV + overhead
            return None

    for cuda_idx in range(num_gpus):
        # Force all layers onto this GPU, 0 on all others
        ts_parts = ["0"] * num_gpus
        ts_parts[cuda_idx] = "1"
        ts_str = ",".join(ts_parts)
        test_cmd = f"{full_cmd} -sm layer --tensor-split {ts_str}"

        try:
            per_gpu = _fit_params_per_gpu_projection(
                test_cmd, gguf_path, native_context, ngl=99,
            )
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            continue

        target_id = f"CUDA{cuda_idx}"
        target = per_gpu.get(target_id)
        if not target:
            continue

        gpu_name = _short_gpu_name(str(target.get("name", f"GPU {cuda_idx}")))

        if target["free"] >= safety_margin:
            log_message(
                f"Single-GPU fit: {gpu_name} ({target_id}) "
                f"free={target['free']} MiB >= margin={safety_margin} MiB",
                category="stats",
            )
            return target_id, gpu_name, test_cmd

        log_message(
            f"Single-GPU check: {target_id} ({gpu_name}) insufficient "
            f"(free={target['free']} MiB < margin={safety_margin} MiB)",
            category="stats",
        )

    return None


def _parse_tensor_split_ratios(cmd: str) -> list[float]:
    """Extract all tensor-split ratios from cmd.

    "2,1" → [2.0, 1.0], "3,1,1" → [3.0, 1.0, 1.0].
    Returns [] if no tensor-split found.
    """
    match = re.search(r'(?:--tensor-split|-ts)\s+([\d.,]+)', cmd)
    if not match:
        return []
    return [float(v) for v in match.group(1).split(",") if v]


def _replace_tensor_split(cmd: str, ratios: list[float]) -> str:
    """Replace the tensor-split ratio in cmd."""
    new_val = ",".join(f"{r:g}" for r in ratios)
    cmd = re.sub(r'(--tensor-split\s+)[\d.,]+', rf'\g<1>{new_val}', cmd)
    cmd = re.sub(r'(-ts\s+)[\d.,]+', rf'\g<1>{new_val}', cmd)
    return cmd


def _ensure_tensor_split(cmd: str, ratios: list[float]) -> str:
    """Set tensor-split in cmd — replace if present, insert if absent."""
    if _has_tensor_split(cmd):
        return _replace_tensor_split(cmd, ratios)
    ts_val = ",".join(f"{r:g}" for r in ratios)
    return cmd.replace(' --port', f' -sm layer --tensor-split {ts_val} -fit off --port')


def _build_layer_split(
    total_layers: int,
    fastest_layers: int,
    original_ratios: list[float],
) -> list[float]:
    """Build N-GPU tensor-split with fastest_layers on GPU0, rest proportional.

    Distributes remaining layers among GPU1..N-1 based on their original
    ratio proportions. Works for 2, 3, or more GPUs.

    Example (3 GPUs, original 60:25:15, push to 80):
      → [80.0, 13.0, 7.0]  (rest 20 split as 62.5%/37.5%)
    """
    rest = total_layers - fastest_layers
    if len(original_ratios) <= 2 or rest <= 0:
        return [float(fastest_layers), float(max(rest, 0))]

    # Distribute rest among GPU1..N-1 proportionally
    other_ratios = original_ratios[1:]
    other_sum = sum(other_ratios)
    if other_sum <= 0:
        # Equal distribution if ratios are zero
        per_gpu = rest / len(other_ratios)
        return [float(fastest_layers)] + [per_gpu] * len(other_ratios)

    result = [float(fastest_layers)]
    assigned = 0
    for r in other_ratios[:-1]:
        n = round(rest * r / other_sum)
        result.append(float(n))
        assigned += n
    # Last GPU gets remainder (fixes rounding)
    result.append(float(rest - assigned))
    return result


def _group_gpus_by_name(gpu_names: list[str]) -> list[list[int]]:
    """Group GPU indices by model name.

    Input:  ["RTX 8000", "RTX 8000", "P40", "P40"]
    Output: [[0, 1], [2, 3]]  (RTX group, P40 group)

    Order: First group = fastest (GPU0 is always in first group).
    """
    groups: dict[str, list[int]] = {}
    for i, name in enumerate(gpu_names):
        groups.setdefault(name, []).append(i)
    first_name = gpu_names[0]
    result = [groups.pop(first_name)]
    result.extend(groups.values())
    return result


def _build_balanced_split(
    total_layers: int,
    fast_group_total: int,
    gpu_names: list[str],
) -> list[float]:
    """Build tensor-split with balanced distribution within speed classes.

    GPUs with the same name form a speed class. Layers are distributed
    equally within each class. The fast group (GPU0's class) gets
    fast_group_total layers, the rest is split among remaining groups
    proportionally by VRAM (approximated by group size, since GPUs of
    the same type have the same VRAM).

    Example: total=88, fast_total=72, names=["RTX 8000","RTX 8000","P40"]
    → Fast group (2x RTX): 72/2 = 36 each
    → Slow group (1x P40): 88-72 = 16
    → [36.0, 36.0, 16.0]
    """
    groups = _group_gpus_by_name(gpu_names)
    rest = total_layers - fast_group_total

    result = [0.0] * len(gpu_names)

    # Fast group: distribute equally
    fast_indices = groups[0]
    per_fast = fast_group_total // len(fast_indices)
    remainder = fast_group_total % len(fast_indices)
    for i, idx in enumerate(fast_indices):
        result[idx] = float(per_fast + (1 if i < remainder else 0))

    # Remaining groups: distribute rest proportionally by group size
    slow_groups = groups[1:]
    total_slow_gpus = sum(len(g) for g in slow_groups)
    if total_slow_gpus > 0 and rest > 0:
        assigned = 0
        for gi, group in enumerate(slow_groups):
            if gi == len(slow_groups) - 1:
                # Last group gets remainder (fixes rounding)
                group_total = rest - assigned
            else:
                group_total = round(rest * len(group) / total_slow_gpus)
                assigned += group_total
            per_gpu = group_total // len(group)
            group_remainder = group_total % len(group)
            for i, idx in enumerate(group):
                result[idx] = float(per_gpu + (1 if i < group_remainder else 0))

    return result


def _format_layer_split(ratios: list[float]) -> str:
    """Format layer split as 'A:B:C' string."""
    return ":".join(str(int(r)) for r in ratios)


async def _optimize_gpu_layers(
    base_cmd: str,
    gguf_path: Path,
    target_context: int,
    port: int,
    initial_layers: list[int],
    active_gpu_names: list[str],
    total_layers: int,
    total_gpus: int,
    min_gpus: int,
    mb_per_layer: float,
    phase_label: str,
    safety_margin: int = LLAMACPP_VRAM_SAFETY_MARGIN,
    per_gpu_total_mb: list[int] | None = None,
) -> AsyncIterator[str | list[float]]:
    """Binary search for max layers on fast GPU group at fixed context.

    Groups GPUs by name (same name = same speed class). The fast group
    (GPU0's class) is optimized as a unit — layers are distributed equally
    within it. Binary search finds the max total layers for the fast group.

    Args:
        active_gpu_names: GPU model names for active GPUs (length == min_gpus).
        per_gpu_total_mb: Total VRAM per active GPU in MiB (length == min_gpus).
                          Used to cap the search so we don't exceed physical VRAM.

    Yields progress strings.  Final yield is the optimized padded ratios
    as ``list[float]`` (length == total_gpus).
    """
    initial_fast_total = sum(initial_layers[i] for i in _group_gpus_by_name(active_gpu_names)[0])
    padded_ratios = _build_balanced_split(total_layers, initial_fast_total, active_gpu_names)
    padded_ratios += [0.0] * (total_gpus - min_gpus)
    initial_cmd = _replace_tensor_split(base_cmd, padded_ratios)

    # fit-params projection at target context
    try:
        per_gpu = _fit_params_per_gpu_projection(
            initial_cmd, gguf_path, target_context, ngl=99,
        )
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        yield f"Phase {phase_label}: fit-params projection failed — keeping initial split"
        yield padded_ratios
        return

    gpu_ids = sorted(per_gpu.keys())
    if not gpu_ids:
        yield f"Phase {phase_label}: no GPU info from fit-params"
        yield padded_ratios
        return

    # Identify fast group from GPU names
    groups = _group_gpus_by_name(active_gpu_names)
    fast_indices = groups[0]
    fast_count = len(fast_indices)
    fastest_name = _short_gpu_name(active_gpu_names[fast_indices[0]])

    free_info = ", ".join(
        f"{_short_gpu_name(str(per_gpu[gid].get('name', gid)))} ({gid}): "
        f"{format_number(per_gpu[gid]['free'])} MB free"
        for gid in gpu_ids[:min_gpus]
    )
    yield f"Projected at ctx={format_number(target_context)}: {free_info}"

    # Sum free VRAM across the entire fast group
    free_fast_total = sum(
        per_gpu.get(f"CUDA{i}", {}).get("free", 0) for i in fast_indices
    )

    # Cap: fast group can't hold more layers than physical VRAM allows
    if per_gpu_total_mb and mb_per_layer > 0:
        max_physical_fast = sum(
            int(per_gpu_total_mb[i] / mb_per_layer) for i in fast_indices
        )
    else:
        max_physical_fast = total_layers

    extra_layers_possible = int(free_fast_total / mb_per_layer)
    estimated_max = min(
        initial_fast_total + extra_layers_possible,
        total_layers,
        max_physical_fast,
    )

    if estimated_max <= initial_fast_total:
        yield f"No headroom for layer optimization on {fastest_name} (x{fast_count})"
        yield padded_ratios
        return

    yield (
        f"Phase {phase_label}: Max layers on {fastest_name} (x{fast_count}) "
        f"at ctx={format_number(target_context)}"
    )

    def _split_and_pad(fast_total: int) -> list[float]:
        active = _build_balanced_split(total_layers, fast_total, active_gpu_names)
        return active + [0.0] * (total_gpus - min_gpus)

    est_padded = _split_and_pad(estimated_max)
    est_split = _format_layer_split(est_padded)
    est_cmd = _replace_tensor_split(base_cmd, est_padded)

    iteration = 1
    est_fits, _, est_gpus, _ = await _test_context_physical(
        est_cmd, target_context, port,
        safety_margin=safety_margin,
    )
    yield _fmt_test(
        f"{phase_label}.{iteration}", target_context,
        fits=est_fits, gpu_list=est_gpus, split=est_split,
    )

    if est_fits:
        low = estimated_max + 1
        high = min(total_layers, max_physical_fast)
        best_fast_total = estimated_max
    else:
        low = initial_fast_total + 1
        high = estimated_max - 1
        best_fast_total = initial_fast_total

    while low <= high:
        mid = (low + high) // 2
        mid_padded = _split_and_pad(mid)
        mid_split = _format_layer_split(mid_padded)
        mid_cmd = _replace_tensor_split(base_cmd, mid_padded)

        iteration += 1
        mid_fits, _, mid_gpus, _ = await _test_context_physical(
            mid_cmd, target_context, port,
            safety_margin=safety_margin,
        )
        yield _fmt_test(
            f"{phase_label}.{iteration}", target_context,
            fits=mid_fits, gpu_list=mid_gpus, split=mid_split,
        )

        if mid_fits:
            best_fast_total = mid
            low = mid + 1
        else:
            high = mid - 1

    padded_ratios = _split_and_pad(best_fast_total)
    best_split = _format_layer_split(padded_ratios)
    yield f"Phase {phase_label} result: {best_split} ({min_gpus}/{total_gpus} GPUs)"
    yield padded_ratios


async def _calibrate_speed_split(
    full_cmd: str,
    port: int,
    target_context: int,
    native_context: int,
    gguf_path: Path,
    per_gpu_total_mb: list[int],
    safety_margin: int = LLAMACPP_VRAM_SAFETY_MARGIN,
    base_kv: str = "f16",
    per_gpu_free_mb: list[int] | None = None,
    cuda_gpu_names: list[str] | None = None,
) -> AsyncIterator[str]:
    """Speed split: use minimum GPUs for fewer boundary transfers.

    Strategy: fewer GPU boundaries = less transfer overhead per token = faster.
    1. Find minimum GPU count needed to hold model weights.
    2. Build tensor-split with only those GPUs active (rest get 0).
    3. Phase A: Binary search for max layers on fast GPU group at target_context.
    4. Phase B: Maximize context at found split. KV chain starts at base_kv
       (inherited from Phase 1), falls back to q8_0/q4_0 if needed.

    Args:
        per_gpu_total_mb: Total VRAM per GPU in MiB, sorted descending
                          (FASTEST_FIRST order).
        per_gpu_free_mb: Free VRAM per GPU in MiB (same order). If provided,
                         used for min_gpus calculation (accounts for TTS VRAM).
        cuda_gpu_names: GPU model names in CUDA order (FASTEST_FIRST).
                        Used for balanced layer distribution within speed classes.

    Yields progress messages.
    Final: "__SPEED__:{layer_split},{context},{num_gpus},{kv_quant}" or "__SPEED__:0".
    layer_split is the full distribution e.g. "36:36:16:0".
    """
    from .gguf_utils import get_gguf_total_size

    total_gpus = len(per_gpu_total_mb)

    original_ratios = _parse_tensor_split_ratios(full_cmd)
    if not original_ratios:
        yield "Speed calibration: cannot parse original tensor-split ratio"
        yield "__SPEED__:0"
        return

    total_layers = get_gguf_layer_count(gguf_path)
    if not total_layers:
        yield "Speed calibration: cannot read layer count from GGUF"
        yield "__SPEED__:0"
        return

    model_size_mb = get_gguf_total_size(gguf_path) / (1024 ** 2)
    mb_per_layer = model_size_mb / total_layers

    # ── Step 1: Find minimum GPU count ──
    # Use free VRAM if available (accounts for TTS models in VRAM)
    gpu_vram_for_fit = per_gpu_free_mb if per_gpu_free_mb else per_gpu_total_mb
    min_gpus = _find_min_gpus(model_size_mb, gpu_vram_for_fit)

    if min_gpus >= total_gpus:
        yield (
            f"Speed calibration: model needs all {total_gpus} GPUs "
            f"({format_number(model_size_mb, 0)} MB) — no speed variant possible"
        )
        yield "__SPEED__:0"
        return

    gpu_names_info = ", ".join(
        f"GPU{i}: {format_number(v)} MB" for i, v in enumerate(per_gpu_total_mb)
    )
    yield (
        f"Speed calibration: model ({format_number(model_size_mb, 0)} MB) "
        f"fits on {min_gpus}/{total_gpus} GPUs ({gpu_names_info})"
    )

    # ── Step 2: Try GPU counts from min_gpus upward ──
    # If min_gpus fails (e.g. TTS eating VRAM), try more GPUs.
    for try_gpus in range(min_gpus, total_gpus):
        found = False
        async for item in _try_speed_split_for_gpu_count(
            try_gpus, total_gpus, total_layers, per_gpu_total_mb, mb_per_layer,
            full_cmd, gguf_path, native_context, target_context, port,
            base_kv, safety_margin,
            cuda_gpu_names=cuda_gpu_names,
        ):
            if isinstance(item, str):
                if item.startswith("__SPEED__:") and not item.startswith("__SPEED__:0"):
                    yield item
                    return
                elif item.startswith("__SPEED__:0"):
                    # This GPU count failed — try next
                    found = False
                else:
                    yield item
        if not found and try_gpus + 1 < total_gpus:
            yield f"Speed: {try_gpus} GPUs insufficient — trying {try_gpus + 1} GPUs"

    yield f"Speed split: no working GPU count found ({min_gpus}..{total_gpus - 1})"
    yield "__SPEED__:0"


async def _try_speed_split_for_gpu_count(
    try_gpus: int,
    total_gpus: int,
    total_layers: int,
    per_gpu_total_mb: list[int],
    mb_per_layer: float,
    full_cmd: str,
    gguf_path: Path,
    native_context: int,
    target_context: int,
    port: int,
    base_kv: str,
    safety_margin: int,
    cuda_gpu_names: list[str] | None = None,
) -> AsyncIterator[str]:
    """Try speed calibration with a specific GPU count. Yields progress + __SPEED__ result."""
    min_gpus = try_gpus

    # Active GPU names (first min_gpus entries)
    active_names = (cuda_gpu_names or [])[:min_gpus]

    # Initial layer distribution: balanced within speed classes
    if active_names and len(active_names) == min_gpus:
        # Use balanced split from the start
        groups = _group_gpus_by_name(active_names)
        # Initial: distribute proportionally by VRAM across groups
        active_vram = per_gpu_total_mb[:min_gpus]
        fast_vram = sum(active_vram[i] for i in groups[0])
        total_active_vram = sum(active_vram)
        initial_fast_total = round(total_layers * fast_vram / total_active_vram)
        # Ensure we don't exceed total
        initial_fast_total = min(initial_fast_total, total_layers)
        padded_ratios = _build_balanced_split(total_layers, initial_fast_total, active_names)
    else:
        # No GPU names available — fall back to VRAM-proportional
        active_vram = per_gpu_total_mb[:min_gpus]
        min_vram = min(active_vram)
        active_ratios = [float(max(1, round(v / min_vram))) for v in active_vram]
        sum_active = sum(active_ratios)
        layers = [round(total_layers * r / sum_active) for r in active_ratios]
        layers[-1] = total_layers - sum(layers[:-1])
        padded_ratios = [float(n) for n in layers]

    # Pad with zeros for inactive GPUs
    padded_ratios += [0.0] * (total_gpus - min_gpus)
    layers = [int(r) for r in padded_ratios[:min_gpus]]

    # Speed variant inherits KV level from Phase 1 (if f16 didn't fit there,
    # it won't fit here with fewer GPUs either)
    speed_base_cmd = _inject_kv_quant(full_cmd, base_kv)

    layers_fmt = _format_layer_split(padded_ratios)
    yield f"Initial speed split: {layers_fmt}"

    # ── Phase A: Optimize layers on fast GPU group via binary search ──
    async for opt_result in _optimize_gpu_layers(
        base_cmd=speed_base_cmd,
        gguf_path=gguf_path,
        target_context=target_context,
        port=port,
        initial_layers=layers,
        active_gpu_names=active_names if active_names else [f"GPU{i}" for i in range(min_gpus)],
        total_layers=total_layers,
        total_gpus=total_gpus,
        min_gpus=min_gpus,
        mb_per_layer=mb_per_layer,
        phase_label="A",
        safety_margin=safety_margin,
        per_gpu_total_mb=per_gpu_total_mb[:min_gpus] if per_gpu_total_mb else None,
    ):
        if isinstance(opt_result, list):
            padded_ratios = opt_result
        else:
            yield opt_result

    best_split = _format_layer_split(padded_ratios)

    # ── Phase B: Maximize context with KV chain starting at base_kv ──
    # KV level inherited from Phase 1 — no point trying higher levels
    all_kv = ["f16", "q8_0", "q4_0"]
    start_idx = all_kv.index(base_kv) if base_kv in all_kv else 0
    speed_kv_levels = all_kv[start_idx:]
    best_context = 0
    best_speed_kv = base_kv

    for kv_level in speed_kv_levels:
        if kv_level == "q8_0" and best_context >= MIN_USEFUL_CONTEXT_TOKENS:
            break  # f16 already sufficient

        kv_cmd = _inject_kv_quant(
            _replace_tensor_split(speed_base_cmd, padded_ratios),
            kv_level,
        )
        yield f"Phase B: Maximizing context at {best_split} (KV={kv_level})"

        # Project max context via fit-params
        try:
            per_gpu_low = _fit_params_per_gpu_projection(
                kv_cmd, gguf_path, CALIBRATION_MIN_CONTEXT, ngl=99,
            )
            per_gpu_high = _fit_params_per_gpu_projection(
                kv_cmd, gguf_path, native_context, ngl=99,
            )

            # Filter to active GPUs only (ratio > 0)
            active_gpu_ids = set(f"CUDA{i}" for i in range(min_gpus))
            filtered_low = {k: v for k, v in per_gpu_low.items() if k in active_gpu_ids}
            filtered_high = {k: v for k, v in per_gpu_high.items() if k in active_gpu_ids}

            projected_ctx, _ = _calculate_max_context_per_gpu(
                filtered_low, filtered_high, CALIBRATION_MIN_CONTEXT, native_context,
                safety_margin=safety_margin,
            )
            projected_ctx = min(projected_ctx, native_context)
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            projected_ctx = target_context

        yield f"Projected: {best_split} supports ~{format_number(projected_ctx)} context (KV={kv_level})"

        kv_best = 0

        # First test at projected context (or target_context if projection is lower)
        start_ctx = max(projected_ctx, target_context)
        start_ctx = min(start_ctx, native_context)

        iteration = 1
        fits, _, gpu_list, _ = await _test_context_physical(
            kv_cmd, start_ctx, port,
            safety_margin=safety_margin,
        )
        yield _fmt_test(f"B{iteration}", start_ctx, fits=fits, gpu_list=gpu_list, split=best_split)

        if fits:
            kv_best = start_ctx
            # Binary search upward: start_ctx → native_context
            if start_ctx < native_context:
                async for item in _binary_search_context(
                    kv_cmd, port, start_ctx, native_context,
                    safety_margin=safety_margin,
                    start_iteration=iteration,
                ):
                    if isinstance(item, int):
                        if item > kv_best:
                            kv_best = item
                    elif isinstance(item, dict):
                        if item.get("iteration") is not None:
                            iteration = item["iteration"]
                    else:
                        yield item
        else:
            # Projection was too optimistic — binary search downward
            if start_ctx > target_context:
                async for item in _binary_search_context(
                    kv_cmd, port, target_context, start_ctx,
                    safety_margin=safety_margin,
                    start_iteration=iteration,
                ):
                    if isinstance(item, int):
                        if item > kv_best:
                            kv_best = item
                    elif isinstance(item, dict):
                        if item.get("iteration") is not None:
                            iteration = item["iteration"]
                    else:
                        yield item

        if kv_best > best_context:
            best_context = kv_best
            best_speed_kv = kv_level

        # f16 reached native — no point trying q8_0
        if best_context >= native_context:
            break

        if kv_level == "f16" and best_context < MIN_USEFUL_CONTEXT_TOKENS:
            yield (
                f"KV=f16: {format_number(best_context)} tokens "
                f"< {format_number(MIN_USEFUL_CONTEXT_TOKENS)} minimum — "
                f"trying q8_0"
            )
            from aifred.backends.ollama import wait_for_vram_stable
            await wait_for_vram_stable(max_wait_seconds=10.0)

    if best_context < target_context:
        yield f"Speed split {best_split} doesn't reach minimum context"
        yield "__SPEED__:0"
        return

    ctx_info = ""
    if best_context > target_context:
        ctx_info = f", ctx={format_number(best_context)}"
    kv_info = f", KV={best_speed_kv}" if best_speed_kv != "f16" else ""
    yield f"Speed split found: {best_split}{ctx_info}{kv_info} ({min_gpus}/{total_gpus} GPUs)"
    yield f"__SPEED__:{best_split},{best_context},{min_gpus},{best_speed_kv}"


async def _calibrate_hybrid(
    model_id: str,
    gguf_path: Path,
    full_cmd: str,
    native_context: int,
    model_size_gb: float,
    total_layers: int,
    port: int = LLAMACPP_CALIBRATION_PORT,
    safety_margin: int = LLAMACPP_VRAM_SAFETY_MARGIN,
) -> AsyncIterator[str]:
    """
    Hybrid NGL+context calibration using fit-params per-GPU VRAM projection.

    Uses per-GPU projections from llama-fit-params (~0.5s each) instead of
    actual server starts (~30-60s each), reducing calibration from minutes to seconds.

    Algorithm:
    1. Build descending context targets (native → 128K → 64K → 32K → 16K)
    2. For each target: binary search NGL via fit-params per-GPU projection (~3s)
    3. Check RAM feasibility for CPU layers
    4. Binary search context upward via fit-params (~3s)
    5. Yield: "__HYBRID__:{context}:{ngl}" — caller handles server verification
    """
    from .gpu_utils import get_free_ram_mb

    yield f"Hybrid calibration: {total_layers} layers, fit-params projection"

    # Build descending context targets
    context_targets = []
    if native_context > 131072:
        context_targets.append(native_context)
        context_targets.append(131072)
    elif native_context > 65536:
        context_targets.append(native_context)
        context_targets.append(65536)
    else:
        context_targets.append(native_context)
    if native_context > 32768:
        context_targets.append(32768)
    context_targets.append(16384)
    seen: set[int] = set()
    unique_targets: list[int] = []
    for c in context_targets:
        if c not in seen:
            seen.add(c)
            unique_targets.append(c)
    context_targets = unique_targets

    best_ngl: Optional[int] = None
    best_ctx: Optional[int] = None

    for target_ctx in context_targets:
        yield f"Searching NGL for ctx={format_number(target_ctx)} via fit-params..."

        ngl, per_gpu = _find_max_ngl_for_context(
            full_cmd, gguf_path, target_ctx, total_layers,
            safety_margin=safety_margin,
        )

        if ngl < 1:
            yield f"  Skip ctx={format_number(target_ctx)}: no NGL fits in VRAM"
            continue

        # Check RAM feasibility (CPU layers consume host RAM)
        cpu_layers = total_layers - ngl
        vram_per_layer = model_size_gb * 1024 / total_layers
        ram_for_cpu_layers = cpu_layers * vram_per_layer
        free_ram = get_free_ram_mb()

        if free_ram and (free_ram - ram_for_cpu_layers) < MIN_FREE_RAM_MB:
            yield (
                f"  Skip ctx={format_number(target_ctx)}, ngl={ngl}: "
                f"not enough RAM for {cpu_layers} CPU layers "
                f"({format_number(int(ram_for_cpu_layers))} MB needed, "
                f"{format_number(free_ram)} MB free)"
            )
            continue

        min_free = min(info["free"] for info in per_gpu.values())
        gpu_detail = ", ".join(
            f"{_short_gpu_name(str(info.get('name', gpu)))} ({gpu}): {info['free']} MB free"
            for gpu, info in sorted(per_gpu.items())
        )
        yield (
            f"  Found ngl={ngl} ({cpu_layers} CPU layers), "
            f"min free: {min_free} MB ({gpu_detail})"
        )
        best_ngl = ngl
        best_ctx = target_ctx
        break

    if best_ngl is None or best_ctx is None:
        yield "Hybrid calibration failed: no working (ngl, context) combination found"
        return

    # Phase 2: Physical context search at the found NGL
    hybrid_cmd = re.sub(r'(-ngl\s+)\d+', rf'\g<1>{best_ngl}', full_cmd)
    yield f"NGL={best_ngl} found. Physical context search..."

    search_result = 0
    async for item in _physical_context_search(
        hybrid_cmd, gguf_path, native_context, ngl=best_ngl, port=port,
        safety_margin=safety_margin,
    ):
        if isinstance(item, int):
            search_result = item
        elif isinstance(item, dict):
            pass  # balance metadata not used in hybrid path
        else:
            yield item

    if search_result > 0:
        best_ctx = search_result

    cpu_layers = total_layers - best_ngl
    yield (
        f"Hybrid calibration complete: ngl={best_ngl}, "
        f"ctx={format_number(best_ctx)} tokens "
        f"({cpu_layers} layers on CPU)"
    )
    yield f"__HYBRID__:{best_ctx}:{best_ngl}"


async def calibrate_llamacpp_model(
    model_id: str,
    gguf_path: Path,
    full_cmd: str,
    port: int = LLAMACPP_CALIBRATION_PORT,
    config_path: Optional[Path] = None,
    min_kv: str = "f16",
    skip_thinking: bool = False,
) -> AsyncIterator[str]:
    """
    Projection-based calibration for a llama.cpp model.

    Phase 1 (GPU-only): VRAM projection via llama-fit-params → verify with server
    Phase 2 (Speed): Tensor-split optimization for multi-GPU (if Phase 1 succeeds)
    Phase 3 (Hybrid): CPU-offload fallback if GPU-only context < MIN_USEFUL_CONTEXT

    Args:
        min_kv: Minimum KV quantization level (inherited from prior calibration).
                Skips KV levels above this (e.g. min_kv="q8_0" skips f16).

    Async generator that yields progress messages.
    Final yield format: "__RESULT__:{context}:{ngl}:{mode}"
      mode: "gpu" or "hybrid"
      Example: "__RESULT__:222960:99:gpu" or "__RESULT__:131072:28:hybrid"
    Error: "__RESULT__:0:0:error"
    """
    # Step 1: Read GGUF metadata
    yield f"Reading GGUF metadata: {gguf_path.name}"

    native_context = get_gguf_native_context(gguf_path)
    if not native_context:
        yield "Could not read native context from GGUF metadata"
        yield "__RESULT__:0:0:error"
        return

    from .gguf_utils import get_gguf_total_size
    model_size_gb = get_gguf_total_size(gguf_path) / (1024 ** 3)
    quantization = extract_quantization_from_filename(gguf_path.name)

    gpu_info = get_all_gpus_memory_info()
    total_vram_mb = gpu_info["total_mb"] if gpu_info else 0
    model_size_mb = model_size_gb * 1024
    vram_ratio = model_size_mb / total_vram_mb if total_vram_mb > 0 else 0
    total_vram_gb = total_vram_mb / 1024

    yield (
        f"Model: {model_id} ({format_number(model_size_gb, 1)} GB), "
        f"native context: {format_number(native_context)} "
        f"(model = {vram_ratio:.0%} of {format_number(total_vram_gb, 1)} GB VRAM)"
    )

    # Vision-language models need extra VRAM for the CLIP compute buffer.
    is_vision = "--mmproj" in full_cmd
    safety_margin = LLAMACPP_VRAM_SAFETY_MARGIN + (
        LLAMACPP_VISION_VRAM_RESERVE if is_vision else 0
    )
    if is_vision:
        yield f"Vision model detected — VRAM reserve: {safety_margin} MB (base {LLAMACPP_VRAM_SAFETY_MARGIN} + CLIP {LLAMACPP_VISION_VRAM_RESERVE})"

    # Strip any existing -ctk/-ctv from cmd — Phase 1 starts with f16 KV
    # and the fallback chain will inject quantization only if needed.
    full_cmd = _inject_kv_quant(full_cmd, "f16")

    # Step 2: Check VRAM
    from aifred.backends.ollama import wait_for_vram_stable

    yield "Waiting for VRAM to stabilize..."
    stabilized, wait_time, free_vram = await wait_for_vram_stable(max_wait_seconds=15.0)
    yield f"Free VRAM: {format_number(free_vram)} MB"

    if free_vram < 1000:
        yield "Not enough VRAM (<1 GB free)"
        yield "__RESULT__:0:0:error"
        return

    # === Single-GPU optimization for multi-GPU systems ===
    # If the model fits entirely on one GPU at native context, restrict
    # calibration to that GPU (avoids unnecessary multi-GPU distribution
    # and enables optimal single-GPU performance).
    is_single_gpu = False
    if gpu_info and gpu_info["gpu_count"] > 1:
        existing_ratios = _parse_tensor_split_ratios(full_cmd)
        # Detect existing single-GPU pattern (e.g. 1,0 or 0,1)
        if existing_ratios and sum(1 for r in existing_ratios if r > 0) == 1:
            is_single_gpu = True
        elif not existing_ratios:
            single_result = _check_single_gpu_fit(
                full_cmd, gguf_path, native_context, gpu_info["gpu_count"],
                safety_margin=safety_margin,
            )
            if single_result:
                cuda_id, gpu_name, full_cmd = single_result
                is_single_gpu = True
                yield (
                    f"✓ Single-GPU: model fits on {gpu_name} ({cuda_id}) "
                    f"at full {format_number(native_context)} context"
                )

    # Helper: test thinking on running server (or start new one), yield result
    async def _finish_calibration(
        ctx: int, ngl: int, mode: str,
        process: Optional[subprocess.Popen] = None,
        thinking_result: Optional[bool] = None,
    ):
        """Test thinking on server, measure VRAM, save calibration, emit result.

        If thinking_result is provided, skips the reasoning test (already done
        during physical context search — saves one full server reload).
        If process is provided, reuses the already-running server (avoids reload).
        Otherwise starts a new server for the thinking test.
        VRAM is parsed from llama_memory_breakdown_print (written at server exit).
        """
        # Reuse existing server or start a new one
        if not process and thinking_result is None:
            process = await _start_and_verify(full_cmd, ctx, port, ngl=ngl if ngl != 99 else None)

        vram_per_gpu: Optional[Dict[str, int]] = None
        ram_cpu_mb: Optional[int] = None
        if thinking_result is not None:
            thinks = thinking_result
            yield f"Reasoning: {'yes' if thinks else 'no'} (tested during context search)"
        else:
            thinks = False
        if process:
            if thinking_result is None:
                yield "Testing reasoning capability..."
                thinks = await test_thinking_on_port(port)
            # Measure CPU RAM before killing (VmRSS from /proc)
            ram_cpu_mb = _measure_process_ram_mb(process.pid)
            if ram_cpu_mb and ram_cpu_mb > 0:
                yield f"RAM (CPU): {format_number(ram_cpu_mb)} MB"
            # Kill but keep log for breakdown parsing
            _kill_process(process, keep_log=True)
            await wait_for_vram_stable(max_wait_seconds=10.0)
            # Parse VRAM from breakdown (physical allocation per GPU)
            log_path = getattr(process, '_server_log', None)
            if log_path:
                breakdown = _parse_memory_breakdown(log_path)
                if breakdown:
                    # Use 'self' from breakdown (actual allocation, not virtual)
                    vram_per_gpu = {gpu: info["self"] for gpu, info in breakdown.items()}
                    total_mb = sum(vram_per_gpu.values())
                    if len(vram_per_gpu) > 1:
                        parts = ", ".join(
                            f"{gpu}: {format_number(mb)} MB"
                            for gpu, mb in sorted(vram_per_gpu.items())
                        )
                        yield f"VRAM (model): {format_number(total_mb)} MB ({parts})"
                    else:
                        yield f"VRAM (model): {format_number(total_mb)} MB"
                _cleanup_server_log(process)

        all_gpus = get_all_gpus_memory_info()
        gpu_model = ", ".join(all_gpus["gpu_models"]) if all_gpus and all_gpus.get("gpu_models") else "Unknown"
        add_llamacpp_calibration(
            model_id=model_id, max_context=ctx, native_context=native_context,
            gguf_path=str(gguf_path), quantization=quantization, gpu_model=gpu_model,
            model_size_gb=model_size_gb, ngl=ngl, mode=mode,
            vram_per_gpu=vram_per_gpu, ram_cpu_mb=ram_cpu_mb,
        )
        # Encode tensor-split and GPU count for TTS variant config
        ts_str = ""
        num_gpus = 0
        if optimized_ratios:
            ts_str = ",".join(f"{r:g}" for r in optimized_ratios if r > 0)
            num_gpus = sum(1 for r in optimized_ratios if r > 0)
        yield f"__RESULT__:{ctx}:{ngl}:{mode}:{'thinks' if thinks else 'nothink'}:{best_kv}:{ts_str}:{num_gpus}"

    # === Skip GPU-only for oversized models ===
    # Model weights alone exceed total VRAM → GPU-only is impossible,
    # jump straight to hybrid calibration (saves minutes of pointless OOM attempts)
    if vram_ratio > 1.0:
        oversized_msg = (
            f"Oversized model: {format_number(model_size_gb, 1)} GB > "
            f"{format_number(total_vram_gb, 1)} GB VRAM ({vram_ratio:.0%})"
        )
        yield oversized_msg
        yield "GPU-only phase skipped — hybrid calibration (GPU + CPU offload) required"

        total_layers = get_gguf_layer_count(gguf_path)
        if not total_layers:
            yield "Cannot read layer count from GGUF — hybrid mode unavailable"
            yield "__RESULT__:0:0:error"
            return

        hybrid_ctx = None
        hybrid_ngl = None
        async for hybrid_msg in _calibrate_hybrid(
            model_id=model_id,
            gguf_path=gguf_path,
            full_cmd=full_cmd,
            native_context=native_context,
            model_size_gb=model_size_gb,
            total_layers=total_layers,
            port=port,
            safety_margin=safety_margin,
        ):
            if hybrid_msg.startswith("__HYBRID__:"):
                parts = hybrid_msg.split(":")
                hybrid_ctx = int(parts[1])
                hybrid_ngl = int(parts[2])
            else:
                yield hybrid_msg

        if hybrid_ctx and hybrid_ngl:
            async for msg in _finish_calibration(hybrid_ctx, hybrid_ngl, "hybrid"):
                yield msg
            return

        yield "Calibration failed: no working configuration found — check llama-server logs"
        yield "__RESULT__:0:0:error"
        return

    # === PHASE 1: GPU-only calibration (ngl=99) with KV fallback chain ===
    # Strategy: f16 KV first (best quality, fastest on hardware without tensor cores).
    # If f16 doesn't reach native context → try q8_0 (nearly lossless, smaller KV).
    # q4_0 only as last resort when q8_0 < MIN_USEFUL — aggressive on attention
    # quality but better than falling through to hybrid (CPU offload = much slower).
    # Force ngl=99 for Phase 1 — the cmd may have a lower ngl from previous
    # hybrid calibration, but Phase 1 always tests all layers on GPU.
    phase1_cmd = re.sub(r'(-ngl\s+)\d+', r'\g<1>99', full_cmd)
    all_kv_levels = ["f16", "q8_0", "q4_0"]
    # Skip KV levels above min_kv (inherited from prior calibration)
    min_kv_idx = all_kv_levels.index(min_kv) if min_kv in all_kv_levels else 0
    kv_levels = all_kv_levels[min_kv_idx:]
    best_result = 0
    best_kv = min_kv
    thinking_result: Optional[bool] = True if skip_thinking else None
    optimized_ratios: Optional[list[float]] = None

    # For single-GPU: pre-set tensor-split ratios for config write-back
    if is_single_gpu:
        optimized_ratios = _parse_tensor_split_ratios(full_cmd)

    # === KV-level selection: always test native context physically ===
    # No fit-params projection — test native directly, binary search if needed.
    # Projection was unreliable with TTS-loaded VRAM (wrong tensor-split assumptions).
    #
    # # --- DISABLED: fit-params projection ---
    from .config import F16_KV_PREFER_THRESHOLD
    # kv_projections: dict[str, Optional[int]] = {}
    # if "f16" in kv_levels:
    #     f16_cmd = _inject_kv_quant(phase1_cmd, "f16")
    #     ctx_low = min(CALIBRATION_MIN_CONTEXT, native_context // 2)
    #     try:
    #         per_gpu_low = _fit_params_per_gpu_projection(f16_cmd, gguf_path, ctx_low, ngl=99)
    #         per_gpu_high = _fit_params_per_gpu_projection(f16_cmd, gguf_path, native_context, ngl=99)
    #         f16_projected, _ = _calculate_max_context_per_gpu(
    #             per_gpu_low, per_gpu_high, ctx_low, native_context, safety_margin=safety_margin,
    #         )
    #         f16_projected = min(f16_projected, native_context)
    #     except (RuntimeError, OSError, subprocess.TimeoutExpired):
    #         f16_projected = None
    #     kv_projections["f16"] = f16_projected
    #
    #     if f16_projected and f16_projected < native_context:
    #         if f16_projected >= F16_KV_PREFER_THRESHOLD:
    #             yield (
    #                 f"F16 projection: {format_number(f16_projected)} "
    #                 f"(≥{format_number(F16_KV_PREFER_THRESHOLD)} threshold) "
    #                 f"— using f16 (fast, high quality)"
    #             )
    #         else:
    #             yield (
    #                 f"F16 projection: {format_number(f16_projected)} < native {format_number(native_context)} "
    #                 f"— skipping f16, starting with q8_0"
    #             )
    #             kv_levels = all_kv_levels[all_kv_levels.index("q8_0"):]

    for kv_level in kv_levels:
        # Skip conditions:
        # - q8_0: skip if f16 already reached ≥256K (prefer f16 quality/speed)
        #         or if f16 reached native context (no gain possible)
        # - q4_0: skip unless q8_0 < MIN_USEFUL (last resort before hybrid)
        if kv_level == "q8_0" and (best_result >= native_context or best_result >= F16_KV_PREFER_THRESHOLD):
            break
        if kv_level == "q4_0" and best_result >= MIN_USEFUL_CONTEXT_TOKENS:
            break

        test_cmd = _inject_kv_quant(phase1_cmd, kv_level)
        ts_ratios = _parse_tensor_split_ratios(test_cmd)
        ts_display = _format_layer_split(ts_ratios) if ts_ratios else "auto"
        yield f"Phase 1: GPU-only (ngl=99, KV={kv_level}, ts={ts_display})"

        # For f16/q8_0: abort downward search early if ceiling drops below
        # MIN_USEFUL — a fallback KV level will be tried anyway. q4_0 is the
        # last resort, so let it search fully.
        abort_threshold = (
            MIN_USEFUL_CONTEXT_TOKENS
            if kv_level != "q4_0" and native_context > MIN_USEFUL_CONTEXT_TOKENS
            else 0
        )

        result = 0
        skip_speed = False
        # Run thinking test on first KV iteration (piggyback on first successful test)
        run_thinking = thinking_result is None
        async for item in _physical_context_search(
            test_cmd, gguf_path, native_context, ngl=99, port=port,
            skip_balance=is_single_gpu,
            run_thinking_test=run_thinking,
            safety_margin=safety_margin,
            early_abort_threshold=abort_threshold,
        ):
            if isinstance(item, int):
                result = item
            elif isinstance(item, dict):
                # Balance adjusted the tensor-split — propagate to base cmds
                balanced_cmd = item.get("balanced_cmd")
                if balanced_cmd:
                    new_ratios = _parse_tensor_split_ratios(balanced_cmd)
                    if new_ratios:
                        full_cmd = _replace_tensor_split(full_cmd, new_ratios)
                        phase1_cmd = _replace_tensor_split(phase1_cmd, new_ratios)
                        optimized_ratios = new_ratios
                if item.get("skip_speed"):
                    skip_speed = True
                if item.get("thinking_result") is not None:
                    thinking_result = item["thinking_result"]
            else:
                yield item

        if result > best_result:
            best_result = result
            best_kv = kv_level

        # Already at native — no point trying lower KV quality
        if best_result >= native_context:
            break

        # f16 reached ≥75% of native context — good enough, skip q8_0
        if kv_level == "f16" and best_result >= F16_KV_PREFER_THRESHOLD:
            yield (
                f"KV=f16: {format_number(best_result)} tokens "
                f"(≥{format_number(F16_KV_PREFER_THRESHOLD)} threshold) — keeping f16"
            )
            break

        if kv_level == "f16":
            ctx_info = (
                format_number(best_result)
                if best_result > 0
                else f"ceiling ≤ min-useful ({format_number(MIN_USEFUL_CONTEXT_TOKENS)})"
            )
            yield f"KV=f16: {ctx_info} — trying q8_0"
        elif kv_level == "q8_0" and best_result < MIN_USEFUL_CONTEXT_TOKENS:
            yield (
                f"KV=q8_0: {format_number(best_result)} tokens "
                f"< {format_number(MIN_USEFUL_CONTEXT_TOKENS)} minimum — "
                f"trying q4_0 (last resort)"
            )
            await wait_for_vram_stable(max_wait_seconds=10.0)

    # GPU-only success with useful context → finish
    # For small-context models: native_context IS the useful threshold
    if best_result >= min(MIN_USEFUL_CONTEXT_TOKENS, native_context):
        # Apply the winning KV level to the cmd for speed calibration and finish
        full_cmd = _inject_kv_quant(full_cmd, best_kv)
        if best_kv != "f16" and config_path:
            update_llamaswap_kv_cache_quant(config_path, model_id, best_kv)
            yield f"KV-Cache quantization set to {best_kv} in llama-swap config"
        elif best_kv == "f16" and config_path:
            # Remove any stale -ctk/-ctv from config
            _remove_llamaswap_kv_cache_quant(config_path, model_id)

        # ── Phase 1b: GPU Minimization ──
        # Try reducing GPU count for the base variant (full context).
        # Fewer GPUs = less memory overhead, simpler configuration.
        if (
            not is_single_gpu
            and gpu_info
            and gpu_info["gpu_count"] > 1
        ):
            total_layers_1b = get_gguf_layer_count(gguf_path)
            if total_layers_1b:
                model_size_mb_1b = get_gguf_total_size(gguf_path) / (1024 ** 2)
                mb_per_layer_1b = model_size_mb_1b / total_layers_1b
                total_gpus_1b = gpu_info["gpu_count"]
                _gpus_sorted_1b = sorted(
                    gpu_info["per_gpu"],
                    key=lambda g: g["total_mb"],
                    reverse=True,
                )
                per_gpu_total_mb_sorted = [g["total_mb"] for g in _gpus_sorted_1b]
                gpu_names_sorted_1b = [
                    _short_gpu_name(g.get("gpu_model", f"GPU{i}"))
                    for i, g in enumerate(_gpus_sorted_1b)
                ]
                min_gpus_1b = _find_min_gpus(
                    model_size_mb_1b, per_gpu_total_mb_sorted,
                )

                if min_gpus_1b < total_gpus_1b:
                    yield (
                        f"Phase 1b: GPU minimization — model "
                        f"({format_number(model_size_mb_1b, 0)} MB) fits on "
                        f"{min_gpus_1b}/{total_gpus_1b} GPUs"
                    )
                    await wait_for_vram_stable(max_wait_seconds=10.0)

                    for n_gpus in range(min_gpus_1b, total_gpus_1b):
                        # Proportional tensor-split for n active GPUs
                        active_vram = per_gpu_total_mb_sorted[:n_gpus]
                        min_vram_1b = min(active_vram)
                        active_ratios_1b = [
                            float(max(1, round(v / min_vram_1b)))
                            for v in active_vram
                        ]
                        sum_active = sum(active_ratios_1b)
                        layers_1b = [
                            round(total_layers_1b * r / sum_active)
                            for r in active_ratios_1b
                        ]
                        layers_1b[-1] = total_layers_1b - sum(layers_1b[:-1])

                        padded_1b = (
                            [float(n) for n in layers_1b]
                            + [0.0] * (total_gpus_1b - n_gpus)
                        )
                        test_cmd = _ensure_tensor_split(full_cmd, padded_1b)

                        split_fmt = _format_layer_split(padded_1b)
                        yield (
                            f"Testing {n_gpus} GPUs: {split_fmt} "
                            f"at ctx={format_number(best_result)}..."
                        )

                        fits, _, test_gpus, _ = await _test_context_physical(
                            test_cmd, best_result, port,
                            safety_margin=safety_margin,
                        )
                        detail = (
                            _format_cuda_detail(test_gpus) if test_gpus else "OOM"
                        )

                        if fits:
                            yield f"✓ {split_fmt} fits ({detail})"

                            # Optimize layers on CUDA0
                            base_cmd_1b = _ensure_tensor_split(
                                full_cmd, padded_1b,
                            )
                            active_names_1b = gpu_names_sorted_1b[:n_gpus]
                            async for opt_result in _optimize_gpu_layers(
                                base_cmd=base_cmd_1b,
                                gguf_path=gguf_path,
                                target_context=best_result,
                                port=port,
                                initial_layers=layers_1b,
                                active_gpu_names=active_names_1b,
                                total_layers=total_layers_1b,
                                total_gpus=total_gpus_1b,
                                min_gpus=n_gpus,
                                mb_per_layer=mb_per_layer_1b,
                                phase_label="1b",
                                safety_margin=safety_margin,
                                per_gpu_total_mb=per_gpu_total_mb_sorted[:n_gpus],
                            ):
                                if isinstance(opt_result, list):
                                    optimized_ratios = opt_result
                                else:
                                    yield opt_result

                            if optimized_ratios:
                                full_cmd = _ensure_tensor_split(
                                    full_cmd, optimized_ratios,
                                )
                            break
                        else:
                            yield f"✗ {split_fmt} doesn't fit ({detail})"

        # Write speed-optimized tensor-split + CUDA_VISIBLE_DEVICES to config
        if optimized_ratios and config_path:
            ts_str = ",".join(f"{r:g}" for r in optimized_ratios)
            if update_llamaswap_tensor_split(config_path, model_id, optimized_ratios):
                yield f"Tensor-split {ts_str} written to llama-swap config"
            # Set CUDA_VISIBLE_DEVICES to only active GPUs (saves ~155 MB/GPU + P8)
            num_active = sum(1 for r in optimized_ratios if r > 0)
            total_gpus_env = gpu_info["gpu_count"] if gpu_info else len(optimized_ratios)
            if update_llamaswap_cuda_visible(config_path, model_id, num_active, total_gpus_env):
                cuda_vis = ",".join(str(i) for i in range(num_active))
                yield f"CUDA_VISIBLE_DEVICES={cuda_vis} written to llama-swap config"

        # Multi-GPU without tensor-split: generate ratios for speed calibration
        if (
            not _has_tensor_split(full_cmd)
            and gpu_info
            and gpu_info["gpu_count"] > 1
            and not is_single_gpu
        ):
            per_gpu = gpu_info["per_gpu"]
            per_gpu_vram = [g["total_mb"] for g in per_gpu]
            min_vram = min(per_gpu_vram)
            auto_ratios = [float(max(1, round(v / min_vram))) for v in per_gpu_vram]
            full_cmd = _ensure_tensor_split(full_cmd, auto_ratios)
            yield "Multi-GPU detected — generated tensor-split for speed calibration"

        if _has_tensor_split(full_cmd):
            if is_single_gpu:
                yield "Phase 2: Skipped (single-GPU, no speed variant needed)"
            elif skip_speed:
                yield (
                    "Phase 2: Speed variant skipped "
                    "(dominant GPU is already the VRAM bottleneck)"
                )
            else:
                yield "Phase 2: Speed variant calibration (min-GPU optimization)"
                await wait_for_vram_stable(max_wait_seconds=10.0)
                # Build per-GPU lists sorted descending by VRAM (FASTEST_FIRST)
                _gpus_sorted = sorted(
                    gpu_info["per_gpu"],
                    key=lambda g: g["total_mb"],
                    reverse=True,
                ) if gpu_info and gpu_info.get("per_gpu") else []
                per_gpu_total_mb = [g["total_mb"] for g in _gpus_sorted]
                cuda_gpu_names = [
                    _short_gpu_name(g.get("gpu_model", f"GPU{i}"))
                    for i, g in enumerate(_gpus_sorted)
                ]
                # Free VRAM for min_gpus (accounts for TTS in VRAM)
                from .gpu_utils import get_all_gpus_memory_info as _get_gpu_info
                _fresh_gpu = _get_gpu_info()
                per_gpu_free_mb = sorted(
                    [g["free_mb"] for g in _fresh_gpu["per_gpu"]],
                    reverse=True,
                ) if _fresh_gpu and _fresh_gpu.get("per_gpu") else None
                speed_target_ctx = min(MIN_USEFUL_CONTEXT_TOKENS, native_context)
                async for msg in _calibrate_speed_split(
                    full_cmd, port, speed_target_ctx,
                    native_context, gguf_path, per_gpu_total_mb,
                    safety_margin=safety_margin,
                    base_kv=best_kv,
                    per_gpu_free_mb=per_gpu_free_mb,
                    cuda_gpu_names=cuda_gpu_names,
                ):
                    yield msg
        async for msg in _finish_calibration(best_result, 99, "gpu", thinking_result=thinking_result):
            yield msg
        return

    # === PHASE 3: Hybrid calibration (GPU-only context insufficient) ===
    min_useful = min(MIN_USEFUL_CONTEXT_TOKENS, native_context)
    yield (
        f"GPU-only context ({format_number(best_result)}) < "
        f"minimum useful ({format_number(min_useful)})"
    )

    total_layers = get_gguf_layer_count(gguf_path)
    if not total_layers:
        yield "Cannot read layer count from GGUF — hybrid mode unavailable"
        yield "__RESULT__:0:0:error"
        return

    yield f"Phase 3: Hybrid calibration (fallback, {total_layers} layers)"

    # Wait for VRAM to stabilize after GPU-only tests
    await wait_for_vram_stable(max_wait_seconds=10.0)

    hybrid_ctx = None
    hybrid_ngl = None
    async for hybrid_msg in _calibrate_hybrid(
        model_id=model_id,
        gguf_path=gguf_path,
        full_cmd=full_cmd,
        native_context=native_context,
        model_size_gb=model_size_gb,
        total_layers=total_layers,
        port=port,
        safety_margin=safety_margin,
    ):
        if hybrid_msg.startswith("__HYBRID__:"):
            parts = hybrid_msg.split(":")
            hybrid_ctx = int(parts[1])
            hybrid_ngl = int(parts[2])
        else:
            yield hybrid_msg

    if hybrid_ctx and hybrid_ngl:
        async for msg in _finish_calibration(hybrid_ctx, hybrid_ngl, "hybrid"):
            yield msg
        return

    yield "Calibration failed: no working configuration found — check llama-server logs"
    yield "__RESULT__:0:0:error"
