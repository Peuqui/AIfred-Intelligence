"""Readable GPU-label comments in the llama-swap config.

The config pins GPUs by UUID (reboot-stable across PCI re-enumeration on
the USB4-tunnel Mini), which is unreadable. ``_write_yaml`` therefore
annotates every ``CUDA_VISIBLE_DEVICES=GPU-…`` line with a derived
``# GPU0 (RTX 8000), GPU2 (RTX 8000)`` comment — regenerated on every
write (PyYAML strips comments), so it can never go stale. The UUID stays
the single source of truth; the comment is purely derived.
"""

from __future__ import annotations

from pathlib import Path

import yaml

import aifred.lib.calibration.gpu as gpu_mod
import aifred.lib.calibration.llamaswap_io as io


_U0 = "GPU-9216479c-8775-d2a2-bf55-3ee210ad8d0c"  # RTX 8000, nvidia-smi 0
_U2 = "GPU-fa3199cb-39df-0cff-86e4-5aba7166ef81"  # RTX 8000, nvidia-smi 2
_U3 = "GPU-83455f9f-a97a-112d-565b-da80f0757300"  # V100, nvidia-smi 3

_LABELS = {
    _U0: "GPU0 (RTX 8000)",
    _U2: "GPU2 (RTX 8000)",
    _U3: "GPU3 (V100)",
}


def _config(uuids: list[str]) -> dict:
    return {
        "models": {
            "big-model": {
                "cmd": "llama-server -sm layer --tensor-split 58,7",
                "env": [f"CUDA_VISIBLE_DEVICES={','.join(uuids)}"],
            }
        }
    }


def _patch_labels(monkeypatch, labels: dict[str, str]) -> None:
    monkeypatch.setattr(gpu_mod, "gpu_uuid_labels", lambda: labels)


def test_comment_inserted_above_cuda_line(monkeypatch, tmp_path: Path) -> None:
    _patch_labels(monkeypatch, _LABELS)
    cfg_path = tmp_path / "config.yaml"
    io._write_yaml(cfg_path, _config([_U0, _U2]))

    text = cfg_path.read_text()
    assert "# GPU0 (RTX 8000), GPU2 (RTX 8000)" in text
    # Comment sits directly above the env line it annotates.
    lines = text.splitlines()
    comment_idx = next(i for i, ln in enumerate(lines) if ln.strip().startswith("#"))
    assert "CUDA_VISIBLE_DEVICES" in lines[comment_idx + 1]


def test_yaml_still_parses_and_env_intact(monkeypatch, tmp_path: Path) -> None:
    _patch_labels(monkeypatch, _LABELS)
    cfg_path = tmp_path / "config.yaml"
    io._write_yaml(cfg_path, _config([_U0, _U2]))

    # The injected comment must not corrupt the round-trip: safe_load
    # ignores comments and the env value stays byte-exact.
    parsed = yaml.safe_load(cfg_path.read_text())
    env = parsed["models"]["big-model"]["env"]
    assert env == [f"CUDA_VISIBLE_DEVICES={_U0},{_U2}"]


def test_idempotent_no_duplicate_comments(monkeypatch, tmp_path: Path) -> None:
    _patch_labels(monkeypatch, _LABELS)
    cfg_path = tmp_path / "config.yaml"
    io._write_yaml(cfg_path, _config([_U0, _U2]))
    # Re-reading drops the comment (not part of the dict); re-writing
    # re-derives exactly one — never an accumulating stack.
    reparsed = yaml.safe_load(cfg_path.read_text())
    io._write_yaml(cfg_path, reparsed)

    text = cfg_path.read_text()
    assert text.count("# GPU0 (RTX 8000), GPU2 (RTX 8000)") == 1


def test_single_gpu_variant_labelled(monkeypatch, tmp_path: Path) -> None:
    _patch_labels(monkeypatch, _LABELS)
    cfg_path = tmp_path / "config.yaml"
    io._write_yaml(cfg_path, _config([_U3]))
    assert "# GPU3 (V100)" in cfg_path.read_text()


def test_unknown_uuid_falls_back_to_raw(monkeypatch, tmp_path: Path) -> None:
    # A UUID not in the map (e.g. a card removed after the last query)
    # is echoed verbatim rather than dropped — visible, not silent.
    _patch_labels(monkeypatch, {_U0: "GPU0 (RTX 8000)"})
    cfg_path = tmp_path / "config.yaml"
    io._write_yaml(cfg_path, _config([_U0, _U3]))
    assert f"# GPU0 (RTX 8000), {_U3}" in cfg_path.read_text()


def test_no_nvidia_smi_leaves_config_uncommented(monkeypatch, tmp_path: Path) -> None:
    # Empty label map (nvidia-smi unavailable) → no annotation, no crash.
    _patch_labels(monkeypatch, {})
    cfg_path = tmp_path / "config.yaml"
    io._write_yaml(cfg_path, _config([_U0, _U2]))

    text = cfg_path.read_text()
    assert "#" not in text
    assert yaml.safe_load(text)["models"]["big-model"]["env"] == [
        f"CUDA_VISIBLE_DEVICES={_U0},{_U2}"
    ]
