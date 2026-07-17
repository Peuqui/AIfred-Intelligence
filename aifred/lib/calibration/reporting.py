"""Log-/Sentinel-Formatierung und kleine Ergebnis-/Settings-Helfer.

Alles hier ist zustandslos (bzw. liest nur settings.json) — Helfer-Schicht,
darf :mod:`flow` NICHT importieren.
"""

from __future__ import annotations

import re

from ..formatting import format_number
from .gpu import gpu_label, gpu_uuid_labels
from .types import Candidate, GPU, Result
from .verifier import VerifyResult


def _split_str(ratios: tuple[float, ...]) -> str:
    """Anzeige-Format eines Splits — erhält Bruchanteile (17.18:…:1.13).

    Splits sind seit der proportionalen Varianten-Ableitung und den
    0.5er-Shifts NICHT mehr ganzzahlig; ein int()-Format würde z.B. einen
    0.32-Anteil als "0" (= idle) anzeigen. NUR für Logs/Meldungen — das
    __SPEED__-Sentinel behält sein eigenes int-Format (Parser-Grammatik).
    """
    return ":".join(f"{round(r, 2):g}" for r in ratios)


def _format_candidate_line(c: Candidate, gpus: list[GPU]) -> str:
    """One log line per projection cell, showing every GPU's predicted free.

    Position index = AIfred's compute-DESC enumeration index, identical
    to llama-server's CUDA index after the UUID-based VISIBLE_DEVICES
    pin (so reading "GPU0" in calibration logs matches "CUDA0" in
    llama-server logs).

    Example::

        [3 GPUs / KV=f16] max_ctx=262.144 split=22:22:4:0
          GPU0 RTX 8000: 2.500 MB, GPU1 RTX 8000: 3.100 MB,
          GPU2 V100: 1.800 MB, GPU3 P40: idle
    """
    labels = gpu_uuid_labels()
    parts: list[str] = []
    for i, g in enumerate(gpus):
        layers_i = int(c.tensor_split[i]) if i < len(c.tensor_split) else 0
        if layers_i == 0:
            parts.append(f"{gpu_label(g, i, labels)}: idle")
            continue
        free = c.predicted_free_mb[i] if i < len(c.predicted_free_mb) else 0
        parts.append(
            f"{gpu_label(g, i, labels)}: {format_number(max(0, free))} MB"
        )
    return (
        f"  [{c.n_gpus} GPUs / KV={c.kv_quant}] "
        f"max_ctx={format_number(c.max_context)} "
        f"split={_split_str(c.tensor_split)}\n"
        f"    {', '.join(parts)}"
    )


def _planned_free_line(
    split: tuple[float, ...], gpus: list[GPU], model_size_mb: float,
) -> str:
    """Per-GPU-Frei-Prognose für einen Split — NUR Gewichtsverteilung.

    Für die Shift-Meldungen im Refine-Loop: dort gibt es kein vram_model
    (KV/Buffer unbekannt), aber die reine Gewichts-Rechnung zeigt bereits,
    welche Karte ein Shift wie eng macht. Ehrlich gelabelt, damit niemand
    die Zahl für den echten Reststand hält (KV + Compute-Buffer + CUDA-
    Context kommen oben drauf)."""
    total = sum(split) or 1.0
    labels = gpu_uuid_labels()
    parts: list[str] = []
    for i, g in enumerate(gpus):
        if i >= len(split) or split[i] <= 0:
            parts.append(f"{gpu_label(g, i, labels)}: idle")
            continue
        free = int(g.free_mb - (split[i] / total) * model_size_mb)
        parts.append(f"{gpu_label(g, i, labels)}: {format_number(free)} MB")
    return "    plan after weights (excl. KV/buffers): " + ", ".join(parts)


def _fmt_verify(
    prefix: str, iteration: int,
    ts: tuple[float, ...], ctx: int, r: VerifyResult,
) -> str:
    status = "✓" if r.fits else "✗"
    head = (
        f"[{prefix}.{iteration}] {_split_str(ts)} | "
        f"ctx {format_number(ctx)} | {status}"
    )
    if r.detail:
        head += f" | {r.detail}"
    return head


def _active_uuid_csv(split: tuple[float, ...], gpus: list[GPU]) -> str:
    """UUIDs of the active (>0) split positions, in enumeration order.

    The sentinel's tensor-split CSV lists only the ACTIVE values — the
    position info (which physical GPU each value belongs to) is lost.
    This companion field restores it: the mixin passes it 1:1 as
    CUDA_VISIBLE_DEVICES to the YAML variant writers, so env and
    tensor-split can never desync again. (A 5-value split with a 4-UUID
    env inherited from a 4-GPU base made llama.cpp re-normalize onto 4
    GPUs → KV-cache OOM at load, 2026-07-06.)
    """
    return ",".join(
        gpus[i].uuid for i, v in enumerate(split)
        if v > 0 and i < len(gpus)
    )


def _result_sentinel(r: Result, thinks: bool, gpus: list[GPU]) -> str:
    ts_csv = ",".join(f"{x:g}" for x in r.tensor_split if x > 0)
    return (
        f"__RESULT__:{r.context}:{r.ngl}:{r.mode}:"
        f"{'thinks' if thinks else 'nothink'}:{r.kv_quant}:"
        f"{ts_csv}:{r.num_gpus}:{_active_uuid_csv(r.tensor_split, gpus)}"
    )


def _speed_sentinel(r: Result, gpus: list[GPU]) -> str:
    # Preserve legacy __SPEED__ grammar: the mixin parser does int(x) on
    # every split part, so this sentinel must stay integer-formatted
    # (speed splits are integer anyway) — do NOT reuse the fractional
    # display formatter _split_str here. The UUID csv is appended as the
    # 5th comma-element; consumers parse with maxsplit=4.
    split_colon = ":".join(str(int(x)) for x in r.tensor_split)
    return (
        f"__SPEED__:{split_colon},{r.context},{r.num_gpus},{r.kv_quant},"
        f"{_active_uuid_csv(r.tensor_split, gpus)}"
    )


def _build_result(
    candidate: Candidate,
    ctx: int,
    verify_r: VerifyResult,
    num_active_gpus: int,
) -> Result:
    return Result(
        variant="base" if candidate.mode == "gpu" else "base",
        mode=candidate.mode,
        context=ctx,
        ngl=candidate.ngl,
        kv_quant=candidate.kv_quant,
        tensor_split=candidate.tensor_split,
        num_gpus=num_active_gpus,
        thinks=bool(verify_r.thinks),
        remaining_free_mb=verify_r.measured_free_mb,
    )


def _active_gpu_count(ts: tuple[float, ...]) -> int:
    return sum(1 for r in ts if r > 0)


def _track_failed_solo(
    best_free: float, best_ctx: int, free_mb: float, max_ctx: int,
) -> tuple[float, int]:
    """Stärkste gescheiterte Solo-Karte mitführen (free_mb, deren max_ctx).

    Bei gleich großem free zählt der BESSERE Kontext (Schwesterkarte ohne
    Handicap schafft mehr) — die Skip-Entscheidung vergleicht dann gegen
    das Beste, was diese Kartengröße erreicht hat."""
    if free_mb > best_free:
        return free_mb, max_ctx
    if free_mb == best_free:
        return best_free, max(best_ctx, max_ctx)
    return best_free, best_ctx


def _ngl_from_cmd(cmd: str) -> int:
    m = re.search(r"-ngl\s+(\d+)", cmd)
    return int(m.group(1)) if m else 99


def _kv_quant_from_cmd(cmd: str) -> str:
    m = re.search(r"-ctk\s+(\S+)", cmd)
    return m.group(1) if m else "f16"


def _hybrid_allowed_in_settings() -> bool:
    """Read the user's hybrid-mode permission from settings.json.

    Off by default — hybrid is slow and the calibration itself takes much
    longer. Users opt in via the toggle next to the Calibration mode
    dropdown when they actually need to run a model larger than total
    GPU VRAM.
    """
    from ..settings import load_settings
    s = load_settings() or {}
    return bool(s.get("calibration_allow_hybrid", False))
