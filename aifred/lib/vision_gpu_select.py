"""Hardware-agnostische GPU-Auswahl für Vision- und TTS-Workloads.

Designentscheidung: Die schnellste Compute-Klasse bleibt komplett frei
für den Haupt-Chat-LLM (typisch via llama-swap). Die Side-Channels
(VLM + TTS) leben auf dem **Side-Channel-Tier** — der Klasse darunter.

**TTS und VLM werden getrennt platziert**, sobald der Tier mehr als
eine Karte hat: TTS auf die erste, VLM auf die zweite. Damit gibt es
keine VRAM-Konkurrenz mehr zwischen TTS-Container und VLM auf einer
Karte (vorher konnte z.B. Fish-TTS + Vigilantia-8B nicht koexistieren).
Hat der Tier nur eine Karte, teilen sich beide sie wie bisher.

**Compute-Floor (weich):** Side-Channels bevorzugen Karten mit Compute
≥ 7.0 (Volta+). Eine P40 (Pascal, cc 6.1) ist beim VLM-Prefill 3–4×
langsamer — sie wird als Side-Channel-Host nur dann gewählt, wenn es
gar keine schnellere Karte gibt (letzter Notnagel statt „keine Vision").

Side-Channel-Tier-Kaskade:

1. **Bevorzugt:** Karten der **zweithöchsten** Compute-Klasse, gefiltert
   auf Compute ≥ 7.0.
2. **Wenn alle GPUs in derselben Klasse sind:** alle außer der
   schnellsten (die bleibt fürs LLM) — z.B. 3× RTX 8000 ⇒ TTS auf die
   zweite, VLM auf die dritte.
3. **Wenn nur unter-7.0-Karten als Tier übrig sind:** weicher Fallback
   auf diese (P40 als Notnagel).
4. **Wenn nur eine GPU im System:** diese eine GPU.
5. **Kein NVIDIA-Stack verfügbar (pynvml fehlt):** ``RuntimeError`` —
   Caller (Plugin-Settings) fällt dann auf CPU-Provider zurück.

Bei 2× RTX 8000 (cc 7.5) + 1× V100 (cc 7.0) + 2× P40 (cc 6.1): TTS und
VLM beide auf die V100 (nur eine im Tier, P40 per Floor raus). Käme eine
zweite V100 dazu: TTS auf V100 #1, VLM auf V100 #2.

Hardware-agnostisch: Welche Karte das konkret ist, hängt von der
aktuellen Bestückung ab. Es wird **nicht** „immer V100" hartkodiert.

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

# Compute-Floor für Side-Channel-Hosts (VLM + TTS). Volta (7.0) ist die
# Untergrenze — Pascal (P40, 6.1) ist beim VLM-Prefill 3–4× langsamer
# (gemessen: 8B-Analyse ~10 s auf P40 vs ~3,4 s auf V100). Weicher Floor:
# greift nur, solange eine Karte ≥ 7.0 existiert; sonst Fallback nach
# unten, damit P40-only-Hosts nicht ganz ohne Vision dastehen.
SIDE_CHANNEL_MIN_COMPUTE: tuple[int, int] = (7, 0)


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


def _side_channel_tier(gpus: Sequence[GpuInfo]) -> list[GpuInfo]:
    """Ordered list of side-channel host GPUs (best first).

    The chat LLM owns the fastest compute class; side-channels (VLM +
    TTS) take the class below it. ``pick_tts_gpu`` claims the first card
    of this tier, ``pick_vlm_gpu`` the second (or the first if the tier
    has only one card). The soft compute floor keeps Pascal cards out
    unless they are the only option. See module docstring for the full
    cascade.

    Assumes a non-empty ``gpus``.
    """
    ranked = _rank(gpus)
    if len(ranked) == 1:
        return [ranked[0]]
    top_cc = ranked[0].compute_capability
    # Cards below the top compute class — keep the top tier free for the
    # chat LLM. If every card shares the top class, reserve only the
    # fastest (ranked[0]) for the LLM and let the rest host side-channels.
    candidates = [g for g in ranked if g.compute_capability != top_cc]
    if not candidates:
        candidates = list(ranked[1:])
    # Soft compute floor: prefer Volta+ for vision/TTS. Only fall back to
    # below-floor cards (P40 etc.) if nothing at/above the floor exists.
    floored = [
        g for g in candidates
        if g.compute_capability >= SIDE_CHANNEL_MIN_COMPUTE
    ]
    if floored:
        candidates = floored
    return candidates


def pick_tts_gpu(gpus: Sequence[GpuInfo] | None = None) -> int:
    """Choose a GPU index for TTS containers: **first card of the
    side-channel tier**.

    Returns the PCI_BUS_ID index. Raises ``RuntimeError`` if no GPU is
    available.
    """
    if gpus is None:
        gpus = list_gpus()
    if not gpus:
        raise RuntimeError("no CUDA GPU available")
    return _side_channel_tier(gpus)[0].index


def pick_vlm_gpu(gpus: Sequence[GpuInfo] | None = None) -> int:
    """Choose a GPU index for the VLM (Ollama): **second card of the
    side-channel tier**, falling back to the first card when the tier
    has only one card (then VLM co-locates with TTS, as before).

    Returns the PCI_BUS_ID index. Raises ``RuntimeError`` if no GPU is
    available.
    """
    if gpus is None:
        gpus = list_gpus()
    if not gpus:
        raise RuntimeError("no CUDA GPU available")
    tier = _side_channel_tier(gpus)
    return tier[1].index if len(tier) > 1 else tier[0].index


def pick_face_gpu(gpus: Sequence[GpuInfo] | None = None) -> int:
    """Choose a GPU index for InsightFace (face_detect+recognize).

    Co-locates with the VLM — InsightFace's footprint is tiny (~280 MB
    vs the VLM's multi-GB) and both are vision workloads, so sharing the
    VLM's card keeps the TTS card uncontended.
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
