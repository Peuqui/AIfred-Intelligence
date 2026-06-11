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


def _extract_uuids_from_env(env_lines: list) -> list[str]:
    """Pull the CUDA_VISIBLE_DEVICES UUID list out of an env list.

    Returns ``[]`` when the env entry is missing, or when its value
    looks like numeric indices instead of GPU UUIDs (legacy state).
    """
    for line in env_lines or []:
        if not isinstance(line, str) or not line.startswith("CUDA_VISIBLE_DEVICES="):
            continue
        val = line.split("=", 1)[1].strip()
        if not val:
            return []
        parts = [p.strip() for p in val.split(",") if p.strip()]
        # Only accept actual UUIDs (start with "GPU-"); reject legacy
        # numeric indices to force a re-calibration if the config is stale.
        if all(p.startswith("GPU-") for p in parts):
            return parts
        return []
    return []


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


def set_mmproj_path(cmd: str, mmproj_path: str) -> str:
    """Set or remove the ``--mmproj`` (native vision encoder) flag.

    An empty ``mmproj_path`` removes the flag (model runs text-only). A path
    replaces any existing ``--mmproj`` or inserts it before ``--port``. The
    path is assumed clean (no whitespace) like all model/gguf paths here.
    """
    cmd = re.sub(r"\s*--mmproj\s+\S+", "", cmd)
    if not mmproj_path:
        return cmd
    return cmd.replace(" --port", f" --mmproj {mmproj_path} --port", 1)


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


def update_llamaswap_mmproj(config_path: Path, model_id: str, mmproj_path: str) -> bool:
    """Add/replace/remove the ``--mmproj`` flag on a model's cmd (persistent)."""
    label = f"--mmproj {mmproj_path}" if mmproj_path else "remove --mmproj"
    return _update_cmd(
        config_path, model_id,
        lambda c: set_mmproj_path(c, mmproj_path), label,
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
    active_uuids: list[str], all_uuids: list[str],
) -> bool:
    """Pin env + tensor-split to a specific subset of GPUs by UUID.

    ``active_uuids`` is the ordered list of NVIDIA GPU UUIDs that should
    receive layers. The order matters: it becomes the CUDA enumeration
    order seen by llama-server (CUDA0 = active_uuids[0], etc.), so
    tensor-split positions map 1:1.

    ``all_uuids`` is the full system inventory at calibration time,
    only used to detect "all GPUs active" — in which case
    CUDA_VISIBLE_DEVICES is still written explicitly with the UUIDs in
    AIfred's preferred order, so llama-server's enumeration matches
    calibration's regardless of NVIDIA's CUDA_DEVICE_ORDER default.

    UUIDs are hardware-bound, so this mapping survives slot moves,
    driver updates and enumeration-heuristic changes.

    Tensor-split is rewritten to one value per active GPU, in
    active_uuids order.
    """
    if not config_path.exists():
        return False
    config = _read_yaml(config_path)
    models = config.get("models", {})
    entry = models.get(model_id)
    if not entry:
        logger.error(f"Model {model_id} not found in llama-swap config")
        return False
    if not active_uuids:
        logger.error(f"update_llamaswap_cuda_visible called with empty active_uuids for {model_id}")
        return False

    cmd = entry.get("cmd", "")

    # Rewrite tensor-split. If the cmd already has a tensor-split, take
    # its values aligned to all_uuids and re-emit only the slots that
    # correspond to active_uuids (in active_uuids order). This handles
    # the case where the tensor-split was originally written for the
    # full GPU set.
    ts_match = re.search(r"(--tensor-split|-ts)\s+([\d.,]+)", cmd)
    if ts_match:
        raw_vals = [v for v in ts_match.group(2).split(",") if v]
        padded = raw_vals + ["0"] * max(0, len(all_uuids) - len(raw_vals))
        # Index lookup: for each active uuid, find its position in
        # all_uuids and grab the corresponding split value.
        uuid_to_pos = {u: i for i, u in enumerate(all_uuids)}
        new_vals: list[str] = []
        for u in active_uuids:
            pos = uuid_to_pos.get(u, -1)
            new_vals.append(padded[pos] if 0 <= pos < len(padded) else "0")
        trimmed = ",".join(new_vals)
        cmd = cmd[: ts_match.start(2)] + trimmed + cmd[ts_match.end(2):]
        entry["cmd"] = cmd

    # Always write CUDA_VISIBLE_DEVICES with UUIDs in AIfred's order.
    # Even when all GPUs are active, this enforces deterministic
    # enumeration regardless of NVIDIA's default CUDA_DEVICE_ORDER
    # heuristic — no FASTEST_FIRST/PCI_BUS_ID lottery.
    cuda_vis = ",".join(active_uuids)
    entry["env"] = [f"CUDA_VISIBLE_DEVICES={cuda_vis}"]
    if len(active_uuids) < len(all_uuids):
        logger.info(
            f"Pinned {model_id} to {len(active_uuids)} GPU(s) by UUID "
            f"(subset of {len(all_uuids)})"
        )
    else:
        logger.info(
            f"Pinned {model_id} to all {len(all_uuids)} GPUs in "
            f"AIfred's compute-DESC order"
        )

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

    # Inherit env from base (already contains CUDA_VISIBLE_DEVICES with
    # UUIDs in AIfred's compute-DESC order). The speed variant uses the
    # same physical GPUs — just fewer of them — so we trim the UUID list
    # to the first ``num_gpus`` entries (compute-fastest first within
    # the base set).
    base_entry = (config.get("models") or {}).get(model_id) or {}
    base_env = base_entry.get("env") or []
    base_uuids = _extract_uuids_from_env(base_env)
    if num_gpus > 0 and base_uuids and num_gpus < len(base_uuids):
        speed_uuids = base_uuids[:num_gpus]
        entry["env"] = [f"CUDA_VISIBLE_DEVICES={','.join(speed_uuids)}"]
    else:
        # Same GPU count as base (or no base UUIDs) — keep base env.
        entry["env"] = list(base_env)

    existed = speed_id in config["models"]
    _insert_variant(config, model_id, speed_id, entry)
    _ensure_in_group(config, speed_id)
    _write_yaml(config_path, config)
    logger.info(f"{'Updated' if existed else 'Added'} speed variant: {speed_id}")
    return True


def has_llamaswap_base(config_path: Path, model_id: str) -> bool:
    """True if a base entry for ``model_id`` exists in llama-swap.yaml with
    a usable context (``-c`` > 0). Used by the calibration picker to show
    a "already calibrated" indicator without re-running the measurement.
    """
    if not config_path.exists():
        return False
    config = _read_yaml(config_path)
    entry = (config.get("models") or {}).get(model_id) or {}
    cmd = entry.get("cmd", "") or ""
    if not cmd:
        return False
    for i, p in enumerate(cmd.split()):
        if p == "-c" and i + 1 < len(cmd.split()):
            try:
                return int(cmd.split()[i + 1]) > 0
            except ValueError:
                return False
    return False


def has_llamaswap_tts_variant(
    config_path: Path, model_id: str, tts_backend: str,
    require_speed: bool = False,
) -> bool:
    """True if a TTS variant exists in llama-swap.yaml for this model.

    ``require_speed=False`` (default): accepts either ``-tts-<backend>``
    or ``-tts-<backend>-speed`` — the engine is usable in *some* mode.
    Used by the calibration picker / UI dropdown gating where the
    granularity "has any TTS profile for this model" is enough.

    ``require_speed=True``: only the ``-tts-<backend>-speed`` entry
    counts. Used when the user has Speed-mode ON and we need to know
    whether a Speed-flavoured TTS profile actually exists.
    """
    if not config_path.exists():
        return False
    models = (_read_yaml(config_path).get("models") or {})
    if require_speed:
        return f"{model_id}-tts-{tts_backend}-speed" in models
    return (
        f"{model_id}-tts-{tts_backend}" in models
        or f"{model_id}-tts-{tts_backend}-speed" in models
    )


def resolve_variant_suffix(
    config_path: Path,
    base_id: str,
    *,
    speed_on: bool,
    has_speed_variant: bool,
    tts_active: bool,
    tts_engine: str = "",
    gpu_tts_engines: set[str] | None = None,
    vlm_active: bool = False,
    vlm_key: str = "",
) -> str:
    """Resolve the variant suffix for ``base_id`` given the current toggles.

    Single source of truth for "which profile do we send to llama-swap".
    All four resolver call sites (``_effective_model_id`` for agents,
    the Automatik resolver in ``_backend_mixin``, the compression-ctx
    lookup in ``context_utils``, and the TTS pre-check gate in
    ``_chat_mixin``) delegate here so that the fallback rules can only
    diverge in one place.

    Fallback order (first hit wins) — VLM combos are tried first when
    ``vlm_active`` is true so a user with both VLM and TTS enabled
    actually lands on the combined variant; if that variant does not
    exist (e.g. user calibrated TTS but never the VLM × TTS combo) the
    resolver gracefully degrades to the TTS-only variant (or further).
    For each VLM/TTS tier the Speed flavour is preferred when ``speed_on``
    so the Speed toggle stays effective even while VLM is active:

    1. ``vlm AND tts AND speed AND <base>-tts-<engine>-vlm-<key>-speed exists``
    2. ``vlm AND tts AND <base>-tts-<engine>-vlm-<key> exists``
    3. ``vlm AND speed AND <base>-vlm-<key>-speed exists``
    4. ``vlm AND <base>-vlm-<key> exists``
    5. ``speed AND tts AND <base>-tts-<engine>-speed exists``
    6. ``tts AND <base>-tts-<engine> exists``
    7. ``speed AND has_speed_variant`` → ``-speed``
    8. otherwise → ``""`` (use the base id unchanged).

    The Speed flavour of a VLM tier only wins if the calibration writer
    actually produced that profile (``in models`` check); otherwise the
    resolver gracefully degrades to the non-speed VLM variant, so an
    un-calibrated combo never breaks resolution.

    ``gpu_tts_engines``: set of engine keys that need the TTS variant
    profile (i.e. share GPU VRAM with the LLM). Engines outside this set
    (Edge / Piper / eSpeak / DashScope) don't have a TTS variant — the
    resolver falls through to the Speed-only or base rule for them.
    """
    if not base_id:
        return ""

    # Read the YAML once; subsequent ``has_llamaswap_tts_variant`` calls
    # would re-read it on every check.
    if not config_path.exists():
        # Without the config file we can't claim any variant exists,
        # so the only knob we can honor is Speed (which lives in state).
        if speed_on and has_speed_variant:
            return "-speed"
        return ""
    models = (_read_yaml(config_path).get("models") or {})

    needs_tts_variant = (
        tts_active
        and bool(tts_engine)
        and (gpu_tts_engines is None or tts_engine in gpu_tts_engines)
    )
    needs_vlm_variant = vlm_active and bool(vlm_key)

    # Rule 1: TTS × VLM × Speed — all three active and the combo exists.
    if needs_vlm_variant and needs_tts_variant and speed_on:
        combo_speed = f"{base_id}-tts-{tts_engine}-vlm-{vlm_key}-speed"
        if combo_speed in models:
            return f"-tts-{tts_engine}-vlm-{vlm_key}-speed"

    # Rule 2: TTS × VLM combo — both active and the combo variant exists.
    if needs_vlm_variant and needs_tts_variant:
        combo_id = f"{base_id}-tts-{tts_engine}-vlm-{vlm_key}"
        if combo_id in models:
            return f"-tts-{tts_engine}-vlm-{vlm_key}"

    # Rule 3: VLM × Speed — VLM active, Speed on, speed flavour exists.
    if needs_vlm_variant and speed_on:
        vlm_speed_id = f"{base_id}-vlm-{vlm_key}-speed"
        if vlm_speed_id in models:
            return f"-vlm-{vlm_key}-speed"

    # Rule 4: VLM only — VLM active, no TTS combo to honor.
    if needs_vlm_variant:
        vlm_id = f"{base_id}-vlm-{vlm_key}"
        if vlm_id in models:
            return f"-vlm-{vlm_key}"

    # Rule 5: Speed + TTS, both flavours present.
    if needs_tts_variant and speed_on:
        sp_id = f"{base_id}-tts-{tts_engine}-speed"
        if sp_id in models:
            return f"-tts-{tts_engine}-speed"

    # Rule 6: TTS without Speed (or Speed flavour missing for this combo).
    if needs_tts_variant:
        base_tts_id = f"{base_id}-tts-{tts_engine}"
        if base_tts_id in models:
            return f"-tts-{tts_engine}"

    # Rule 7: Speed only.
    if speed_on and has_speed_variant:
        return "-speed"

    # Rule 8: bare base.
    return ""


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
        # llama.cpp's `--tensor-split` wants COMMA-separated values. Some
        # callers pass the colon-form used internally for log/sentinel
        # formatting — normalize here so the YAML always lands with the
        # format llama.cpp can parse. Use the long ``--tensor-split``
        # form to stay consistent with the BASE writer (the short ``-ts``
        # and the long form are functionally identical, but mixing both
        # in the same YAML makes diffs and grep harder).
        ts_normalized = tensor_split.replace(":", ",")
        cmd = re.sub(
            r"(--tensor-split|-ts)\s+[\d.,:]+",
            f"--tensor-split {ts_normalized}", cmd,
        )
    entry["cmd"] = cmd

    # Always pin via UUID list. Caller passes ``cuda_visible_devices``
    # as a comma-separated UUID string (the same value that base used,
    # if isolated-mode reuses base; otherwise a tightened subset).
    if cuda_visible_devices:
        entry["env"] = [f"CUDA_VISIBLE_DEVICES={cuda_visible_devices}"]
    else:
        # Inherit env from source (already UUID-pinned).
        src_entry = (config.get("models") or {}).get(src_id) or {}
        src_env = src_entry.get("env") or []
        src_uuids = _extract_uuids_from_env(src_env)
        if num_gpus > 0 and src_uuids and num_gpus < len(src_uuids):
            entry["env"] = [
                f"CUDA_VISIBLE_DEVICES={','.join(src_uuids[:num_gpus])}",
            ]
        else:
            entry["env"] = list(src_env)

    existed = tts_id in config["models"]
    _insert_variant(config, model_id, tts_id, entry)
    _ensure_in_group(config, tts_id)
    _write_yaml(config_path, config)
    logger.info(f"{'Updated' if existed else 'Added'} TTS variant: {tts_id}")
    return True


def remove_llamaswap_tts_variant(
    config_path: Path, model_id: str, tts_backend: str,
) -> bool:
    """Remove the ``<model>-tts-<backend>`` entry from llama-swap YAML —
    both the model definition and its group membership.

    Called when a TTS-variant calibration fails: a stale variant left
    over from an earlier (successful or buggy) run must not survive, or
    llama-swap will happily load a profile that no longer fits the GPU
    layout and OOM. Returns True if an entry was actually removed."""
    if not config_path.exists():
        return False
    config = _read_yaml(config_path)
    tts_id = f"{model_id}-tts-{tts_backend}"
    removed = False
    models = config.get("models") or {}
    if tts_id in models:
        del models[tts_id]
        removed = True
    for group in (config.get("groups") or {}).values():
        members = group.get("members") or []
        if tts_id in members:
            members.remove(tts_id)
            removed = True
    if removed:
        _write_yaml(config_path, config)
        logger.info(f"Removed stale TTS variant: {tts_id}")
    return removed


def diagnose_uncalibrated_combo(
    config_path: Path,
    base_id: str,
    *,
    tts_active: bool,
    tts_engine: str = "",
    gpu_tts_engines: set[str] | None = None,
    vlm_active: bool = False,
    vlm_key: str = "",
) -> str | None:
    """Return a human-readable warning when the user's current toggle
    state asks for a YAML variant that doesn't exist.

    Counterpart to :func:`resolve_variant_suffix` — the resolver returns
    *what gets loaded*, this function returns *what should have been
    loaded but isn't*. Returns ``None`` when everything the user wants
    is actually calibrated.

    Useful at chat-submit time so the user sees a debug-console message
    like "⚠️ TTS+VLM combo for X+Y is not calibrated, falling back to
    Z — VLM may OOM on next inference" instead of silently picking the
    wrong profile.
    """
    if not base_id or not config_path.exists():
        return None

    needs_tts = (
        tts_active
        and bool(tts_engine)
        and (gpu_tts_engines is None or tts_engine in gpu_tts_engines)
    )
    needs_vlm = vlm_active and bool(vlm_key)
    if not needs_tts and not needs_vlm:
        return None

    models = (_read_yaml(config_path).get("models") or {})

    if needs_tts and needs_vlm:
        ideal = f"{base_id}-tts-{tts_engine}-vlm-{vlm_key}"
        if ideal in models:
            return None
        # Pick the best fallback the resolver would land on
        fallbacks = [
            f"{base_id}-vlm-{vlm_key}",
            f"{base_id}-tts-{tts_engine}",
            base_id,
        ]
        actual = next((f for f in fallbacks if f in models), base_id)
        return (
            f"⚠️ Combo profile {ideal} is not calibrated — runtime falls "
            f"back to {actual}. Open the calibration matrix and tick "
            f"the {tts_engine} × {vlm_key} cell to fix this."
        )

    if needs_vlm:
        ideal = f"{base_id}-vlm-{vlm_key}"
        if ideal in models:
            return None
        return (
            f"⚠️ VLM profile {ideal} is not calibrated — runtime falls "
            f"back to {base_id}. Tick the {vlm_key} / No-TTS cell in the "
            f"calibration matrix to fix this."
        )

    # needs_tts only
    ideal = f"{base_id}-tts-{tts_engine}"
    if ideal in models:
        return None
    return (
        f"⚠️ TTS profile {ideal} is not calibrated — runtime falls back "
        f"to {base_id}. Tick the No-VLM / {tts_engine} cell in the "
        f"calibration matrix to fix this."
    )


def add_llamaswap_vlm_variant(
    config_path: Path,
    model_id: str,
    vlm_context: int,
    vlm_key: str,
    kv_quant: str = "f16",
    tensor_split: str = "",
    num_gpus: int = 0,
    cuda_visible_devices: str = "",
    source_model_id: str | None = None,
    tts_backend: str | None = None,
    speed: bool = False,
) -> bool:
    """Create the ``<model>-vlm-<key>`` (or
    ``<model>-tts-<backend>-vlm-<key>``) entry in llama-swap YAML.

    With ``speed=True`` the target id gains a trailing ``-speed`` (i.e.
    ``<model>-vlm-<key>-speed`` / ``<model>-tts-<backend>-vlm-<key>-speed``)
    — the fewer-GPU Speed flavour of the VLM variant, picked by the
    resolver when the user has both Speed and VLM active.

    Direct sibling of :func:`add_llamaswap_tts_variant` — same copy / set /
    insert / group-add sequence, only the target id and the source id
    differ. ``cuda_visible_devices`` (explicit) wins over ``num_gpus``
    (derived). ``source_model_id`` lets isolated-mode inherit a parent
    variant (typically BASE or the matching ``-speed`` variant). When
    ``tts_backend`` is given, the target id becomes
    ``<model>-tts-<backend>-vlm-<key>`` — used for the TTS×VLM combo
    variants.
    """
    if not config_path.exists():
        logger.error(f"llama-swap config not found: {config_path}")
        return False
    config = _read_yaml(config_path)
    if tts_backend:
        target_id = f"{model_id}-tts-{tts_backend}-vlm-{vlm_key}"
    else:
        target_id = f"{model_id}-vlm-{vlm_key}"
    if speed:
        target_id += "-speed"
    src_id = source_model_id or model_id
    entry = _copy_entry(config, src_id, target_id)
    if entry is None:
        logger.error(f"Source model {src_id} not found in llama-swap config")
        return False

    cmd = entry.get("cmd", "")
    cmd = set_context(cmd, vlm_context)
    cmd = set_kv_quant(cmd, kv_quant)
    if tensor_split:
        # Same rationale as the TTS writer: long-form ``--tensor-split``
        # keeps the YAML consistent with the BASE writer.
        ts_normalized = tensor_split.replace(":", ",")
        cmd = re.sub(
            r"(--tensor-split|-ts)\s+[\d.,:]+",
            f"--tensor-split {ts_normalized}", cmd,
        )
    entry["cmd"] = cmd

    if cuda_visible_devices:
        entry["env"] = [f"CUDA_VISIBLE_DEVICES={cuda_visible_devices}"]
    else:
        src_entry = (config.get("models") or {}).get(src_id) or {}
        src_env = src_entry.get("env") or []
        src_uuids = _extract_uuids_from_env(src_env)
        if num_gpus > 0 and src_uuids and num_gpus < len(src_uuids):
            entry["env"] = [
                f"CUDA_VISIBLE_DEVICES={','.join(src_uuids[:num_gpus])}",
            ]
        else:
            entry["env"] = list(src_env)

    existed = target_id in config["models"]
    _insert_variant(config, model_id, target_id, entry)
    _ensure_in_group(config, target_id)
    _write_yaml(config_path, config)
    logger.info(f"{'Updated' if existed else 'Added'} VLM variant: {target_id}")
    return True


def remove_llamaswap_vlm_variant(
    config_path: Path, model_id: str, vlm_key: str,
    tts_backend: str | None = None,
    speed: bool = False,
) -> bool:
    """Remove a VLM variant from llama-swap YAML — mirror of
    :func:`remove_llamaswap_tts_variant`. Removes both the model entry
    and the group membership. ``speed=True`` targets the ``-speed`` flavour."""
    if not config_path.exists():
        return False
    config = _read_yaml(config_path)
    if tts_backend:
        target_id = f"{model_id}-tts-{tts_backend}-vlm-{vlm_key}"
    else:
        target_id = f"{model_id}-vlm-{vlm_key}"
    if speed:
        target_id += "-speed"
    removed = False
    models = config.get("models") or {}
    if target_id in models:
        del models[target_id]
        removed = True
    for group in (config.get("groups") or {}).values():
        members = group.get("members") or []
        if target_id in members:
            members.remove(target_id)
            removed = True
    if removed:
        _write_yaml(config_path, config)
        logger.info(f"Removed stale VLM variant: {target_id}")
    return removed
