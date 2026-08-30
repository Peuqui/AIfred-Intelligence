"""Gemessene GEMEINSAME VRAM-Spitzen von VLM- und TTS-Paaren.

Eigener Speicher neben ``vlm_vram_cache`` und ``tts_vram_cache``, weil er
eine andere Frage beantwortet: Jene halten fest, was ein Dienst ALLEIN
braucht; hier steht, was ein Paar ZUSAMMEN auf einer Karte zieht. Die
Summe der Einzelwerte ist dafuer keine Antwort — beide Dienste halten
ihre Puffer gleichzeitig, und der Allokator fragmentiert dazwischen.

Schluessel ist ``"<vlm_key>|<tts_key>"`` in derselben Schreibweise wie
die Kalibrier-Matrix (leere Seite = "keins"), damit die Oberflaeche pro
Zelle direkt nachschlagen kann.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from .config import DATA_DIR
from .vram_peak_cache import JsonPeakCache

logger = logging.getLogger(__name__)

CACHE_FILE = DATA_DIR / "sidechannel_vram_cache.json"

_store = JsonPeakCache(CACHE_FILE, "sidechannel_vram_cache")


def pair_key(vlm_key: str, tts_key: str) -> str:
    """Matrix-Schluessel — SSOT fuer beide Seiten (Speicher und Anzeige)."""
    return f"{vlm_key}|{tts_key}"


def get(vlm_key: str, tts_key: str) -> Optional[int]:
    """Gemeinsame Spitze in MiB, oder ``None`` wenn ungemessen."""
    entry = _store.get_entry(pair_key(vlm_key, tts_key))
    if entry is None:
        return None
    peak = entry.get("peak_mb")
    if not isinstance(peak, (int, float)) or peak <= 0:
        return None
    return int(peak)


def put(vlm_key: str, tts_key: str, peak_mb: int, gpu_index: int,
        total_mb: int, fits: bool) -> None:
    """Messung festhalten. ``fits`` haelt das Urteil fest, damit die
    Oberflaeche eine gescheiterte Paarung rot statt gruen zeigen kann."""
    _store.put_entry(pair_key(vlm_key, tts_key), {
        "peak_mb": int(peak_mb),
        "gpu_index": int(gpu_index),
        "total_mb": int(total_mb),
        "fits": bool(fits),
        "measured_at": datetime.now().isoformat(timespec="seconds"),
    })


def fits(vlm_key: str, tts_key: str) -> Optional[bool]:
    """Urteil der letzten Messung, ``None`` wenn ungemessen."""
    entry = _store.get_entry(pair_key(vlm_key, tts_key))
    return None if entry is None else bool(entry.get("fits", False))


def clear() -> int:
    return _store.clear()
