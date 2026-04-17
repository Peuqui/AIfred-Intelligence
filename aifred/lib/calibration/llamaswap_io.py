"""llama-swap YAML read/write + cmd-string manipulation.

This is the I/O boundary for the calibration package.  Everything that
touches the YAML file or parses/emits llama-server command strings lives
here, so the calibration algorithm stays pure.

The public ``parse_llamaswap_config`` and ``update_llamaswap_*`` helpers
are consumed by backends, state mixins and config.py — their signatures
must stay stable.
"""

from __future__ import annotations

import copy
import logging
import re
from pathlib import Path
from typing import Any, Dict

import yaml

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# YAML read/write primitives
# ═══════════════════════════════════════════════════════════════════

def _read_yaml(config_path: Path) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_yaml(config_path: Path, config: dict) -> None:
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(
            config, f, default_flow_style=False,
            allow_unicode=True, sort_keys=False,
        )


def _get_cmd(config: dict, model_id: str) -> str | None:
    models = config.get("models") or {}
    entry = models.get(model_id)
    return entry.get("cmd", "") if entry else None


def _set_cmd(config: dict, model_id: str, cmd: str) -> None:
    config["models"][model_id]["cmd"] = cmd


def _ensure_in_group(config: dict, model_id: str, group_name: str = "main") -> None:
    """Add a model to a llama-swap group (creates it if missing)."""
    groups = config.setdefault("groups", {})
    group = groups.setdefault(
        group_name, {"exclusive": True, "swap": True, "members": []},
    )
    members = group.setdefault("members", [])
    if model_id not in members:
        members.append(model_id)


# ═══════════════════════════════════════════════════════════════════
# cmd-string parsers (pure, no IO)
# ═══════════════════════════════════════════════════════════════════

def parse_llamaswap_config(config_path: Path) -> Dict[str, Dict]:
    """Parse llama-swap YAML and extract per-model info.

    Returns a dict ``{model_id: {...}}`` with fields used across aifred:
    gguf_path, llama_server_bin, current_context, ngl, kv_cache_quant,
    reasoning_format, full_cmd, env.
    """
    if not config_path.exists():
        logger.warning(f"llama-swap config not found: {config_path}")
        return {}

    config = _read_yaml(config_path)
    if not config.get("models"):
        return {}

    result: Dict[str, Dict] = {}
    for model_id, entry in config["models"].items():
        cmd: str = entry.get("cmd", "") or ""
        if not cmd:
            continue
        parts: list[str] = cmd.split()
        llama_server_bin = parts[0] if parts else ""

        def _flag_value(flag: str, _parts: list[str] = parts) -> str:
            for i, p in enumerate(_parts):
                if p == flag and i + 1 < len(_parts):
                    return _parts[i + 1]
            return ""

        def _int_flag(flag: str, default: int) -> int:
            val = _flag_value(flag)
            try:
                return int(val) if val else default
            except ValueError:
                return default

        env_list = entry.get("env", []) or []
        env_dict: Dict[str, str] = {}
        for e in env_list:
            if isinstance(e, str) and "=" in e:
                k, v = e.split("=", 1)
                env_dict[k.strip()] = v.strip()

        result[model_id] = {
            "gguf_path": _flag_value("--model"),
            "llama_server_bin": llama_server_bin,
            "current_context": _int_flag("-c", 0),
            "ngl": _int_flag("-ngl", 99),
            "kv_cache_quant": _flag_value("-ctk"),
            "reasoning_format": _flag_value("--reasoning-format"),
            "full_cmd": cmd,
            "env": env_dict,
        }
    return result


def parse_sampling_from_cmd(cmd: str) -> Dict[str, float]:
    """Extract sampling parameters (--temp, --top-k, etc.) from a cmd string."""
    flag_map = {
        "--temp": "temperature",
        "--top-k": "top_k",
        "--top-p": "top_p",
        "--min-p": "min_p",
        "--repeat-penalty": "repeat_penalty",
    }
    parts = cmd.split()
    out: Dict[str, float] = {}
    for i, p in enumerate(parts):
        if p in flag_map and i + 1 < len(parts):
            try:
                out[flag_map[p]] = float(parts[i + 1])
            except ValueError:
                pass
    return out


def parse_tensor_split(cmd: str) -> list[float]:
    """Extract tensor-split ratios from a cmd string (``[]`` if absent)."""
    match = re.search(r"(?:--tensor-split|-ts)\s+([\d.,]+)", cmd)
    if not match:
        return []
    return [float(v) for v in match.group(1).split(",") if v]


def has_tensor_split(cmd: str) -> bool:
    return bool(re.search(r"(--tensor-split|-ts)\s+[\d.,]+", cmd))


# ═══════════════════════════════════════════════════════════════════
# cmd-string mutators (pure, no IO)
# ═══════════════════════════════════════════════════════════════════

def set_context(cmd: str, ctx: int) -> str:
    return re.sub(r"-c\s+\d+", f"-c {ctx}", cmd)


def set_ngl(cmd: str, ngl: int) -> str:
    return re.sub(r"-ngl\s+\d+", f"-ngl {ngl}", cmd)


def set_tensor_split(cmd: str, ratios: list[float] | tuple[float, ...]) -> str:
    """Replace or insert --tensor-split in the cmd.

    When inserting (no tensor-split present), also injects ``-sm layer``
    and ``-fit off`` — both required for deterministic multi-GPU splits.
    """
    new_val = ",".join(f"{r:g}" for r in ratios)
    if has_tensor_split(cmd):
        cmd = re.sub(r"(--tensor-split\s+)[\d.,]+", rf"\g<1>{new_val}", cmd)
        cmd = re.sub(r"(-ts\s+)[\d.,]+", rf"\g<1>{new_val}", cmd)
        return cmd
    return cmd.replace(
        " --port", f" -sm layer --tensor-split {new_val} -fit off --port",
    )


def set_kv_quant(cmd: str, kv_quant: str) -> str:
    """Set -ctk/-ctv; ``f16``/empty removes the flags (restore default)."""
    if not kv_quant or kv_quant == "f16":
        cmd = re.sub(r"\s*-ctk\s+\S+", "", cmd)
        cmd = re.sub(r"\s*-ctv\s+\S+", "", cmd)
        return cmd
    if "-ctk " in cmd:
        cmd = re.sub(r"-ctk\s+\S+", f"-ctk {kv_quant}", cmd)
    else:
        cmd = cmd.replace(" --port", f" -ctk {kv_quant} --port")
    if "-ctv " in cmd:
        cmd = re.sub(r"-ctv\s+\S+", f"-ctv {kv_quant}", cmd)
    else:
        cmd = cmd.replace(" --port", f" -ctv {kv_quant} --port")
    return cmd


# ═══════════════════════════════════════════════════════════════════
# Public YAML mutators (consumed by backends + state mixins)
# ═══════════════════════════════════════════════════════════════════

def _update_cmd(
    config_path: Path, model_id: str, transform, log_label: str,
) -> bool:
    """Generic helper: read YAML, transform cmd, write back if changed."""
    if not config_path.exists():
        logger.error(f"llama-swap config not found: {config_path}")
        return False
    config = _read_yaml(config_path)
    cmd = _get_cmd(config, model_id)
    if cmd is None:
        logger.error(f"Model {model_id} not found in llama-swap config")
        return False
    new_cmd = transform(cmd)
    if new_cmd == cmd:
        return True  # no-op success
    _set_cmd(config, model_id, new_cmd)
    _write_yaml(config_path, config)
    logger.info(f"Updated llama-swap config: {model_id} → {log_label}")
    return True


def update_llamaswap_context(config_path: Path, model_id: str, ctx: int) -> bool:
    return _update_cmd(
        config_path, model_id,
        lambda c: set_context(c, ctx), f"-c {ctx}",
    )


def update_llamaswap_ngl(config_path: Path, model_id: str, ngl: int) -> bool:
    return _update_cmd(
        config_path, model_id,
        lambda c: set_ngl(c, ngl), f"-ngl {ngl}",
    )


def update_llamaswap_tensor_split(
    config_path: Path, model_id: str, ratios: list[float],
) -> bool:
    """Write tensor-split — trims trailing zeros (inactive GPUs)."""
    trimmed = list(ratios)
    while len(trimmed) > 1 and trimmed[-1] == 0:
        trimmed.pop()
    return _update_cmd(
        config_path, model_id,
        lambda c: set_tensor_split(c, trimmed),
        f"tensor-split {trimmed}",
    )


def update_llamaswap_kv_cache_quant(
    config_path: Path, model_id: str, kv_quant: str,
) -> bool:
    return _update_cmd(
        config_path, model_id,
        lambda c: set_kv_quant(c, kv_quant),
        f"KV cache {kv_quant}",
    )


def remove_llamaswap_kv_cache_quant(
    config_path: Path, model_id: str,
) -> bool:
    return update_llamaswap_kv_cache_quant(config_path, model_id, "f16")


def update_llamaswap_reasoning_format(
    config_path: Path, model_id: str, fmt: str = "deepseek",
) -> bool:
    """Ensure ``--reasoning-format <fmt>`` is present after --jinja."""
    def transform(cmd: str) -> str:
        if "--reasoning-format " in cmd:
            if f"--reasoning-format {fmt}" in cmd:
                return cmd
            return re.sub(
                r"--reasoning-format\s+\S+", f"--reasoning-format {fmt}", cmd,
            )
        return cmd.replace(" --jinja", f" --jinja --reasoning-format {fmt}")
    return _update_cmd(
        config_path, model_id, transform, f"--reasoning-format {fmt}",
    )


def update_llamaswap_cuda_visible(
    config_path: Path, model_id: str,
    num_active_gpus: int, total_gpus: int,
) -> bool:
    """Set (or remove) CUDA_VISIBLE_DEVICES; trims matching tensor-split."""
    if not config_path.exists():
        return False
    config = _read_yaml(config_path)
    models = config.get("models", {})
    entry = models.get(model_id)
    if not entry:
        logger.error(f"Model {model_id} not found in llama-swap config")
        return False

    cmd = entry.get("cmd", "")
    if num_active_gpus < total_gpus:
        ts_match = re.search(r"(--tensor-split|-ts)\s+([\d.,]+)", cmd)
        if ts_match:
            ts_vals = [v for v in ts_match.group(2).split(",") if v]
            while len(ts_vals) > 1 and ts_vals[-1] in ("0", "0.0"):
                ts_vals.pop()
            trimmed = ",".join(ts_vals)
            cmd = cmd[: ts_match.start(2)] + trimmed + cmd[ts_match.end(2):]
            entry["cmd"] = cmd

    if num_active_gpus >= total_gpus:
        entry.pop("env", None)
        logger.info(f"Removed CUDA_VISIBLE_DEVICES for {model_id}")
    else:
        cuda_vis = ",".join(str(i) for i in range(num_active_gpus))
        entry["env"] = [f"CUDA_VISIBLE_DEVICES={cuda_vis}"]
        logger.info(f"Set CUDA_VISIBLE_DEVICES={cuda_vis} for {model_id}")

    _write_yaml(config_path, config)
    return True


# ═══════════════════════════════════════════════════════════════════
# Variant creation (speed, tts-xtts, tts-moss)
# ═══════════════════════════════════════════════════════════════════

def _copy_entry(config: dict, source_id: str, target_id: str) -> dict | None:
    models = config.get("models", {})
    source = models.get(source_id)
    if not source:
        return None
    copied: dict = copy.deepcopy(source)
    return copied


def _insert_variant(
    config: dict, base_id: str, variant_id: str, entry: dict,
) -> None:
    """Insert (or replace) variant right after the base model in YAML order."""
    models = config["models"]
    if variant_id in models:
        models[variant_id] = entry
        return
    new_models: dict[str, Any] = {}
    for key, val in models.items():
        new_models[key] = val
        if key == base_id:
            new_models[variant_id] = entry
    config["models"] = new_models


def add_llamaswap_speed_variant(
    config_path: Path,
    model_id: str,
    speed_split_cuda0: int,         # legacy, kept for signature compat
    speed_split_rest: int,          # legacy, kept for signature compat
    speed_context: int,
    num_gpus: int = 0,
    kv_quant: str = "f16",
    speed_layer_split: str = "",
) -> bool:
    """Create the ``<model>-speed`` entry in llama-swap YAML.

    Prefers ``speed_layer_split`` (full A:B:C:D string) when provided;
    falls back to existing tensor-split for backward compatibility.
    """
    if not config_path.exists():
        logger.error(f"llama-swap config not found: {config_path}")
        return False
    config = _read_yaml(config_path)
    speed_id = f"{model_id}-speed"
    entry = _copy_entry(config, model_id, speed_id)
    if entry is None:
        logger.error(f"Model {model_id} not found in llama-swap config")
        return False

    cmd = entry.get("cmd", "")
    original_ratios = parse_tensor_split(cmd)

    if speed_layer_split:
        speed_ratios = [float(x) for x in speed_layer_split.split(":")]
    else:
        speed_ratios = list(original_ratios) if original_ratios else [1.0]

    while len(speed_ratios) > 1 and speed_ratios[-1] == 0:
        speed_ratios.pop()

    cmd = set_tensor_split(cmd, speed_ratios)
    cmd = set_context(cmd, speed_context)
    cmd = set_kv_quant(cmd, kv_quant)
    entry["cmd"] = cmd

    if num_gpus > 0 and num_gpus < len(original_ratios):
        cuda_vis = ",".join(str(i) for i in range(num_gpus))
        entry["env"] = [f"CUDA_VISIBLE_DEVICES={cuda_vis}"]

    existed = speed_id in config["models"]
    _insert_variant(config, model_id, speed_id, entry)
    _ensure_in_group(config, speed_id)
    _write_yaml(config_path, config)
    logger.info(f"{'Updated' if existed else 'Added'} speed variant: {speed_id}")
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
    """Create the ``<model>-tts-<backend>`` entry in llama-swap YAML.

    ``cuda_visible_devices`` (explicit) wins over ``num_gpus`` (derived).
    ``source_model_id`` lets isolated-mode inherit the speed variant.
    """
    if not config_path.exists():
        logger.error(f"llama-swap config not found: {config_path}")
        return False
    config = _read_yaml(config_path)
    tts_id = f"{model_id}-tts-{tts_backend}"
    src_id = source_model_id or model_id
    entry = _copy_entry(config, src_id, tts_id)
    if entry is None:
        logger.error(f"Source model {src_id} not found in llama-swap config")
        return False

    cmd = entry.get("cmd", "")
    cmd = set_context(cmd, tts_context)
    cmd = set_kv_quant(cmd, kv_quant)
    if tensor_split:
        cmd = re.sub(
            r"(--tensor-split|-ts)\s+[\d.,]+", f"-ts {tensor_split}", cmd,
        )
    entry["cmd"] = cmd

    if cuda_visible_devices:
        entry["env"] = [f"CUDA_VISIBLE_DEVICES={cuda_visible_devices}"]
    elif num_gpus > 0:
        cuda_vis = ",".join(str(i) for i in range(num_gpus))
        entry["env"] = [f"CUDA_VISIBLE_DEVICES={cuda_vis}"]

    existed = tts_id in config["models"]
    _insert_variant(config, model_id, tts_id, entry)
    _ensure_in_group(config, tts_id)
    _write_yaml(config_path, config)
    logger.info(f"{'Updated' if existed else 'Added'} TTS variant: {tts_id}")
    return True
