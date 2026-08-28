"""
Static operating points for llama-swap model entries.

An operating point is a fully measured, hand- or calibration-produced
llama-swap model entry (cmd/env/ttl/cmdStop) plus free-form metadata.
Models that have one are NOT calibrated — the entry is adopted 1:1 into
the llama-swap config. Primary use case: vLLM deployments whose search
space (TP×PP topology, spec depth, capture sizes, env switches) the
calibration cannot explore yet; the file format doubles as the
persistence target for future vLLM auto-calibration results.

File layout: ``OPERATING_POINTS_DIR/<entry-name>.yaml`` with

    llamaswap:        # the literal llama-swap model entry
      cmd: ...
      cmdStop: ...    # optional
      ttl: 3600       # optional
      env: [...]      # optional
    group: main       # optional, default "main"
    meta: {...}       # free-form (source, date, reference throughput)

The entry name is the filename stem — it must match the llama-swap model
id AND the ``--served-model-name`` inside cmd (vLLM rejects mismatches).
"""

import logging
import shlex
import subprocess
from pathlib import Path

import yaml

from .config import LLAMASWAP_CONFIG_PATH, OPERATING_POINTS_DIR
from .calibration.llamaswap_io import _ensure_in_group, _read_yaml, _write_yaml

logger = logging.getLogger(__name__)


def gpu_fingerprint() -> str:
    """Hardware fingerprint in the autoscan header format
    (``RTX_8000:49152,...``, PCI order). Operating points are bound to
    the exact GPU set they were measured on."""
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=10, check=True,
        env={"PATH": "/usr/bin:/usr/local/bin:/bin", "CUDA_DEVICE_ORDER": "PCI_BUS_ID"},
    ).stdout
    parts = []
    for line in out.strip().splitlines():
        name, total = line.split(", ")
        for prefix in ("NVIDIA ", "Quadro ", "Tesla "):
            name = name.removeprefix(prefix)
        parts.append(f"{name.replace(' ', '_')}:{total}")
    return ",".join(parts)


def _checkpoint_path_from_cmd(cmd: str) -> Path | None:
    """The --model path from a vLLM cmd (None if the cmd has none)."""
    tokens = shlex.split(cmd)
    for i, tok in enumerate(tokens):
        if tok == "--model" and i + 1 < len(tokens):
            return Path(tokens[i + 1])
    return None


def list_operating_points() -> dict[str, Path]:
    """All available operating-point profiles, keyed by entry name."""
    if not OPERATING_POINTS_DIR.exists():
        return {}
    return {p.stem: p for p in sorted(OPERATING_POINTS_DIR.glob("*.yaml"))}


def get_operating_point(model_id: str) -> dict | None:
    """Load the profile for ``model_id``; None if there is none.

    Raises ValueError for a profile that exists but is structurally
    invalid — a broken profile must surface, not silently degrade into
    a calibration run.
    """
    path = OPERATING_POINTS_DIR / f"{model_id}.yaml"
    if not path.exists():
        return None
    profile = yaml.safe_load(path.read_text())
    if not isinstance(profile, dict) or "llamaswap" not in profile:
        raise ValueError(f"Operating point {path} has no 'llamaswap' section")
    entry = profile["llamaswap"]
    if not isinstance(entry, dict) or not entry.get("cmd"):
        raise ValueError(f"Operating point {path}: 'llamaswap.cmd' is required")
    return profile


def apply_operating_point(model_id: str) -> list[str]:
    """Adopt the profile 1:1 into the llama-swap config.

    Returns debug messages (English, for add_debug). Idempotent: an
    unchanged entry does not rewrite the config (llama-swap watches the
    file — needless writes would trigger needless reloads).
    """
    profile = get_operating_point(model_id)
    if profile is None:
        raise ValueError(f"No operating point for '{model_id}'")

    entry = profile["llamaswap"]
    group = profile.get("group", "main")

    # Hardware-bound: a profile measured on a different GPU set must not
    # be applied — topology and card order would be wrong. Re-measure
    # (or hand-update the profile) instead.
    measured_on = profile.get("hardware")
    if measured_on:
        current = gpu_fingerprint()
        if current != measured_on:
            raise ValueError(
                f"Hardware changed since the operating point was measured: "
                f"profile expects [{measured_on}], machine has [{current}]. "
                f"The operating point must be re-measured."
            )

    # A profile whose checkpoint is gone must not resurrect a dead entry
    # (the autoscan prunes such entries — the profile file it cannot see).
    ckpt = _checkpoint_path_from_cmd(entry["cmd"])
    if ckpt is not None and not ckpt.exists():
        raise ValueError(
            f"Checkpoint for operating point '{model_id}' is gone: {ckpt}. "
            f"Delete the profile or restore the model."
        )

    config = _read_yaml(LLAMASWAP_CONFIG_PATH)
    models = config.setdefault("models", {})
    members = config.get("groups", {}).get(group, {}).get("members", [])

    if models.get(model_id) == entry and model_id in members:
        return [f"📌 Operating point for {model_id} already in llama-swap config"]

    models[model_id] = entry
    _ensure_in_group(config, model_id, group)
    _write_yaml(LLAMASWAP_CONFIG_PATH, config)
    logger.info(f"Operating point applied: {model_id} (group {group})")
    return [
        f"📌 Static operating point applied: {model_id} → llama-swap (group {group})",
        "   Entry adopted 1:1 — no calibration needed",
    ]
