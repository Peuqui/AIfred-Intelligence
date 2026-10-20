"""Where the side channels (TTS, VLM) of a calibrated variant are held, and what that costs per card.

The TTS-Suite places its engines at runtime (a card nobody uses first, then the most free VRAM), so the
calibration no longer assumes a fixed TTS card. It reserves the engine's VRAM on the card where that costs
the LLM the least, and the suite finds the room there (or on any card with more):

1. a card the LLM does not use at all — the base result stays valid unchanged;
2. else the LLM card that carries the smallest share of the layers per MiB, so the fewest layers move.

A card that holds side channels keeps their measured peaks plus ONE fixed residual
(``SIDE_CHANNEL_RESIDUAL_MB``) free, however many of them sit there.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ..config import SIDE_CHANNEL_RESIDUAL_MB
from .types import GPU


def side_channel_reserves(gpus: Sequence[GPU], holds: Iterable[tuple[str | None, int]]) -> list[int]:
    """Per card (aligned to ``gpus``): the peaks of the side channels held there plus the residual once.

    ``holds`` are ``(card uuid, peak MiB)`` pairs; a pair without a card or without a peak holds nothing.
    """
    peaks = [0] * len(gpus)
    index_of = {gpu.uuid: index for index, gpu in enumerate(gpus)}
    for uuid, peak_mb in holds:
        if uuid is not None and peak_mb > 0 and uuid in index_of:
            peaks[index_of[uuid]] += peak_mb
    return [peak + SIDE_CHANNEL_RESIDUAL_MB if peak > 0 else 0 for peak in peaks]


def pick_tts_reserve_gpu(
    gpus: Sequence[GPU],
    base_split: Sequence[float],
    reserve_mb: int,
    *,
    vlm_gpu_uuid: str | None = None,
    vlm_reserve_mb: int = 0,
) -> str | None:
    """UUID of the card that keeps ``reserve_mb`` free for the TTS engine, or ``None`` when no card can hold it.

    ``base_split`` is aligned to ``gpus`` (a share above 0 = the LLM uses the card). A VLM reserve on a card
    counts against its capacity: both side channels (and the card's one residual) must fit on it.
    """
    if len(base_split) != len(gpus):
        raise ValueError(f"base split has {len(base_split)} slots for {len(gpus)} GPUs")

    def room(gpu: GPU) -> int:
        held = vlm_reserve_mb if gpu.uuid == vlm_gpu_uuid else 0
        return gpu.free_mb - held - reserve_mb - SIDE_CHANNEL_RESIDUAL_MB

    fitting = [(index, gpu) for index, gpu in enumerate(gpus) if room(gpu) >= 0]
    unused_by_llm = [(index, gpu) for index, gpu in fitting if base_split[index] <= 0]
    if unused_by_llm:
        return max(unused_by_llm, key=lambda item: (room(item[1]), -item[0]))[1].uuid
    if fitting:
        return min(fitting, key=lambda item: (base_split[item[0]] / item[1].free_mb, item[0]))[1].uuid
    return None
