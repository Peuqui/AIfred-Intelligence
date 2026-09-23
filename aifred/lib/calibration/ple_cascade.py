"""Budget der PLE-Überlaufkaskade für vLLM-Einträge.

Modelle der Qwen4Exp-Familie tragen eine Hash-N-Gramm-Tabelle (PLE), die
größer ist als der VRAM der Rechenkarten (Flash-Next: 47,7 GiB). vLLM verteilt
sie über vier Stufen — VRAM, gepinnter Host-RAM, Speicherkarten, SSD — und
misst selbst, wie viel in den VRAM passt und wie viel eine Speicherkarte nach
dem Aufbau der Stufen frei hat. AIfred macht nur die Angebote: Es sagt, wie
viel Host-RAM je Rang gepinnt werden darf, welche Karte die Überlaufstufe
trägt und wie viel dort frei bleiben muss.

Die Speicherkarte ist die Sammelkarte der Seitenkanäle (``pick_side_channel_gpu``),
auf der auch VLM und TTS liegen. Deren gemessene Reserven bleiben frei —
je Eintragsvariante nur die, die dort tatsächlich läuft.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from ..config import PLE_HOST_SHARE_GIB, PLE_STORE_SAFETY_MB

_MIB = 1024 * 1024
_GIB = 1024**3


@dataclass(frozen=True)
class PleCascadePlan:
    """Was ein vLLM-Eintrag für die Kaskade mitbekommt."""

    visible_gpu_ids: list[int]   # CUDA_VISIBLE_DEVICES in dieser Reihenfolge
    env: dict[str, str]          # zusätzliche Umgebungsvariablen
    store_gib: float             # 0 = keine Speicherkarte in dieser Variante

    @property
    def uses_store_card(self) -> bool:
        return self.store_gib > 0


def plan_ple_cascade(
    *,
    ple_bytes: int,
    compute_gpu_ids: Sequence[int],
    side_channel_gpu: int | None,
    side_channel_total_mb: int,
    side_channel_reserved_mb: int,
    host_share_gib: float = PLE_HOST_SHARE_GIB,
    safety_mb: int = PLE_STORE_SAFETY_MB,
) -> PleCascadePlan | None:
    """Kaskade für einen Eintrag planen, oder ``None`` ohne PLE-Tabelle.

    ``side_channel_reserved_mb`` ist die Summe der gemessenen Reserven der
    Seitenkanäle, die in DIESER Variante auf der Karte laufen (VLM über
    ``resolve_vlm_reserve``, TTS über ``calibration_vram_reserve_mb``). Bleibt
    danach kein sinnvolles Budget übrig, trägt nur der Host-Anteil — die Karte
    wird dann gar nicht erst sichtbar gemacht, damit kein CUDA-Kontext auf ihr
    entsteht.
    """

    if ple_bytes <= 0:
        return None
    if host_share_gib < 0 or safety_mb < 0:
        raise ValueError("host share and safety margin must be non-negative")
    visible = list(compute_gpu_ids)
    env: dict[str, str] = {"VLLM_QWEN4EXP_PLE_HOST_GIB": _number(host_share_gib)}

    store_mb = side_channel_total_mb - side_channel_reserved_mb - safety_mb
    if (
        side_channel_gpu is None
        or side_channel_gpu in visible
        or store_mb <= 0
    ):
        return PleCascadePlan(visible_gpu_ids=visible, env=env, store_gib=0.0)

    visible.append(side_channel_gpu)
    store_gib = store_mb * _MIB / _GIB
    # Aufrunden: die Seitenkanäle dürfen nie ein paar MiB zu wenig haben.
    reserve_gib = math.ceil((side_channel_reserved_mb + safety_mb) / 1024 * 10) / 10
    env["VLLM_QWEN4EXP_PLE_STORE_DEVICES"] = str(len(visible) - 1)
    env["VLLM_QWEN4EXP_PLE_STORE_RESERVE_GIB"] = _number(reserve_gib)
    return PleCascadePlan(visible_gpu_ids=visible, env=env, store_gib=store_gib)


def _number(value: float) -> str:
    """Ganze Zahlen ohne Nachkomma schreiben, sonst eine Stelle."""
    rounded = round(value, 1)
    return str(int(rounded)) if rounded == int(rounded) else f"{rounded:.1f}"
