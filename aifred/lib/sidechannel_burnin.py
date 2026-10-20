"""Gemeinsamer Burn-in fuer VLM und TTS auf der Side-Channel-Karte.

Die Profile fuer die Zusatzmodelle planen mit der Summe der Einzelspitzen;
landen VLM und TTS auf derselben Karte, zaehlt ihre Summe. Ob beide dort zusammen unter Last passen,
beantwortet keine der Einzelmessungen: ``stress_burnin_tts`` misst die
TTS-Spitze auf leerer Karte, der VLM-Prewarm die VLM-Spitze auf leerer
Karte. Die Summe ist nicht die Wahrheit, weil beide Dienste ihre Puffer
gleichzeitig halten und der Allokator dazwischen fragmentiert.

Dieses Modul misst deshalb den GEMEINSAMEN Spitzenbedarf: VLM laden, dann
den TTS-Stresslauf darueber fahren, Spitze auf der Karte mitschreiben. Die
TTS-Engine startet dafuer ausdruecklich auf dieser Karte
(``stress_burnin_tts(gpu=...)``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, TypedDict

logger = logging.getLogger(__name__)


def _query_gpu_used_mb(gpu_index: int) -> int:
    """One-shot read of memory.used for a single GPU."""
    from .nvidia_smi import query
    rows = query("memory.used", gpu_index=gpu_index)
    try:
        return int(rows[0]["memory.used"]) if rows else 0
    except ValueError as e:
        logger.warning("tts_stress_burnin: nvidia-smi read failed (%s)", e)
        return 0


def _resolve_side_channel_gpu_index() -> Optional[int]:
    """PCI_BUS_ID index of the shared side-channel card (``pick_side_channel_gpu``);
    ``None`` when no GPU is available."""
    from .vision_gpu_select import pick_side_channel_gpu
    try:
        return pick_side_channel_gpu()
    except RuntimeError:
        return None


class _PeakMonitor:
    """Background sampler — same shape as the VLM stress-prewarm peak
    monitor. Polls memory.used every ``interval`` seconds, tracks the
    maximum."""

    def __init__(self, gpu_index: int, interval: float = 0.1) -> None:
        self._gpu = gpu_index
        self._interval = interval
        self._peak = 0
        self._task: Optional[asyncio.Task[None]] = None
        self._stop = asyncio.Event()

    async def _run(self) -> None:
        while not self._stop.is_set():
            used = _query_gpu_used_mb(self._gpu)
            if used > self._peak:
                self._peak = used
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    async def __aenter__(self) -> "_PeakMonitor":
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task

    @property
    def peak_mb(self) -> int:
        return self._peak


class SidechannelResult(TypedDict):
    """Ergebnis des gemeinsamen Laufs. ``fits`` ist die Aussage, auf die
    es ankommt; die Einzelwerte dienen der Nachvollziehbarkeit im Log."""
    gpu_index: int
    total_mb: int
    baseline_mb: int
    vlm_loaded_mb: int
    joint_peak_mb: int
    headroom_mb: int
    fits: bool
    tts_engine: str
    vlm_model: str


async def burnin_sidechannel(
    tts_engine_key: str,
    *,
    debug: Any = None,
) -> Optional[SidechannelResult]:
    """VLM laden, TTS-Stresslauf darueber, gemeinsame VRAM-Spitze messen.

    Gibt ``None`` zurueck, wenn keine Side-Channel-Karte aufloesbar ist
    (keine GPU) — der Aufrufer behandelt das als "nicht gemessen",
    nicht als Fehlschlag.
    """
    def _say(msg: str) -> None:
        if debug:
            debug(msg)
        logger.info("sidechannel_burnin: %s", msg)

    from .tts_stress_burnin import stress_burnin_tts

    gpu_index = _resolve_side_channel_gpu_index()
    if gpu_index is None:
        _say("no side-channel GPU — joint burn-in skipped")
        return None

    from .nvidia_smi import query
    rows = query("memory.total", gpu_index=gpu_index)
    total_mb = int(rows[0]["memory.total"]) if rows else 0

    baseline_mb = _query_gpu_used_mb(gpu_index)
    _say(f"GPU {gpu_index}: {baseline_mb} / {total_mb} MiB in use before burn-in")

    from .vision_prewarm import get_active_vlm_model, prewarm_vlm
    vlm_model = get_active_vlm_model() or ""
    if vlm_model:
        _say(f"loading VLM {vlm_model} ...")
        await prewarm_vlm()
        # Ollama meldet "done", bevor der Allokator zur Ruhe kommt.
        await asyncio.sleep(2.0)
    vlm_loaded_mb = _query_gpu_used_mb(gpu_index)
    if vlm_model:
        _say(f"VLM resident: {vlm_loaded_mb - baseline_mb} MiB")

    # Der TTS-Stresslauf laeuft jetzt MIT geladenem VLM — genau der
    # Zustand, den der Produktionsbetrieb herstellt.
    _say(f"TTS burn-in {tts_engine_key} with the VLM resident ...")
    # Die TTS-Suite platziert sonst selbst (leere Karte zuerst) — die Engine
    # landete dann nicht neben dem VLM, und die Spitze zeigte nur das VLM.
    async with _PeakMonitor(gpu_index) as mon:
        tts_peak = await stress_burnin_tts(tts_engine_key, gpu=str(gpu_index), debug=debug)
        joint_peak_mb = max(mon.peak_mb, _query_gpu_used_mb(gpu_index))
    if tts_peak is None:
        _say(f"TTS burn-in of {tts_engine_key} on GPU {gpu_index} failed — joint peak not measured")
        return None

    headroom_mb = total_mb - joint_peak_mb
    fits = headroom_mb > 0
    _say(f"joint peak {joint_peak_mb} / {total_mb} MiB "
         f"({headroom_mb} MiB headroom) — {'fits' if fits else 'DOES NOT FIT'}")

    # Persistieren, damit die Matrix im Kalibrier-Popover pro Paar zeigen
    # kann, was gemessen ist — und ob es gepasst hat.
    from . import sidechannel_vram_cache
    sidechannel_vram_cache.put(vlm_model, tts_engine_key, joint_peak_mb,
                               gpu_index, total_mb, fits)

    return SidechannelResult(
        gpu_index=gpu_index,
        total_mb=total_mb,
        baseline_mb=baseline_mb,
        vlm_loaded_mb=vlm_loaded_mb,
        joint_peak_mb=joint_peak_mb,
        headroom_mb=headroom_mb,
        fits=fits,
        tts_engine=tts_engine_key,
        vlm_model=vlm_model,
    )
