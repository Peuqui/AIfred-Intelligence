"""Hardware-agnostische GPU-Auswahl für Vision-Workloads.

Strategie (siehe Memory ``ollama-gpu-greedy-not-strategic``):
Die VLM-Pipeline soll **nicht** die schnellste GPU blockieren, weil dort
typischerweise der Haupt-Chat-LLM via llama-swap läuft. Stattdessen:
**Zweite GPU der höchsten Compute-Klasse**. Damit bleibt die schnellste GPU
für den Chat-Backend frei, und die VLM-Latenz bleibt im selben Architektur-
Tier (z.B. zweite RTX 8000 statt P40-Fallback).

Fallback-Kaskade:

1. **Bevorzugt:** zweitbeste GPU der höchsten Compute-Klasse
   (z.B. 2× RTX 8000 ⇒ index der zweiten RTX 8000).
2. **Wenn nur eine Top-Klasse-GPU:** nächstbeste GPU im Ranking
   (RTX 8000 belegt für Chat ⇒ V100 fürs VLM).
3. **Wenn nur eine GPU im System:** diese eine GPU.
4. **Kein NVIDIA-Stack verfügbar (pynvml fehlt):** ``RuntimeError`` —
   Caller (Plugin-Settings) fällt dann auf CPU-Provider zurück.

GPU-Indexierung ist **PCI_BUS_ID-stabil** (entspricht ``nvidia-smi``-
Reihenfolge), damit Werte zwischen Reboots und Subprozessen
reproduzierbar sind. Über ``CUDA_DEVICE_ORDER=PCI_BUS_ID`` in den
Subprozessen (Ollama-Service, InsightFace-Loader) wird der Index dann
auch in den Workern korrekt zugeordnet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Lock
from typing import Sequence

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GpuInfo:
    """Snapshot eines CUDA-GPUs in PCI_BUS_ID-Ordnung."""

    index: int                       # PCI_BUS_ID index
    name: str                        # z.B. "Quadro RTX 8000"
    compute_capability: tuple[int, int]   # (major, minor) — höher = neuer
    total_memory_mb: int             # in MiB


def list_gpus() -> list[GpuInfo]:
    """Enumerate all CUDA GPUs via NVML. Reihenfolge entspricht
    ``nvidia-smi`` (PCI_BUS_ID).

    Bei Fehlen von pynvml oder NVIDIA-Treiber: leere Liste — die Caller
    behandeln das gracefully (CPU-Fallback in InsightFace, Ollama nutzt
    seine eigene Default-Wahl).
    """
    try:
        import pynvml
    except ImportError:
        logger.debug("pynvml not installed — no GPU enumeration")
        return []
    try:
        pynvml.nvmlInit()
    except Exception as e:  # noqa: BLE001
        logger.debug("nvmlInit failed: %s", e)
        return []

    gpus: list[GpuInfo] = []
    try:
        count = pynvml.nvmlDeviceGetCount()
        for i in range(count):
            try:
                h = pynvml.nvmlDeviceGetHandleByIndex(i)
                name_raw = pynvml.nvmlDeviceGetName(h)
                name = name_raw.decode() if isinstance(name_raw, bytes) else str(name_raw)
                major, minor = pynvml.nvmlDeviceGetCudaComputeCapability(h)
                mem = pynvml.nvmlDeviceGetMemoryInfo(h)
                gpus.append(
                    GpuInfo(
                        index=i,
                        name=name,
                        compute_capability=(int(major), int(minor)),
                        total_memory_mb=int(mem.total // (1024 * 1024)),
                    )
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("NVML query failed for GPU %d: %s", i, e)
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:  # noqa: BLE001
            pass
    return gpus


def _rank(gpus: Sequence[GpuInfo]) -> list[GpuInfo]:
    """Sort by (compute_capability DESC, total_memory_mb DESC). Stable on ties
    via the input index — ensures reproducible second-best selection when two
    GPUs are identical."""
    return sorted(
        gpus,
        key=lambda g: (-g.compute_capability[0], -g.compute_capability[1],
                       -g.total_memory_mb, g.index),
    )


def pick_vlm_gpu(gpus: Sequence[GpuInfo] | None = None) -> int:
    """Choose a GPU index for the VLM (Ollama) according to the strategy.

    Returns the PCI_BUS_ID index of the chosen GPU.

    Raises ``RuntimeError`` if no GPU is available.
    """
    if gpus is None:
        gpus = list_gpus()
    if not gpus:
        raise RuntimeError("no CUDA GPU available")
    ranked = _rank(gpus)
    if len(ranked) == 1:
        return ranked[0].index
    top_cc = ranked[0].compute_capability
    top_class = [g for g in ranked if g.compute_capability == top_cc]
    if len(top_class) >= 2:
        # Second of the top compute class — leaves the fastest free for chat LLM
        return top_class[1].index
    # Only one top-class GPU — pick the next-best (preserves the architecture
    # tier as much as possible while still keeping the fastest free)
    return ranked[1].index


def pick_face_gpu(gpus: Sequence[GpuInfo] | None = None) -> int:
    """Choose a GPU index for InsightFace (face_detect+recognize).

    Same strategy as VLM — co-locating with the VLM is fine because
    InsightFace's footprint is tiny (~200 MB vs the VLM's ~17 GB).
    """
    return pick_vlm_gpu(gpus)


def ollama_override_text(gpu_id: int) -> str:
    """Generate the systemd drop-in content that pins Ollama to a GPU.

    Returns a string the user (or an admin script) writes to::

        /etc/systemd/system/ollama.service.d/gpu-pin.conf

    followed by ``sudo systemctl daemon-reload && sudo systemctl restart ollama``.

    ``CUDA_DEVICE_ORDER=PCI_BUS_ID`` is essential — without it, the index
    is reordered by FASTEST_FIRST and our PCI-stable choice would map to
    a different physical GPU.
    """
    return (
        "[Service]\n"
        f"Environment=\"CUDA_DEVICE_ORDER=PCI_BUS_ID\"\n"
        f"Environment=\"CUDA_VISIBLE_DEVICES={gpu_id}\"\n"
    )


# ── Cache (resolve "auto" once per process) ─────────────────────────────

_cached_vlm_gpu: int | None = None
_cache_lock = Lock()


def resolve_gpu_id(setting: int | str | None) -> int | None:
    """Resolve a settings-file value (``"auto"`` / int / None) to a concrete
    PCI_BUS_ID index.

    * ``int``    — used as-is
    * ``"auto"`` — runs ``pick_vlm_gpu()`` and caches the result
    * ``None``   — returns ``None`` (caller decides on CPU fallback)

    Returns ``None`` if ``"auto"`` was requested but no GPU is available —
    Caller handles fallback (e.g. ``["CPUExecutionProvider"]`` for
    InsightFace).
    """
    if setting is None:
        return None
    if isinstance(setting, int):
        return setting
    if isinstance(setting, str) and setting.lower() == "auto":
        global _cached_vlm_gpu
        with _cache_lock:
            if _cached_vlm_gpu is None:
                try:
                    _cached_vlm_gpu = pick_vlm_gpu()
                except RuntimeError as e:
                    logger.info("GPU auto-select unavailable: %s", e)
                    return None
            return _cached_vlm_gpu
    # Numeric string?
    try:
        return int(setting)
    except (TypeError, ValueError):
        logger.warning("invalid gpu_id setting: %r (expected int or 'auto')", setting)
        return None


def reset_cache() -> None:
    """Reset the cached auto-pick (for tests + after hardware changes)."""
    global _cached_vlm_gpu
    with _cache_lock:
        _cached_vlm_gpu = None
