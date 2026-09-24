"""Sidecar guard: unload a describer/embedder only when the next model needs its card."""

import asyncio
from typing import Any

import httpx
import pytest

import aifred.lib  # noqa: F401 — loads lib before backends (lib/__init__ ↔ backends cycle)
from aifred.backends.vllm import vLLMBackend
from aifred.lib.calibration.llamaswap_io import entry_gpu_uuids

UUIDS = ["GPU-aaaa-0", "GPU-bbbb-1", "GPU-cccc-2", "GPU-dddd-3", "GPU-eeee-4"]
PCI = {"CUDA_DEVICE_ORDER": "PCI_BUS_ID"}


def test_entry_gpu_uuids_resolves_uuids_and_pci_indices() -> None:
    assert entry_gpu_uuids({"CUDA_VISIBLE_DEVICES": "GPU-eeee"}, UUIDS) == {"GPU-eeee-4"}
    assert entry_gpu_uuids({**PCI, "CUDA_VISIBLE_DEVICES": "0,2,1,3"}, UUIDS) == set(UUIDS[:4])
    # Empty means CPU only; unset means every GPU.
    assert entry_gpu_uuids({"CUDA_VISIBLE_DEVICES": ""}, UUIDS) == frozenset()
    assert entry_gpu_uuids({}, UUIDS) is None


def test_entry_gpu_uuids_refuses_what_it_cannot_map() -> None:
    # FASTEST_FIRST indices are not nvidia-smi's.
    with pytest.raises(ValueError, match="PCI_BUS_ID"):
        entry_gpu_uuids({"CUDA_VISIBLE_DEVICES": "0,1"}, UUIDS)
    with pytest.raises(ValueError, match="no GPU"):
        entry_gpu_uuids({**PCI, "CUDA_VISIBLE_DEVICES": "5"}, UUIDS)
    with pytest.raises(ValueError, match="unknown GPU"):
        entry_gpu_uuids({"CUDA_VISIBLE_DEVICES": "GPU-ffff"}, UUIDS)


CONFIG = {
    "big-vllm": {"env": {**PCI, "CUDA_VISIBLE_DEVICES": "0,1,4,3,2"}},
    "pp4-vllm": {"env": {**PCI, "CUDA_VISIBLE_DEVICES": "0,2,1,3"}},
    "pp4-vllm-vlm-qwen3vl4b": {"env": {**PCI, "CUDA_VISIBLE_DEVICES": "0,2,1,3,4"}},
    "vl4b-visiond": {"env": {"CUDA_VISIBLE_DEVICES": "GPU-eeee-4"}},
    "bge-embed": {"env": {"CUDA_VISIBLE_DEVICES": ""}},
}


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class _Client:
    """Stands in for httpx.AsyncClient: serves /running, records unloads."""

    running: list[str] = []
    unloaded: list[str] = []

    def __init__(self, **_: Any) -> None:
        pass

    async def __aenter__(self) -> "_Client":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def get(self, url: str) -> _Response:
        return _Response({"running": [{"model": m} for m in self.running]})

    async def post(self, url: str) -> _Response:
        self.unloaded.append(url.rsplit("/", 1)[1])
        return _Response({})


def _evicted(monkeypatch: pytest.MonkeyPatch, model: str, running: list[str]) -> list[str]:
    import aifred.lib.calibration as calibration
    from aifred.lib.calibration import gpu

    _Client.running, _Client.unloaded = running, []
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(calibration, "parse_llamaswap_config", lambda _: CONFIG)
    monkeypatch.setattr(gpu, "gpu_uuids_by_index", lambda: UUIDS)
    backend = vLLMBackend.__new__(vLLMBackend)
    backend.base_url = "http://swap/v1"
    asyncio.run(backend._pre_request_check(model))
    return _Client.unloaded


def test_guard_evicts_only_the_sidecar_on_a_shared_card(monkeypatch) -> None:
    # The five-card model needs the describer's card; the CPU embedder stays.
    assert _evicted(monkeypatch, "big-vllm", ["vl4b-visiond", "bge-embed"]) == ["vl4b-visiond"]


def test_guard_keeps_sidecars_beside_a_model_on_other_cards(monkeypatch) -> None:
    assert _evicted(monkeypatch, "pp4-vllm", ["vl4b-visiond", "bge-embed"]) == []


def test_guard_keeps_the_describer_beside_a_vlm_reserve_profile(monkeypatch) -> None:
    assert _evicted(monkeypatch, "pp4-vllm-vlm-qwen3vl4b", ["vl4b-visiond"]) == []


def test_guard_does_not_unload_when_the_target_already_runs(monkeypatch) -> None:
    assert _evicted(monkeypatch, "big-vllm", ["big-vllm", "vl4b-visiond"]) == []


def test_guard_evicts_every_sidecar_when_the_cards_are_unknown(monkeypatch) -> None:
    # An entry the config does not list: better an unloaded describer than a
    # failed chat start.
    assert _evicted(monkeypatch, "unknown-vllm", ["vl4b-visiond", "bge-embed"]) == [
        "vl4b-visiond",
        "bge-embed",
    ]

