"""Autoscan: KV-Typ der Describer und ihre Platzierungs-Varianten je Karte."""

import importlib.util
from pathlib import Path

import pytest
import yaml

_SPEC = importlib.util.spec_from_file_location(
    "llama_swap_autoscan",
    Path(__file__).resolve().parent.parent / "scripts" / "llama-swap-autoscan.py",
)
autoscan = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(autoscan)

HOME = "VL-4B-visiond"
CMD = "llama-server --model /m/VL-4B.gguf --mmproj /m/mmproj.gguf -ngl 99 -c 24576 --flash-attn on"
GPUS = [{"index": "0", "uuid": "GPU-a"}, {"index": "1", "uuid": "GPU-b"}, {"index": "2", "uuid": "GPU-c"}]

CONFIG = f"""models:
  Chat-27B:
    cmd: llama-server --model /m/chat.gguf -ngl 99
  {HOME}:
    cmd: {CMD}
    ttl: 900
    env:
    - CUDA_VISIBLE_DEVICES=GPU-c
groups:
  main:
    exclusive: true
    swap: true
    members:
      - Chat-27B
  vision:
    exclusive: false
    swap: true
    persistent: true
    members:
      - {HOME}
"""


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG)
    monkeypatch.setattr(autoscan.nvidia_smi, "query", lambda _fields, **_kw: GPUS)
    monkeypatch.setattr(autoscan, "read_vlm_num_ctx", lambda: 24576)
    monkeypatch.setattr(autoscan, "read_vlm_kv_cache_type", lambda: "q8_0")
    return path


def _models(path):
    return yaml.safe_load(path.read_text())


def test_kv_cache_type_is_set_on_the_home_profile(config):
    assert autoscan.enforce_visiond_flags(config) == 1
    assert "-c 24576 -ctk q8_0 -ctv q8_0 " in _models(config)["models"][HOME]["cmd"]
    assert autoscan.enforce_visiond_flags(config) == 0


def test_placements_on_every_other_card_copy_the_home(config):
    autoscan.enforce_visiond_flags(config)
    assert autoscan.sync_visiond_placements(config) == 2
    data = _models(config)
    home = data["models"][HOME]
    for index, uuid in (("0", "GPU-a"), ("1", "GPU-b")):
        entry = data["models"][f"{HOME}-gpu{index}"]
        assert entry["cmd"] == home["cmd"] and entry["ttl"] == 900
        assert entry["env"] == [f"CUDA_VISIBLE_DEVICES={uuid}"]
    assert f"{HOME}-gpu2" not in data["models"]  # Heimatkarte
    assert sorted(data["groups"]["vision"]["members"]) == [HOME, f"{HOME}-gpu0", f"{HOME}-gpu1"]
    assert autoscan.sync_visiond_placements(config) == 0


def test_placements_follow_a_changed_home(config):
    autoscan.sync_visiond_placements(config)
    autoscan.enforce_visiond_flags(config)
    # Die Text-Ersetzung trifft die wortgleichen Varianten-Zeilen mit;
    # was danach noch abweicht, gleicht sync an — so oder so am Ende gleich.
    autoscan.sync_visiond_placements(config)
    data = _models(config)["models"]
    assert data[f"{HOME}-gpu0"]["cmd"] == data[HOME]["cmd"]
    assert "-ctk q8_0" in data[f"{HOME}-gpu0"]["cmd"]


def test_hand_edited_placement_is_reset_to_the_home(config):
    autoscan.sync_visiond_placements(config)
    config.write_text(config.read_text().replace(
        f"  {HOME}-gpu0:\n    cmd: {CMD}", f"  {HOME}-gpu0:\n    cmd: {CMD} --extra",
    ))
    assert autoscan.sync_visiond_placements(config) == 1
    data = _models(config)["models"]
    assert data[f"{HOME}-gpu0"]["cmd"] == data[HOME]["cmd"]


def test_placement_of_a_vanished_card_is_removed(config, monkeypatch):
    autoscan.sync_visiond_placements(config)
    monkeypatch.setattr(autoscan.nvidia_smi, "query", lambda _fields, **_kw: GPUS[1:])
    assert autoscan.sync_visiond_placements(config) == 1
    data = _models(config)
    assert f"{HOME}-gpu0" not in data["models"]
    assert f"{HOME}-gpu0" not in data["groups"]["vision"]["members"]


def test_groups_keep_placements_in_the_vision_group(config):
    autoscan.sync_visiond_placements(config)
    autoscan.update_groups_in_yaml(config)
    groups = _models(config)["groups"]
    assert f"{HOME}-gpu0" in groups["vision"]["members"]
    assert all("visiond" not in m for m in groups["main"]["members"])


def test_placements_match_a_four_space_member_list(config):
    # llama-swap-build-config schreibt die Mitglieder mit vier Leerzeichen;
    # eine sechser Zeile darunter faltete am 09.10. alle Namen zu einem.
    config.write_text(config.read_text().replace(f"      - {HOME}\n", f"    - {HOME}\n"))
    autoscan.sync_visiond_placements(config)
    members = _models(config)["groups"]["vision"]["members"]
    assert sorted(members) == [HOME, f"{HOME}-gpu0", f"{HOME}-gpu1"]
