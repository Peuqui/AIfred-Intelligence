"""PLE-Store-Budget im llama-swap-Autoscan: GPU-Platz der Store-Karte abzueglich
Worker, Sicherheitsabstand und der Seitenkanaele der Variante (VLM/TTS)."""

import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
BASE = "Flash-Next-PLE-Kaskade-vllm"


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
    monkeypatch.setattr(module.nvidia_smi, "query", lambda fields, gpu_index=None: [
        {"index": "3", "uuid": "GPU-aaa", "memory.total": "32768", "memory.reserved": "274"},
        {"index": "4", "uuid": "GPU-bbb", "memory.total": "32768", "memory.reserved": "274"},
    ])
    return module


def _entry(name: str, store_gib: str, *, visible: str = "0,2,1,3,4",
           device: str = "4", disk: bool = False) -> str:
    env = [
        f"CUDA_VISIBLE_DEVICES={visible}",
        "VLLM_QWEN4EXP_PLE_HOST_GIB=2",
        f"VLLM_QWEN4EXP_PLE_STORE_DEVICE={device}",
        f"VLLM_QWEN4EXP_PLE_STORE_GIB={store_gib}",
    ]
    if disk:
        env.append("VLLM_QWEN4EXP_PLE_DISK=1")
    lines = [f"  {name}:", f"    cmd: python -m vllm --served-model-name {name}", "    env:"]
    lines += [f"    - {e}" for e in env]
    return "\n".join(lines) + "\n"


def _write(tmp_path: Path, *entries: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text("# gpu_hardware: test\nmodels:\n" + "".join(entries) + "groups:\n  main:\n    members: []\n")
    return path


def _budget(path: Path, name: str) -> str:
    block = path.read_text().split(f"  {name}:\n", 1)[1]
    return block.split("VLLM_QWEN4EXP_PLE_STORE_GIB=", 1)[1].split("\n", 1)[0]


def test_budget_per_variant_leaves_room_for_its_side_channels(autoscan, tmp_path) -> None:
    names = [BASE, f"{BASE}-tts-qwen3local", f"{BASE}-vlm-qwen3vl4b",
             f"{BASE}-tts-qwen3local-vlm-qwen3vl4b"]
    path = _write(tmp_path, *(_entry(n, "6") for n in names))
    assert autoscan.enforce_ple_store_budgets(path) == 4
    usable = 32768 - 274
    base_reserve = autoscan.PLE_STORE_WORKER_MB + autoscan.VRAM_SAFETY_MARGIN_MB
    expected = {
        BASE: usable - base_reserve,
        f"{BASE}-tts-qwen3local": usable - base_reserve - (6024 + 512),
        f"{BASE}-vlm-qwen3vl4b": usable - base_reserve - (8988 + 500),
        f"{BASE}-tts-qwen3local-vlm-qwen3vl4b": usable - base_reserve - (6024 + 512) - (8988 + 500),
    }
    for name, mib in expected.items():
        assert _budget(path, name) == f"{int(mib / 1024 * 10) / 10:.1f}", name
    assert path.read_text().startswith("# gpu_hardware: test\n")
    assert autoscan.enforce_ple_store_budgets(path) == 0  # idempotent


def test_disk_tier_profiles_keep_their_small_store(autoscan, tmp_path) -> None:
    path = _write(tmp_path, _entry(f"{BASE}-Disk", "1", disk=True))
    assert autoscan.enforce_ple_store_budgets(path) == 0
    assert _budget(path, f"{BASE}-Disk") == "1"


def test_missing_measurement_leaves_the_entry_unchanged(autoscan, tmp_path, capsys) -> None:
    path = _write(tmp_path, _entry(f"{BASE}-tts-xtts", "6"),
                  _entry(f"{BASE}-vlm-unbekannt", "6"))
    assert autoscan.enforce_ple_store_budgets(path) == 0
    assert _budget(path, f"{BASE}-tts-xtts") == "6"
    assert _budget(path, f"{BASE}-vlm-unbekannt") == "6"
    out = capsys.readouterr().out
    assert "no TTS measurement for 'xtts'" in out and "no VLM measurement for 'unbekannt'" in out


def test_vlm_measurement_at_another_context_does_not_count(autoscan, tmp_path) -> None:
    autoscan.VLM_VRAM_CACHE_FILE.write_text(json.dumps({
        "Qwen3VL-4B-Instruct-Q8_0": {"num_ctx": 8192, "peak_mb": 5000},
    }))
    path = _write(tmp_path, _entry(f"{BASE}-vlm-qwen3vl4b", "6"))
    assert autoscan.enforce_ple_store_budgets(path) == 0


def test_store_device_is_an_ordinal_in_cuda_visible_devices(autoscan, tmp_path) -> None:
    """STORE_DEVICE=1 with CUDA_VISIBLE_DEVICES=GPU-aaa,GPU-bbb means GPU-bbb."""
    path = _write(tmp_path, _entry(BASE, "6", visible="GPU-aaa,GPU-bbb", device="1"))
    assert autoscan._store_gpu_usable_mib(path.read_text().split(f"  {BASE}:\n", 1)[1]) == 32768 - 274
    path = _write(tmp_path, _entry(BASE, "6", visible="GPU-aaa", device="1"))
    assert autoscan.enforce_ple_store_budgets(path) == 0  # ordinal outside the list
