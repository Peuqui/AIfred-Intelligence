"""Verdict per pair of the vLLM matrix: does a describer and a TTS engine fit together on the side-channel card?

The rule is the llama.cpp combos' (``calibration.service.side_channel_pair_fits``): the describer's reserve (its
stress-measured peak in ``vlm_vram_cache`` plus ``VLM_HEADROOM_MIB``) and the engine's need (the TTS service's
``needed_mib``, budget plus its headroom) against the card. A joint measurement under load is not possible: the TTS
service chooses its card itself, an empty one when there is one.

Key is ``"<vlm_key>|<tts_key>"`` as in the calibration matrix (empty side = none).
"""

from __future__ import annotations

import logging
from datetime import datetime

from .config import DATA_DIR
from .vram_peak_cache import JsonPeakCache

logger = logging.getLogger(__name__)

CACHE_FILE = DATA_DIR / "sidechannel_vram_cache.json"

_store = JsonPeakCache(CACHE_FILE, "sidechannel_vram_cache")


def pair_key(vlm_key: str, tts_key: str) -> str:
    """The matrix key (``calibration.matrix.cell_key`` writes the same)."""
    return f"{vlm_key}|{tts_key}"


def put(vlm_key: str, tts_key: str, needed_mb: int, gpu_index: int,
        total_mb: int, fits: bool) -> None:
    """The verdict of a pair with what it needs on its fullest card; ``fits`` lets the matrix show a pair that does
    not fit red instead of green."""
    _store.put_entry(pair_key(vlm_key, tts_key), {
        "needed_mb": int(needed_mb),
        "gpu_index": int(gpu_index),
        "total_mb": int(total_mb),
        "fits": bool(fits),
        "measured_at": datetime.now().isoformat(timespec="seconds"),
    })


def fits(vlm_key: str, tts_key: str) -> bool | None:
    """The pair's last verdict, ``None`` when it was never judged."""
    entry = _store.get_entry(pair_key(vlm_key, tts_key))
    return None if entry is None else bool(entry.get("fits", False))


def clear() -> int:
    return _store.clear()
