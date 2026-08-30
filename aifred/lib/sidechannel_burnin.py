"""Gemeinsamer Burn-in fuer VLM und TTS auf der Side-Channel-Karte.

Bei vLLM rechnet das Haupt-LLM nur auf den Karten, die
``calibration.vllm_flow.side_channel_uuids()`` uebrig laesst — VLM und TTS
teilen sich also EINE Karte. Ob beide dort zusammen unter Last passen,
beantwortet keine der Einzelmessungen: ``stress_burnin_tts`` misst die
TTS-Spitze auf leerer Karte, der VLM-Prewarm die VLM-Spitze auf leerer
Karte. Die Summe ist nicht die Wahrheit, weil beide Dienste ihre Puffer
gleichzeitig halten und der Allokator dazwischen fragmentiert.

Dieses Modul misst deshalb den GEMEINSAMEN Spitzenbedarf: VLM laden, dann
den TTS-Stresslauf darueber fahren, Spitze auf der Karte mitschreiben.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, TypedDict

logger = logging.getLogger(__name__)


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
    (kein TTS gepinnt) — der Aufrufer behandelt das als "nicht gemessen",
    nicht als Fehlschlag.
    """
    def _say(msg: str) -> None:
        if debug:
            debug(msg)
        logger.info("sidechannel_burnin: %s", msg)

    # Bewusst die privaten Helfer des TTS-Burn-ins: dieselbe Karte,
    # dieselbe Messmethode — nachbauen wuerde zwei Wahrheiten schaffen.
    from .tts_stress_burnin import (
        _PeakMonitor,
        _query_gpu_used_mb,
        _resolve_tts_gpu_index,
        stress_burnin_tts,
    )

    gpu_index = _resolve_tts_gpu_index()
    if gpu_index is None:
        _say("no side-channel GPU pinned — joint burn-in skipped")
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
    async with _PeakMonitor(gpu_index) as mon:
        await stress_burnin_tts(tts_engine_key, debug=debug)
        joint_peak_mb = max(mon.peak_mb, _query_gpu_used_mb(gpu_index))

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
