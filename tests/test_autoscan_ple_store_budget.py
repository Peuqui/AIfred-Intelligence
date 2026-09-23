"""PLE-Store-Freihalte-Werte im llama-swap-Autoscan: Auf Store-Karten außerhalb
der Pipeline bleibt der Bedarf der Seitenkanäle der Variante (VLM/TTS) plus
Sicherheitsabstand frei; Pipeline-Karten behalten die Vorgabe des Forks."""

import importlib.machinery
import importlib.util
import json
import math
import re
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
BASE = "Flash-Next-PLE-Kaskade-vllm"
RESERVE = "VLLM_QWEN4EXP_PLE_STORE_RESERVE_GIB"


@pytest.fixture()
def autoscan(tmp_path, monkeypatch):
    sys.path.insert(0, str(SCRIPTS))
    loader = importlib.machinery.SourceFileLoader(
        "llama_swap_autoscan_store", str(SCRIPTS / "llama-swap-autoscan.py")
    )
    spec = importlib.util.spec_from_loader("llama_swap_autoscan_store", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)

    config_py = tmp_path / "config.py"
    config_py.write_text(
        "VLM_NUM_CTX = 24576\nLLAMACPP_VLM_HEADROOM_MB = 500\n"
        "LLAMACPP_TTS_BURNIN_HEADROOM_MB = 512\n"
    )
    vlm_cache = tmp_path / "vlm_vram_cache.json"
    vlm_cache.write_text(json.dumps({
        "Qwen3VL-4B-Instruct-Q8_0": {"num_ctx": 24576, "peak_mb": 8988},
    }))
    tts_cache = tmp_path / "tts_vram_cache.json"
    tts_cache.write_text(json.dumps({"qwen3local": {"peak_mb": 6024}}))
    monkeypatch.setattr(module, "AIFRED_CONFIG_PY", config_py)
    monkeypatch.setattr(module, "VLM_VRAM_CACHE_FILE", vlm_cache)
    monkeypatch.setattr(module, "TTS_VRAM_CACHE_FILE", tts_cache)
    return module


def _entry(name: str, *, devices: str = "4", reserve: str | None = None,
           tp: int = 2, pp: int = 2) -> str:
    env = [
        "CUDA_VISIBLE_DEVICES=0,2,1,3,4",
        "VLLM_QWEN4EXP_PLE_HOST_GIB=3",
        f"VLLM_QWEN4EXP_PLE_STORE_DEVICES={devices}",
    ]
    if reserve is not None:
        env.append(f"{RESERVE}={reserve}")
    cmd = (f"python -m vllm --served-model-name {name} "
           f"--tensor-parallel-size {tp} --pipeline-parallel-size {pp}")
    lines = [f"  {name}:", f"    cmd: {cmd}", "    env:"]
    lines += [f"    - {e}" for e in env]
    return "\n".join(lines) + "\n"


def _write(tmp_path: Path, *entries: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text("# gpu_hardware: test\nmodels:\n" + "".join(entries) + "groups:\n  main:\n    members: []\n")
    return path


def _reserve(path: Path, name: str) -> str | None:
    block = re.search(rf"^  {re.escape(name)}:\n((?:    .*\n)+)", path.read_text(), re.M)
    assert block is not None, name
    value = re.search(rf"^    - {RESERVE}=(.*)$", block.group(1), re.M)
    return value.group(1) if value else None


def _gib(mib: int) -> str:
    return f"{math.ceil(mib / 1024 * 10) / 10:.1f}"


def test_spare_card_keeps_room_for_the_variants_side_channels(autoscan, tmp_path) -> None:
    names = [BASE, f"{BASE}-tts-qwen3local", f"{BASE}-vlm-qwen3vl4b",
             f"{BASE}-tts-qwen3local-vlm-qwen3vl4b"]
    # The first entry has no reserve yet, the others a stale one.
    path = _write(tmp_path, _entry(names[0]),
                  *(_entry(n, reserve="0.5") for n in names[1:]))
    assert autoscan.enforce_ple_store_reserves(path) == 4
    margin = autoscan.VRAM_SAFETY_MARGIN_MB
    expected = {
        BASE: margin,
        f"{BASE}-tts-qwen3local": margin + 6024 + 512,
        f"{BASE}-vlm-qwen3vl4b": margin + 8988 + 500,
        f"{BASE}-tts-qwen3local-vlm-qwen3vl4b": margin + 6024 + 512 + 8988 + 500,
    }
    for name, mib in expected.items():
        assert _reserve(path, name) == _gib(mib), name
    assert path.read_text().startswith("# gpu_hardware: test\n")
    assert autoscan.enforce_ple_store_reserves(path) == 0  # idempotent


def test_every_spare_card_gets_its_own_value(autoscan, tmp_path) -> None:
    # TP1 x PP2 computes on ordinals 0 and 1; 2 and 3 are spare cards.
    path = _write(tmp_path, _entry(f"{BASE}-tts-qwen3local", devices="2,3", tp=1, pp=2))
    assert autoscan.enforce_ple_store_reserves(path) == 1
    value = _gib(autoscan.VRAM_SAFETY_MARGIN_MB + 6024 + 512)
    assert _reserve(path, f"{BASE}-tts-qwen3local") == f"{value},{value}"


def test_pipeline_store_cards_keep_the_fork_default(autoscan, tmp_path) -> None:
    # PP4 computes on ordinals 0-3: stores on 1,2,3 carry no side channels.
    path = _write(tmp_path, _entry(f"{BASE}-tts-qwen3local", devices="1,2,3", tp=1, pp=4))
    assert autoscan.enforce_ple_store_reserves(path) == 0
    assert _reserve(path, f"{BASE}-tts-qwen3local") is None


def test_mixed_store_cards_are_left_alone(autoscan, tmp_path, capsys) -> None:
    path = _write(tmp_path, _entry(BASE, devices="3,4", tp=1, pp=4))
    assert autoscan.enforce_ple_store_reserves(path) == 0
    assert _reserve(path, BASE) is None
    assert "mix pipeline and spare cards" in capsys.readouterr().out


def test_missing_measurement_leaves_the_entry_unchanged(autoscan, tmp_path, capsys) -> None:
    path = _write(tmp_path, _entry(f"{BASE}-tts-xtts", reserve="8"),
                  _entry(f"{BASE}-vlm-unbekannt", reserve="8"))
    assert autoscan.enforce_ple_store_reserves(path) == 0
    assert _reserve(path, f"{BASE}-tts-xtts") == "8"
    assert _reserve(path, f"{BASE}-vlm-unbekannt") == "8"
    out = capsys.readouterr().out
    assert "no TTS measurement for 'xtts'" in out and "no VLM measurement for 'unbekannt'" in out


def test_vlm_measurement_at_another_context_does_not_count(autoscan, tmp_path) -> None:
    autoscan.VLM_VRAM_CACHE_FILE.write_text(json.dumps({
        "Qwen3VL-4B-Instruct-Q8_0": {"num_ctx": 8192, "peak_mb": 5000},
    }))
    path = _write(tmp_path, _entry(f"{BASE}-vlm-qwen3vl4b", reserve="8"))
    assert autoscan.enforce_ple_store_reserves(path) == 0


def test_compute_cards_default_to_one_without_parallel_flags(autoscan) -> None:
    assert autoscan._compute_card_count("    cmd: python -m vllm --model x\n") == 1
    assert autoscan._compute_card_count(
        "    cmd: vllm --tensor-parallel-size 2 --pipeline-parallel-size 2\n") == 4
