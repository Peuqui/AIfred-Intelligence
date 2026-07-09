"""Top-level calibration orchestrator.

Five sequential phases (``A``–``E``) each documented inline.  The output
protocol (``__RESULT__`` / ``__SPEED__`` strings) is preserved so that
existing state-mixin parsers keep working without change.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import AsyncIterator, Optional

from ..config import (
    CALIBRATION_MIN_CONTEXT,
    LLAMACPP_CALIBRATION_PORT,
    LLAMACPP_CALIBRATION_PRECISION,
    LLAMACPP_HYBRID_HEALTH_TIMEOUT,
    LLAMACPP_VRAM_SAFETY_MARGIN,
    MIN_FREE_RAM_MB,
    MIN_USEFUL_CONTEXT_TOKENS,
)
from ..formatting import format_number
from ..gguf_utils import (
    extract_quantization_from_filename,
    get_gguf_layer_count,
    get_gguf_native_context,
    get_gguf_total_size,
)
from ..model_vram_cache import add_llamacpp_calibration
from . import llamaswap_io as io
from . import projection as proj
from .llamaswap_io import parse_tensor_split
from .gpu import (
    build_budget,
    cuda_visible_devices,
    enumerate_gpus,
    find_min_gpus_for_weights,
    format_gpu_positions,
    gpu_label,
    gpu_uuid_labels,
    total_free_mb,
)
from .optimizer import OptResult, fill_fastest_first
from .types import Budget, Candidate, GPU, Model, Result
from .verifier import VerifyResult, kill_orphan_on_port, verify

logger = logging.getLogger(__name__)


def _calib_file_log(line: str) -> None:
    """Eine Zeile ins persistente Kalibrier-Log schreiben.

    Die Debug-Konsole rotiert bei jedem App-Neustart — die Diagnose-
    Ausgaben einer stundenlangen Nacht-Kalibrierung waren danach weg
    (so überlebte der Reserve-Blindheits-Bug unentdeckt eine komplette
    Nacht). Diese Datei ist append-only und überlebt Neustarts."""
    try:
        from ..config import DATA_DIR
        from datetime import datetime
        path = DATA_DIR / "logs" / "calibration.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {line}\n")
    except OSError as e:
        logger.warning("calibration log write failed: %s", e)


def _tee_calibration_log(gen_func):
    """Decorator für die öffentlichen Kalibrier-Generatoren: jede
    yield-Zeile wird zusätzlich ins File-Log geschrieben — der Consumer
    (Debug-Konsole) bleibt unverändert."""
    import functools

    @functools.wraps(gen_func)
    async def wrapper(*args, **kwargs):
        _calib_file_log(f"━━━ {gen_func.__name__} start ━━━")
        try:
            async for msg in gen_func(*args, **kwargs):
                _calib_file_log(str(msg))
                yield msg
        finally:
            _calib_file_log(f"━━━ {gen_func.__name__} end ━━━")
    return wrapper


# KV-quant levels in order of quality (higher = better, larger VRAM).
# Q4 is intentionally excluded from the default sweep: it sacrifices
# too much quality for marginal VRAM savings.  A caller can opt in by
# passing ``min_kv="q4_0"`` (used by edge-case re-runs on very small
# remaining budgets, never by the default flow).
_DEFAULT_KV_LEVELS = ("f16", "q8_0")
_ALL_KV_LEVELS = ("f16", "q8_0", "q4_0")


@_tee_calibration_log
async def calibrate_llamacpp_model(
    model_id: str,
    gguf_path: Path,
    full_cmd: str,
    port: int = LLAMACPP_CALIBRATION_PORT,
    config_path: Optional[Path] = None,
    min_kv: str = "f16",
    known_thinking: Optional[bool] = None,
    env: Optional[dict[str, str]] = None,
    tts_gpu_uuid: Optional[str] = None,
    tts_gpu_extra_reserve_mb: int = 0,
    vlm_gpu_uuid: Optional[str] = None,
    vlm_gpu_extra_reserve_mb: int = 0,
) -> AsyncIterator[str]:
    """Calibrate one llama.cpp model end-to-end.

    Yields human-readable progress strings and two sentinel lines for
    programmatic consumers (state mixin / ``_parse_calibration_result``):

    ``__RESULT__:{ctx}:{ngl}:{mode}:{thinks|nothink}:{kv}:{ts_csv}:{num_gpus}``
    ``__SPEED__:{split_colon},{ctx},{num_gpus},{kv}``  (only when a
                                                       speed variant
                                                       was calibrated)

    When ``config_path`` is ``None`` the YAML is never written (dry-run
    mode used by TTS-variant calibration).
    """
    from ...backends.ollama import wait_for_vram_stable

    # ── Phase A: metadata + budget ──────────────────────────────────
    yield f"Reading GGUF metadata: {gguf_path.name}"
    model = _load_model_meta(model_id, gguf_path)
    if not model:
        yield "Cannot read GGUF metadata"
        yield "__RESULT__:0:0:error"
        return

    await kill_orphan_on_port(port)
    yield "Waiting for VRAM to stabilize..."
    await wait_for_vram_stable(max_wait_seconds=15.0)

    gpus = enumerate_gpus()
    if not gpus:
        yield "No GPUs detected"
        yield "__RESULT__:0:0:error"
        return

    # TTS-variant calibration: subtract the engine's permanent VRAM
    # reserve from the TTS GPU so the LLM gets planned with a cushion
    # for the (not-currently-loaded) TTS container. Same adjustment the
    # fast path applies — without it the fallback full calibration would
    # plan the TTS GPU full and the resulting profile would OOM once the
    # real TTS container is up.
    # reserve_vec begleitet die Anpassung: pro GPU die Summe der Side-
    # Channel-Reserven. Wandert ins Budget, damit verify() dieselben
    # Reserven auch von den PROBE-Messwerten abzieht — sonst optimiert
    # der Refine-Loop in den (physisch leeren) Reserve-Platz hinein.
    reserve_vec = [0] * len(gpus)
    if tts_gpu_extra_reserve_mb > 0 and tts_gpu_uuid:
        from dataclasses import replace
        adjusted = []
        for i, g in enumerate(gpus):
            if g.uuid == tts_gpu_uuid:
                new_free = max(0, g.free_mb - tts_gpu_extra_reserve_mb)
                yield (
                    f"TTS variant: reserving {tts_gpu_extra_reserve_mb} MB "
                    f"on {g.name} ({g.free_mb} → {new_free} MB free)"
                )
                reserve_vec[i] += tts_gpu_extra_reserve_mb
                adjusted.append(replace(g, free_mb=new_free))
            else:
                adjusted.append(g)
        gpus = adjusted

    # Same idea for the VLM side-channel (Vigilantia + on-demand Tool-Use):
    # the configured VLM model occupies a known peak (measured table or
    # stress-prewarm cache) on its assigned GPU. Subtract that from the
    # free budget so the LLM is permanently planned around it — the VLM
    # may be unloaded at calibration time but will reclaim its slot on
    # next inference.
    if vlm_gpu_extra_reserve_mb > 0 and vlm_gpu_uuid:
        from dataclasses import replace
        adjusted = []
        for i, g in enumerate(gpus):
            if g.uuid == vlm_gpu_uuid:
                new_free = max(0, g.free_mb - vlm_gpu_extra_reserve_mb)
                yield (
                    f"VLM reserve: holding {vlm_gpu_extra_reserve_mb} MB "
                    f"on {g.name} ({g.free_mb} → {new_free} MB free)"
                )
                reserve_vec[i] += vlm_gpu_extra_reserve_mb
                adjusted.append(replace(g, free_mb=new_free))
            else:
                adjusted.append(g)
        gpus = adjusted

    gpu_total = tuple(g.total_mb for g in gpus)

    # Probe-first (2026-07-07): kein Vision-Zuschlag mehr auf die Margin.
    # Der CLIP-/mmproj-Bedarf wird von der 4K-Bild-Probe im Verifier real
    # alloziert und gemessen statt pauschal reserviert.
    safety_margin = LLAMACPP_VRAM_SAFETY_MARGIN
    if _is_vision_model(full_cmd):
        yield "Vision model detected — probe includes 4K image analysis"

    budget = build_budget(gpus, safety_margin=safety_margin)
    if any(reserve_vec):
        from dataclasses import replace as _replace
        budget = _replace(budget, gpu_reserve_mb=tuple(reserve_vec))

    yield (
        f"Model: {model.model_id} ({format_number(model.size_mb / 1024, 1)} GB), "
        f"native context: {format_number(model.native_context)} "
        f"(model = {model.size_mb / sum(gpu_total):.0%} of "
        f"{format_number(sum(gpu_total) / 1024, 1)} GB VRAM)"
    )

    # ── AI-driven calibration (alternative path, NOT a legacy hybrid) ──
    # The toggle picks ONE path: either the algorithm or the AI, never a
    # mix. So when AI mode is on it is TERMINAL — on AI failure we emit an
    # honest error sentinel and stop, we do NOT silently fall back to the
    # classic algorithm (CLAUDE.md: no fallbacks, no mixing).
    from ..settings import load_settings as _load_settings
    settings = _load_settings() or {}
    # `or "legacy"` so a missing OR null key both fall back cleanly
    # (str(None) would be "None", not "legacy").
    cal_mode = str(settings.get("calibration_mode") or "legacy")
    if cal_mode == "ai":
        async for line in _try_ai_calibration(
            model_id=model_id,
            full_cmd=full_cmd,
            gguf_path=gguf_path,
            safety_margin=safety_margin,
            port=port,
            env=env,
            model_size_mb=model.size_mb,
            native_ctx=model.native_context,
            total_layers=model.total_layers,
            config_path=config_path,
            gpus=gpus,
            reserve_mb=budget.gpu_reserve_mb,
        ):
            yield line
            if line.startswith("__RESULT__:"):
                return
        return  # AI mode is terminal — never drop into the legacy phases

    yield (
        f"Free VRAM: {format_number(total_free_mb(gpus))} MB, "
        f"first-GPU handicap (idle floor): {budget.first_gpu_handicap} MB "
        f"— model-derived per cell once fit-params ran"
    )

    # ── Phase 1: estimate-first + immediate probe per candidate ─────
    # For each (kv-quality, n-gpus) cell — fewest GPUs first, fastest
    # KV first — run the math projection. If that says ≥ native_ctx is
    # reachable, immediately probe (real run + up to 15 layer shifts on
    # OOM). First successfully-verified config wins → BASE. Rationale:
    # the math is cheap (1-2 s) and filters out hopeless GPU counts
    # without burning a 30-90 s probe; the probe is the truth and
    # catches MoE/runtime overhead that fit-params miss.
    min_gpus = find_min_gpus_for_weights(model.size_mb, gpus)
    kv_levels = _kv_levels_from(min_kv)

    yield (
        f"Phase 1: searching (KV-quality first, then GPU count) for "
        f"native ctx={format_number(model.native_context)}..."
    )

    base_pick: Candidate | None = None
    base_result_obj: Result | None = None
    all_tried: list[Candidate] = []
    known_thinks: bool | None = known_thinking
    configs = _enumerate_gpu_configs(gpus, min_gpus)
    for kv in kv_levels:
        if base_pick is not None:
            break
        # Dominanz-Abkürzung für den 1-GPU-Sweep: Scheitert eine Karte
        # (an Kapazität ODER Kontext), ist jede Karte mit kleinerem
        # effektivem Budget chancenlos — deren fit-params-Läufe (2 Stück
        # à 3-13 s je nach Modellgröße) sind reine Zeitverschwendung.
        # GLEICH große Karten werden nur getestet, wenn die gescheiterte
        # KNAPP dran war: Das Display-Handicap (256 MB ≈ wenige tausend
        # Tokens) ist der einzige Unterschied zwischen Schwesterkarten —
        # es kann einen 2%-Fehlbetrag wettmachen, aber keine 25 %.
        failed_solo_free = -1.0
        failed_solo_ctx = 0
        labels = gpu_uuid_labels()
        for active in configs:
            n = len(active)
            if n == 1:
                label = gpu_label(gpus[active[0]], active[0], labels)
                _free_i = gpus[active[0]].free_mb
                _clearly_smaller = _free_i <= failed_solo_free - budget.first_gpu_handicap
                _same_size_hopeless = (
                    _free_i <= failed_solo_free
                    and failed_solo_ctx
                    < model.native_context * (1 - _SOLO_NEAR_MISS_RATIO)
                )
                if _clearly_smaller or _same_size_hopeless:
                    yield (
                        f"  [{label} / KV={kv}] skipped — "
                        f"{format_number(int(_free_i))} MB free "
                        f"≤ already-failed card"
                        + ("" if _clearly_smaller else
                           f" (its {format_number(failed_solo_ctx)} ctx is not a near miss)")
                    )
                    continue
            else:
                # nvidia-smi-anchored labels (SSOT gpu_label) so it's clear
                # which physical cards the config picks — same numbering as
                # the config comments and `nvidia-smi`.
                _names = ", ".join(gpu_label(gpus[i], i, labels) for i in active)
                label = f"{n} GPUs ({_names})"
            c, reason = await _project_cell(
                model, gpus, budget, full_cmd, kv, active,
            )
            if c is None:
                if n == 1:
                    failed_solo_free, failed_solo_ctx = _track_failed_solo(
                        failed_solo_free, failed_solo_ctx,
                        gpus[active[0]].free_mb, 0,
                    )
                yield f"  [{label} / KV={kv}] estimate: {reason}"
                continue
            all_tried.append(c)
            yield _format_candidate_line(c, gpus)
            if c.max_context < model.native_context:
                if n == 1:
                    failed_solo_free, failed_solo_ctx = _track_failed_solo(
                        failed_solo_free, failed_solo_ctx,
                        gpus[active[0]].free_mb, c.max_context,
                    )
                # max_ctx steht schon in der Kandidatenzeile darüber —
                # hier nur noch das Urteil.
                yield f"  [{label} / KV={kv}] < native — try next config"
                continue

            # Math says fit at native → real probe + shift loop
            yield (
                f"  [{label} / KV={kv}] math OK at native ctx → verifying"
            )
            v_result: Result | None = None
            async for item in _verify_and_refine(
                c, model, gpus, budget, full_cmd, port, env,
                probe_thinking=(known_thinks is None),
                status_prefix=f"[{label}/{kv}]",
            ):
                if isinstance(item, _Done):
                    v_result = item.result
                else:
                    yield item
            if v_result is not None:
                base_pick = c
                base_result_obj = v_result
                if base_result_obj.thinks is not None:
                    known_thinks = base_result_obj.thinks
                yield (
                    f"  ✓ Phase 1 success: {label}, KV={kv}, "
                    f"split={_split_str(base_result_obj.tensor_split)}, "
                    f"ctx={format_number(base_result_obj.context)}"
                )
                break

    # No candidate reached native context. Before giving up to hybrid,
    # try a **best-effort GPU-only fit**. Prefer the HIGHEST KV QUALITY
    # (f16 > q8_0) whose best GPU-set still reaches a useful context —
    # full-precision KV is faster on these GPUs (P40/V100/RTX8000 have no
    # fast quantized-KV attention path) and higher quality. Only drop to a
    # lower KV quality if the better one can't even reach a useful context.
    # (Previously this just took max(max_context), which silently traded
    # full-precision KV for ~20% more context — slower on this hardware.)
    if base_result_obj is None and all_tried:
        best = None
        for kv in kv_levels:  # quality-ordered: f16 first
            kv_best = max(
                (c for c in all_tried if c.kv_quant == kv),
                key=lambda c: c.max_context,
                default=None,
            )
            if kv_best is not None and kv_best.max_context >= MIN_USEFUL_CONTEXT_TOKENS:
                best = kv_best
                break
        if best is None:  # no KV level reached useful ctx → absolute best
            best = max(all_tried, key=lambda c: c.max_context)
        if best.max_context >= MIN_USEFUL_CONTEXT_TOKENS:
            best_label = (
                f"{best.n_gpus} GPUs / KV={best.kv_quant}"
            )
            yield (
                f"  💡 No native-ctx fit — falling back to best candidate: "
                f"{best_label}, ctx={format_number(best.max_context)}"
            )
            v_result_fb: Result | None = None
            async for item in _verify_and_refine(
                best, model, gpus, budget, full_cmd, port, env,
                probe_thinking=(known_thinks is None),
                status_prefix=f"[best-effort/{best.kv_quant}]",
            ):
                if isinstance(item, _Done):
                    v_result_fb = item.result
                else:
                    yield item
            if v_result_fb is not None:
                base_result_obj = v_result_fb
                if base_result_obj.thinks is not None:
                    known_thinks = base_result_obj.thinks
                yield (
                    f"  ✓ Phase 1 best-effort success: "
                    f"split={_split_str(base_result_obj.tensor_split)}, "
                    f"ctx={format_number(base_result_obj.context)}"
                )

    # Still no fit → no GPU-only path. Try hybrid (or fail).
    if base_result_obj is None:
        if not _hybrid_allowed_in_settings():
            yield (
                "❌ No GPU-only configuration verified at native context. "
                "Hybrid mode is disabled in settings — model is too large "
                "for the available GPU VRAM."
            )
            yield "💡 Enable the Hybrid toggle next to the Calibration mode dropdown to allow CPU offload."
            yield "__RESULT__:0:0:error"
            return
        yield "No GPU-only configuration verified — trying hybrid"
        async for msg in _calibrate_hybrid(
            model, gpus, budget, full_cmd, port, env,
            known_thinking=known_thinking, config_path=config_path,
        ):
            yield msg
        return

    final = base_result_obj
    thinks = final.thinks if known_thinking is None else known_thinking

    # ── Phase E: speed variant (fewer GPUs, fastest class only) ─────
    speed_result: Optional[Result] = None
    if len(gpus) > 1 and final.num_gpus > 1:
        speed_pick = await _find_speed_candidate(
            model, gpus, budget, full_cmd, all_tried,
            base_n_gpus=final.num_gpus,
            base_kv=final.kv_quant,
        )
        if speed_pick is not None:
            # Nur der Phasen-Marker — Split/ctx/Frei-MB stehen in der
            # folgenden Kandidatenzeile (sonst dieselben Zahlen doppelt).
            yield "Phase E: speed variant (fewer GPUs, fastest class)"
            yield _format_candidate_line(speed_pick, gpus)
            # lock_active_gpus=True: speed must use FEWER GPUs than base.
            # If shifts can't fit at target ctx, ctx-shrink iteratively
            # rather than activating an idle GPU (which would bring us
            # back to the base config).
            speed_result_holder: Result | None = None
            async for item in _verify_and_refine(
                speed_pick, model, gpus, budget, full_cmd, port, env,
                probe_thinking=False,
                status_prefix="speed",
                lock_active_gpus=True,
            ):
                if isinstance(item, _Done):
                    speed_result_holder = item.result
                else:
                    yield item
            speed_result = speed_result_holder

    # ── Speed → Base promotion / drop ──────────────────────────────
    # If the speed variant reaches native context at the same KV
    # quality, it is strictly better than the base (fewer/faster GPUs,
    # same everything else).  Promote it and drop the speed variant —
    # no point offering two configs that differ only in GPU count when
    # the smaller one is already on par.
    if (
        speed_result is not None
        and speed_result.context >= model.native_context
        and speed_result.kv_quant == final.kv_quant
    ):
        yield (
            f"Speed variant matches native ctx at KV={final.kv_quant} — "
            f"promoting to base ({speed_result.num_gpus} GPUs), "
            f"no separate speed variant kept"
        )
        final = speed_result
        speed_result = None

    # If speed ended up with the SAME split as base (e.g. activating an
    # idle GPU during shift loop reached the base config), drop it —
    # there's no speed gain and we'd just write a redundant config.
    if (
        speed_result is not None
        and speed_result.tensor_split == final.tensor_split
    ):
        yield (
            f"Speed split identical to base ({_split_str(final.tensor_split)}) — "
            f"no speed gain possible, variant skipped"
        )
        speed_result = None

    # ── Phase D: write configs + persist cache ─────────────────────
    # Only persist (YAML + cache) for real runs. TTS-variant calibration
    # passes config_path=None (dry_run) and writes its own YAML entry via
    # add_llamaswap_tts_variant — must NOT overwrite the base cache here,
    # else base speed_split fields get lost.
    if config_path:
        async for msg in _write_base_config(config_path, model_id, final, gpus):
            yield msg
        _persist_cache(model, final, gpus, speed_result=speed_result)
        if speed_result:
            async for msg in _write_speed_config(config_path, model_id, speed_result):
                yield msg

    # ── Emit sentinels ─────────────────────────────────────────────
    yield _result_sentinel(final, thinks=thinks, gpus=gpus)
    if speed_result:
        yield _speed_sentinel(speed_result, gpus)


# ═══════════════════════════════════════════════════════════════════
# Phase helpers
# ═══════════════════════════════════════════════════════════════════

def _enumerate_gpu_configs(
    gpus: list[GPU], min_gpus: int,
) -> list[list[int]]:
    """Generate the candidate GPU index lists to try, in priority order.

    The returned indices reference ``gpus`` (which is already sorted by
    compute_cap DESC). The order in which configs are emitted reflects
    AIfred's preference for fewer + faster + more-homogeneous GPU sets.

    Phases:

    1. **Single GPU** (when ``min_gpus <= 1``), each one tried in
       compute-DESC order: [0], [1], ... — single-GPU beats multi-GPU
       at comparable speed (no inter-GPU transfer, KV stays on one card).
    2. **Multi-GPU**, ascending in count. For each ``n``:
       a. **Homogeneous**: all ``n`` GPUs from the same speed_class, if
          available. Probed first because mixed compute classes mean the
          slowest card paces the whole inference (sequential layer
          dispatch). Tried in class order (highest compute first).
       b. **Mixed (compute-first fill)**: take the first ``n`` GPUs from
          ``gpus`` (highest compute first, mixing classes if necessary).
          Skipped when identical to the homogeneous case.
    """
    n_total = len(gpus)
    by_class: dict[int, list[int]] = defaultdict(list)
    for i, g in enumerate(gpus):
        by_class[g.speed_class].append(i)

    configs: list[list[int]] = []

    # 1. Single-GPU enumeration (compute-DESC)
    if min_gpus <= 1 and n_total >= 1:
        for i in range(n_total):
            configs.append([i])

    # 2. Multi-GPU
    for n in range(max(2, min_gpus), n_total + 1):
        # 2a. Homogeneous: try each speed class that has >= n members.
        for cls in sorted(by_class):
            members = by_class[cls]
            if len(members) >= n:
                combo = sorted(members[:n])
                if combo not in configs:
                    configs.append(combo)
        # 2b. Compute-first fill (the existing fastest-first stack).
        mixed = list(range(n))
        if mixed not in configs:
            configs.append(mixed)

    return configs


def _is_vision_model(cmd: str) -> bool:
    return "--mmproj" in cmd


def _kv_levels_from(min_kv: str) -> list[str]:
    """Pick which KV-quant levels to include in the sweep.

    Always includes F16 and Q8 (cheap to project anyway, and the picker
    uses a strict quality ranking).  Q4 is *only* added when the caller
    explicitly asks for it via ``min_kv="q4_0"`` — typically never, since
    the default flow prefers to add a GPU over dropping to Q4.

    ``min_kv`` is therefore interpreted as "Q4 is also acceptable",
    not "start sweeping here".
    """
    if min_kv == "q4_0":
        return list(_ALL_KV_LEVELS)  # f16, q8_0, q4_0
    return list(_DEFAULT_KV_LEVELS)  # f16, q8_0


# 1-GPU-Sweep-Dominanz: Eine gleich große Schwesterkarte unterscheidet sich
# nur ums Display-Handicap (~256 MB ≈ wenige tausend Tokens). Sie wird nur
# noch getestet, wenn die gescheiterte Karte den nativen Kontext um weniger
# als diesen Anteil verfehlt hat — ein 25%-Fehlbetrag ist damit nie aufholbar.
_SOLO_NEAR_MISS_RATIO = 0.05


def _quantize_split_to_layers(
    split: tuple[float, ...], total_layers: int
) -> tuple[float, ...]:
    """Fraktionalen Split auf ganze Layer runden (Summe = total_layers).

    llama.cpp platziert im ``-sm layer``-Modus nur GANZE Layer — ein Split
    wie ``16.68:17.4:6.12:…`` ist physikalisch nicht darstellbar und wird
    intern gerundet. Empirisch belegt: ein Shift ``V100 2 → 1.5`` bewegte
    physisch nichts (identische Messwerte), erst ``2 → 1`` schob einen
    echten Layer. Wir runden deshalb SELBST (Largest-Remainder, Summe bleibt
    total_layers), damit der getestete + geloggte Split GENAU dem entspricht,
    was llama.cpp lädt — statt eine Feinkörnigkeit vorzugaukeln, die es nicht
    gibt (und die nur No-Op-Reloads kostet). Nullen (idle) bleiben null."""
    floors = [int(x) for x in split]
    remainder = total_layers - sum(floors)
    if remainder <= 0:
        return tuple(float(x) for x in floors)
    # Rest-Layer an die Karten mit größtem Nachkomma-Anteil (Largest-Remainder).
    order = sorted(
        range(len(split)), key=lambda i: split[i] - floors[i], reverse=True
    )
    result = list(floors)
    for k in range(remainder):
        result[order[k % len(order)]] += 1
    return tuple(float(x) for x in result)


def _weights_fit(
    split: tuple[float, ...],
    gpus: list[GPU],
    budget: Budget,
    model: Model,
) -> tuple[bool, str]:
    """True wenn die reinen Layer-GEWICHTE des Splits auf jede aktive GPU
    passen (free − Gewichtsanteil − safety_margin − Handicap ≥ 0).

    Prüft NUR die Gewichte, nicht den KV-Cache: der KV skaliert mit dem
    Kontext und wird vom ctx-shrink im Verify behandelt. Das trennt zwei
    Fälle, die das ``fill_fastest_first``-Urteil ("model too big")
    vermischt:

    - Gewichte passen NICHT (auch bei ctx→0 überläuft eine Karte) → der
      Split ist physikalisch unmöglich, Probe wäre ein garantierter
      OOM/Segfault-Load. Skippen ist korrekt.
    - Gewichte passen, nur der KV bei der (von der Basis geerbten) hohen
      ctx sprengt → der Split ist gültig, die Probe muss laufen; der
      ctx-shrink senkt die ctx bis es passt, der Upward-Push maximiert
      danach zurück ans safety_margin-Limit.

    Der first-in-class-Handicap fließt ein, damit der Load-Peak der ersten
    Karte (Output/Logits, Compute-Workspace) mit abgedeckt ist.
    """
    total = sum(split) or 1.0
    for i, layers in enumerate(split):
        if layers <= 0:
            continue
        weight = (layers / total) * model.size_mb
        handicap = budget.first_gpu_handicap if gpus[i].first_in_class else 0
        free_after = (
            budget.per_gpu_free[i] - weight - budget.safety_margin - handicap
        )
        if free_after < 0:
            return False, (
                f"weights alone don't fit {gpu_label(gpus[i], i)}: "
                f"{int(weight)} MB weight + margin > "
                f"{budget.per_gpu_free[i]} MB free"
            )
    return True, "weights fit"


def _derive_reserved_split(
    base_split: tuple[float, ...],
    reserve_idxs: list[int],
    gpus: list[GPU],
    budget: Budget,
    model: Model,
    base_remaining_free: dict[str, int] | None = None,
) -> tuple[tuple[float, ...], dict[int, tuple[float, float]]]:
    """Leite den Split für reserve-belastete GPUs aus der verifizierten
    Basis ab — Prinzip der überlaufenden Gläser.

    Jede GPU in ``reserve_idxs`` (TTS- und/oder VLM-Side-Channel) verliert
    Layer proportional zu ihrem verbliebenen ``free/total``; der so
    verdrängte Layer-Anteil (``lost``) läuft in die Karten mit echtem
    Headroom über. Headroom = die REALE Rest-Kapazität nach dem base-Laden
    (``base_remaining_free`` je UUID, Gewicht + KV + Buffer schon abgezogen)
    minus first-GPU-Handicap. Fehlt die Messung (alter Cache), Rückfall auf
    ``free − Gewichtsanteil − Handicap`` — das ignoriert aber den
    ctx-abhängigen KV und kann eine bei hohem ctx randvolle RTX 8000
    überladen (sie zeigt leer viel „free"). Der Handicap deckt den
    ctx-unabhängigen Load-Peak der ersten Karte je Klasse (Output/Logits,
    Compute-Workspace, MTP-Draft-Buffer).

    Deckel (NUR im Fallback ohne Messung): Eine Karte, die in der Basis
    bereits die tightest ihrer Compute-Klasse ist UND Layer trägt
    (``first_in_class`` und base>0), gilt dort als greedy randvoll gepackt
    und nimmt KEINEN Spill auf — ein zusätzlicher Layer dort riskiert den
    Load-Peak, den nur die grobe Gewichts-Schätzung nicht sehen kann
    (Vigilantia-8B-Kombis: 16→17 auf der RTX 8000 = OOM). Mit echter
    ``base_remaining_free``-Messung entfällt der Deckel: Die Messung IST
    bereits der wahre Rest-Platz, first_in_class ist dort kein Proxy mehr
    nötig — und bei >1 Karte pro Klasse (z.B. 3× V100) ist first_in_class
    ohnehin nur ein UUID-Tiebreak beim initialen Enumerieren, kein
    Platz-Signal. Der Deckel hätte sonst beim 397B die V100 mit 15 GB
    Rest-Kapazität blockiert, nur weil sie zufällig als first_in_class
    markiert wurde, und den gesamten Überlauf auf eine RTX 8000 mit 2,6 GB
    gepresst (TTS×VLM-8B-Combo: physisch unmöglicher Split, Kalibration
    brach ab).

    Setup-agnostisch: kein Kartennamen-Heuristik, keine feste GPU-Position —
    nur ``first_in_class``, ``free_mb`` und ``base_split``. Die bisherige
    Ein-GPU-Ableitung (nur VLM) ist der Spezialfall ``len(reserve_idxs)==1``.

    Returns: (auf ganze Layer quantisierter Split, {reserve_idx:
    (ratio, new_layers)}) — das Dict nur fürs Logging.
    """
    adj = [float(x) for x in base_split]
    total_base = sum(adj) or 1.0
    reductions: dict[int, tuple[float, float]] = {}
    lost = 0.0
    for idx in reserve_idxs:
        if adj[idx] <= 0:
            continue
        ratio = gpus[idx].free_mb / float(gpus[idx].total_mb or 1)
        new_layers = adj[idx] * ratio
        lost += adj[idx] - new_layers
        adj[idx] = new_layers
        reductions[idx] = (ratio, new_layers)

    if lost > 0:
        reserve_set = set(reserve_idxs)
        targets: list[tuple[int, float]] = []
        _remaining = base_remaining_free or {}
        for i in range(len(gpus)):
            if i in reserve_set:
                continue
            has_measurement = gpus[i].uuid in _remaining
            # Deckel nur im Fallback (kein first_in_class-Bypass mehr, wenn
            # die echte Messung vorliegt — siehe Docstring).
            if not has_measurement and gpus[i].first_in_class and adj[i] > 0:
                continue
            handicap = (
                budget.first_gpu_handicap if gpus[i].first_in_class else 0
            )
            if has_measurement:
                # Reale Rest-Kapazität nach base-Laden (Gewicht + KV + Buffer
                # bereits abgezogen) — die genaue SSOT statt free − Gewicht.
                # Verhindert das Überladen einer RTX 8000, die bei hohem ctx
                # durch KV schon randvoll ist, aber leer viel "free" zeigt.
                headroom = float(_remaining[gpus[i].uuid] - handicap)
            else:
                # Fallback ohne base-Messung (Cache vor diesem Feld): grobe
                # Gewichts-Schätzung, ignoriert den ctx-abhängigen KV-Anteil.
                weight_mb = (adj[i] / total_base) * model.size_mb
                headroom = gpus[i].free_mb - weight_mb - handicap
            if headroom > 0:
                targets.append((i, headroom))
        total_headroom = sum(h for _, h in targets)
        if total_headroom > 0:
            for i, h in targets:
                adj[i] += lost * h / total_headroom
            # Headroom-bewusste Quantisierung. Es entstehen KEINE fraktionalen
            # Layer — llama.cpp platziert nur ganze; die Fraktionen sind reines
            # Rechen-Zwischenergebnis. Nur die Frage WELCHE Karte den
            # aufgerundeten Rest-Layer bekommt, richtet sich hier nach echtem
            # Platz statt nach dem Rundungsrest: Die generische
            # Largest-Remainder-Rundung vergibt den Rest an den größten
            # Nachkomma — das zwängt einer randvollen RTX 8000 einen Layer auf
            # (GPU2 17→18, obwohl remaining_free nur ~2 GB), während die freie
            # V100 (viel remaining_free) leer bleibt. Stattdessen: floor pro
            # Karte, dann jeden Rest-Layer per Water-Filling an das Spill-Ziel
            # mit dem aktuell meisten verbleibenden remaining_free.
            floors = [float(int(x)) for x in adj]
            remainder = model.total_layers - int(sum(floors))
            _target_idxs = [i for i, _ in targets]
            if remainder > 0 and _target_idxs and base_remaining_free:
                _cost = model.size_mb / model.total_layers
                _slack = {
                    i: base_remaining_free[gpus[i].uuid]
                    - max(0.0, floors[i] - base_split[i]) * _cost
                    for i in _target_idxs
                    if gpus[i].uuid in base_remaining_free
                }
                if _slack:
                    for _ in range(remainder):
                        _best = max(_slack, key=lambda k: _slack[k])
                        floors[_best] += 1.0
                        _slack[_best] -= _cost
                    return (tuple(floors), reductions)

    return (
        _quantize_split_to_layers(tuple(adj), model.total_layers),
        reductions,
    )


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


def _load_model_meta(model_id: str, gguf_path: Path) -> Model | None:
    native = get_gguf_native_context(gguf_path)
    total_layers = get_gguf_layer_count(gguf_path)
    if not native or not total_layers:
        return None
    size_mb = get_gguf_total_size(gguf_path) / (1024 ** 2)
    return Model(
        model_id=model_id,
        gguf_path=gguf_path,
        native_context=native,
        total_layers=total_layers,
        size_mb=size_mb,
        mb_per_layer=size_mb / total_layers,
        quantization=extract_quantization_from_filename(gguf_path.name),
    )


async def _find_speed_candidate(
    model: Model,
    gpus: list[GPU],
    budget: Budget,
    full_cmd: str,
    already_tried: list[Candidate],
    base_n_gpus: int,
    base_kv: str,
) -> Candidate | None:
    """Find a speed variant: fewer GPUs, same KV quality as base.

    Cascades through speed classes — first tries only the fastest
    class with the smallest ``n_gpus`` that reaches
    ``MIN_USEFUL_CONTEXT_TOKENS``. If nothing fits there, extends by
    GPUs from the next slower class, peu à peu, until a usable
    candidate appears or all classes are exhausted.

    Reuses already-projected cells from the base search when possible;
    runs new projections only for cells we haven't seen.

    Returns ``None`` early when base is already at or below
    ``find_min_gpus_for_weights`` — the model simply won't fit on fewer
    cards, no point trying.
    """
    min_gpus = find_min_gpus_for_weights(model.size_mb, gpus)
    if base_n_gpus <= min_gpus:
        return None

    # Cascade: start with the fastest speed class only; if no candidate
    # there reaches MIN_USEFUL_CONTEXT, extend by one class at a time
    # (peu à peu). Strictly fewer GPUs than the base in every stage —
    # otherwise the variant offers no speed-up.
    speed_classes = sorted({g.speed_class for g in gpus})
    if not speed_classes:
        return None

    prev_cumulative = 0
    for cls in speed_classes:
        cumulative = prev_cumulative + sum(1 for g in gpus if g.speed_class == cls)
        upper = min(cumulative, base_n_gpus - 1)
        # First stage: any n_gpus from the fastest class.
        # Later stages: must activate at least one GPU from the newly
        # added class, so lower = prev_cumulative + 1.
        lower = 1 if prev_cumulative == 0 else prev_cumulative + 1
        prev_cumulative = cumulative

        if upper < lower:
            continue

        for n in range(lower, upper + 1):
            # Re-use from base search if the same (n, kv) was projected
            cached = next(
                (c for c in already_tried
                 if c.n_gpus == n and c.kv_quant == base_kv),
                None,
            )
            if cached is not None:
                c: Candidate | None = cached
            else:
                c, _reason = await _project_cell(
                    model, gpus, budget, full_cmd, base_kv, n,
                )
            if c is None:
                continue
            if c.max_context >= MIN_USEFUL_CONTEXT_TOKENS:
                return c

    return None


# Stable marker inside _project_cell's failure reason: the cost model has
# proven that NO context (not even the minimum) fits this GPU set/budget.
# Callers use it to fail fast instead of burning minutes-long blind probes
# on a configuration the math has already ruled out.
_REASON_TOO_BIG = "model too big"


async def _project_cell(
    model: Model,
    gpus: list[GPU],
    budget: Budget,
    full_cmd: str,
    kv: str,
    active: list[int] | int,
) -> tuple[Candidate | None, str]:
    """Project one (active, kv) cell.

    ``active`` is the explicit list of CUDA ids participating, e.g.
    ``[1]`` for "use only CUDA1" or ``[0, 1]`` for "use CUDA0+CUDA1".
    For backward-compat with callers that just want "fastest N GPUs",
    pass an int ``n_gpus`` and it is interpreted as ``range(n_gpus)``.

    Returns ``(candidate, reason)``.  ``candidate`` is ``None`` on any
    failure; ``reason`` is a short label for the log so the caller can
    show exactly why a cell got skipped (fit-params error, model too big
    for this GPU count, …).
    """
    if isinstance(active, int):
        active = list(range(active))
    n_gpus = len(active)
    total_gpus = len(gpus)
    ctx_low = min(CALIBRATION_MIN_CONTEXT, model.native_context // 2) or 2048
    ctx_high = model.native_context

    seed = _seed_tensor_split(model.total_layers, active, gpus, budget)
    # Map seed back to GPU index space: seed is parallel to ``active``
    # (slot order), so seed[i] belongs on GPU ``active[i]``.
    _padded = [0.0] * total_gpus
    for _slot, _gpu_idx in enumerate(active):
        if _slot < len(seed):
            _padded[_gpu_idx] = float(seed[_slot])
    padded_seed = tuple(_padded)
    cmd = proj.adjust_cmd_for_projection(full_cmd, padded_seed, kv)

    # Pin fit-params to the same UUID order calibration uses, so the
    # CUDA indices it reports line up with our tensor-split positions.
    fit_env = {"CUDA_VISIBLE_DEVICES": cuda_visible_devices(gpus)}
    gpu_total_mb = tuple(g.total_mb for g in gpus)
    try:
        low = await proj.project(cmd, model.gguf_path, ctx_low, ngl=99,
                                 n_gpus=total_gpus, env_override=fit_env,
                                 gpu_total_mb=gpu_total_mb)
        high = await proj.project(cmd, model.gguf_path, ctx_high, ngl=99,
                                  n_gpus=total_gpus, env_override=fit_env,
                                  gpu_total_mb=gpu_total_mb)
    except proj.FitParamsError as e:
        logger.warning(f"fit-params failed (n_gpus={n_gpus}, kv={kv}): {e}")
        return None, f"fit-params error: {e}"

    try:
        vmodel = proj.fit_linear_model(
            low=low, high=high,
            n_gpus=n_gpus, kv_quant=kv, ngl=99,
            tensor_split=padded_seed,
        )
    except ValueError as e:
        return None, f"linear-model fit error: {e}"

    opt: OptResult = fill_fastest_first(
        model=vmodel,
        budget=budget,
        gpus=gpus,
        active_gpus=active,
        total_layers=model.total_layers,
        model_size_mb=model.size_mb,
        target_context=model.native_context,
    )

    # If not every layer fits at native context, the model's KV cache
    # at native would eat more VRAM than available.  Before giving up,
    # binary-search the largest context at which all layers *do* fit —
    # for very large models (≥ 80 % of total VRAM) that's the only
    # GPU-only path and is always better than hybrid mode.
    if not opt.reached_target:
        reduced = _max_ctx_where_all_layers_fit(
            vmodel=vmodel, budget=budget, gpus=gpus, active=active,
            total_layers=model.total_layers, model_size_mb=model.size_mb,
            ceiling=model.native_context,
        )
        if reduced is None or reduced.context < CALIBRATION_MIN_CONTEXT:
            # Probe-first (2026-07-07): bevor die Zelle mathematisch
            # stirbt, einmal OHNE safety_margin rechnen. Damit fällt nur
            # der künstliche Puffer weg — base_overhead, Handicap und
            # KV-Slope (gemessene Physik aus fit-params) bleiben in der
            # Rechnung. Jeder Caller PROBT einen Kandidaten real (Load +
            # Inferenz), bevor irgendetwas geschrieben wird; die Probe
            # ist die Wahrheit. Grund: die händisch verifizierten
            # Profile (397B: 141 MB frei) liegen bewusst unter der
            # Margin — die margin-behaftete Mathe kann sie nie finden.
            from dataclasses import replace as _budget_replace
            budget0 = _budget_replace(budget, safety_margin=0)
            zero = _max_ctx_where_all_layers_fit(
                vmodel=vmodel, budget=budget0, gpus=gpus, active=active,
                total_layers=model.total_layers,
                model_size_mb=model.size_mb,
                ceiling=model.native_context,
            )
            if (
                zero is not None
                and zero.reached_target
                and zero.context >= CALIBRATION_MIN_CONTEXT
            ):
                reduced = zero
        if reduced is None:
            placed = int(sum(opt.tensor_split))
            return None, (
                f"only {placed}/{model.total_layers} layers fit — "
                f"{_REASON_TOO_BIG} for {n_gpus} GPU(s) even at minimum "
                f"context"
            )
        # A reduced-context result with context == 0 means the binary
        # search landed on a split whose layer weights alone exceed a
        # GPU's budget — the "reached_target=True" path in
        # fill_fastest_first can report that when the overshoot
        # fallback crams layers onto an already-tight GPU.  Treat it as
        # a failure instead of propagating an unusable candidate.
        if reduced.context < CALIBRATION_MIN_CONTEXT:
            return None, (
                f"{_REASON_TOO_BIG} for {n_gpus} GPU(s) at KV={kv}: no "
                f"split leaves room for even the minimum context"
            )
        opt = reduced

    return Candidate(
        mode="gpu",
        n_gpus=n_gpus,
        kv_quant=kv,
        ngl=99,
        tensor_split=opt.tensor_split,
        max_context=opt.context,
        predicted_free_mb=opt.per_gpu_predicted_free_mb,
        vram_model=vmodel,
    ), "ok"


async def _vram_model_for_fixed_split(
    model: Model,
    gpus: list[GPU],
    full_cmd: str,
    kv: str,
    split: tuple[float, ...],
):
    """fit-params-Kostenmodell für einen KONKRETEN, bereits festgelegten Split.

    Anders als ``_project_cell`` (das den Split per fill_fastest_first neu
    optimiert und bei zu großem Modell aufgibt) misst dies GENAU den
    übergebenen Split — gebraucht von der proportionalen VLM-Ableitung,
    deren Split fest steht (17:18:8:9:9) und deren einzige freie Variable
    der Kontext ist. Mit dem Modell findet ``max_context_for_budget`` den
    passenden ctx analytisch, statt sich über Minuten-Load-Proben blind
    runterzutasten. Gibt ``None`` bei fit-params-Fehler zurück (Caller
    probt dann konservativ bei base_ctx mit ctx-shrink)."""
    cmd = proj.adjust_cmd_for_projection(full_cmd, split, kv)
    fit_env = {"CUDA_VISIBLE_DEVICES": cuda_visible_devices(gpus)}
    gpu_total_mb = tuple(g.total_mb for g in gpus)
    ctx_low = min(CALIBRATION_MIN_CONTEXT, model.native_context // 2) or 2048
    try:
        low = await proj.project(
            cmd, model.gguf_path, ctx_low, ngl=99, n_gpus=len(gpus),
            env_override=fit_env, gpu_total_mb=gpu_total_mb,
        )
        high = await proj.project(
            cmd, model.gguf_path, model.native_context, ngl=99,
            n_gpus=len(gpus), env_override=fit_env, gpu_total_mb=gpu_total_mb,
        )
    except proj.FitParamsError as e:
        logger.warning("VLM fixed-split fit-params failed: %s", e)
        return None
    try:
        return proj.fit_linear_model(
            low=low, high=high,
            n_gpus=len([x for x in split if x > 0]),
            kv_quant=kv, ngl=99, tensor_split=split,
        )
    except ValueError as e:
        logger.warning("VLM fixed-split model fit failed: %s", e)
        return None


def _max_ctx_where_all_layers_fit(
    vmodel,
    budget: Budget,
    gpus: list[GPU],
    active: list[int],
    total_layers: int,
    model_size_mb: float,
    ceiling: int,
) -> OptResult | None:
    """Binary-search the largest context where ``fill_fastest_first``
    can place every layer.

    For giant models the native context is unreachable because the KV
    cache alone would exceed available VRAM.  This function walks the
    context down to the largest multiple of the precision where every
    layer still has a home on the GPUs.  Returns ``None`` when even
    the minimum context can't hold the model — the caller then falls
    through to hybrid.
    """
    precision = LLAMACPP_CALIBRATION_PRECISION
    lo = CALIBRATION_MIN_CONTEXT
    hi = ceiling
    best: OptResult | None = None
    while lo <= hi:
        mid = ((lo + hi) // 2 // precision) * precision
        if mid < CALIBRATION_MIN_CONTEXT:
            break
        trial = fill_fastest_first(
            model=vmodel, budget=budget, gpus=gpus, active_gpus=active,
            total_layers=total_layers, model_size_mb=model_size_mb,
            target_context=mid,
        )
        # reached_target alone is NOT enough: the overshoot fallback in
        # fill_fastest_first can cram the last 1-2 layers onto an already
        # tight GPU — all layers "placed", but the split's real context
        # ceiling collapses (e.g. 3072 at a probed mid of 131k).  Without
        # the ceiling check the search keeps walking UP on such degenerate
        # trials and returns one of them as "best", failing models that
        # fit fine at lower contexts.
        if trial.reached_target and trial.context >= mid:
            best = trial
            lo = mid + precision
        else:
            hi = mid - precision
    return best


def _seed_tensor_split(
    total_layers: int,
    active_gpus: list[int],
    gpus: list[GPU],
    budget: Budget,
) -> list[int]:
    """Initial integer layer split proportional to (free − handicap)."""
    weights: list[float] = []
    for i in active_gpus:
        free = budget.per_gpu_free[i]
        if gpus[i].first_in_class:
            free = max(0, free - budget.first_gpu_handicap)
        weights.append(float(free))
    if sum(weights) <= 0:
        weights = [float(gpus[i].total_mb) for i in active_gpus]
    total_w = sum(weights)
    raw = [total_layers * w / total_w for w in weights]
    layers = [int(round(r)) for r in raw]
    diff = total_layers - sum(layers)
    if diff != 0:
        order = sorted(
            range(len(active_gpus)),
            key=lambda k: raw[k] - layers[k],
            reverse=(diff > 0),
        )
        step = 1 if diff > 0 else -1
        for k in order[: abs(diff)]:
            layers[k] += step
    return layers


# ═══════════════════════════════════════════════════════════════════
# Phase C/E: verification with at most one refinement round
# ═══════════════════════════════════════════════════════════════════

class _Done:
    """Sentinel yielded as the LAST item of ``_verify_and_refine`` so the
    caller can distinguish progress messages (str) from the final result.
    """
    __slots__ = ("result",)

    def __init__(self, result: Result | None):
        self.result = result


class _CtxSearchResult:
    """Sentinel yielded as the LAST item of :func:`_binary_search_fitting_ctx`
    so the caller can pull the outcome plus the running counters back out of
    the extracted search."""
    __slots__ = ("best_r", "best_ctx", "iteration", "thinks_seen")

    def __init__(
        self,
        best_r: "VerifyResult | None",
        best_ctx: int,
        iteration: int,
        thinks_seen: bool | None,
    ):
        self.best_r = best_r
        self.best_ctx = best_ctx
        self.iteration = iteration
        self.thinks_seen = thinks_seen


# ── Cross-variant bias cache (the "cross-engine derivation") ──────────────
# The fit-params-vs-real gap ("bias") is a property of the MODEL + HARDWARE —
# runtime buffers (compute/MTP/activation) that fit-params doesn't model — NOT
# of the per-engine TTS/VLM reserve (that's subtracted separately, on the
# reserve-adjusted measured free). So once ANY variant of a model measures the
# bias, every later variant on the same GPUs seeds its ctx search with it and
# trusts the cost model from probe 1 instead of re-learning it — the whole
# point of Point 2: engine 2/3 converge in ~2-3 probes instead of a full
# search, without ever blindly copying engine 1's ctx (each still recomputes
# its own max from its own reserve). Keyed by (model_id, gpu-uuids); ratchets
# to the max observed (conservative — too-high only costs a few bisections,
# never a wrong result); a pure hint, the search self-corrects if it's off.
# Process-local: a service restart (= fresh code/hardware) starts clean.
_MODEL_BIAS_CACHE: dict[tuple[str, tuple[str, ...]], int] = {}


def _bias_key(model: Model, gpus: list[GPU]) -> tuple[str, tuple[str, ...]]:
    return (model.model_id, tuple(g.uuid for g in gpus))


def _remember_bias(model: Model, gpus: list[GPU], bias_mb: int) -> None:
    key = _bias_key(model, gpus)
    if bias_mb > _MODEL_BIAS_CACHE.get(key, 0):
        _MODEL_BIAS_CACHE[key] = bias_mb


# Cache-Key: (tensor_split, ctx). One entry per physical model load during a
# single _verify_and_refine call.
ProbeCache = dict[tuple[tuple[float, ...], int], VerifyResult]


async def _load_and_cache(
    probe_cache: ProbeCache,
    split: tuple[float, ...],
    ctx: int,
    *,
    full_cmd: str,
    candidate: Candidate,
    port: int,
    gpus: list[GPU],
    budget: Budget,
    env: Optional[dict[str, str]],
    probe_thinking: bool,
) -> VerifyResult:
    """Physically probe ``(split, ctx)`` and record the result in
    ``probe_cache``.

    SSOT for the minutes-long model-loading probes of BOTH ctx searches (the
    downward :func:`_binary_search_fitting_ctx` and the upward push in
    :func:`_verify_and_refine`). The two phases binary-search ctx at the same
    fixed split and used to re-load the model for a value the other phase had
    already measured (~3 min each). The caller checks the cache first and only
    calls this on a miss, so a repeat is instant instead of a reload.
    """
    r = await verify(
        full_cmd=proj.adjust_cmd_for_projection(
            full_cmd, split, candidate.kv_quant,
        ),
        context=ctx, port=port, gpus=gpus,
        safety_margin_mb=budget.safety_margin,
        reserve_mb=budget.gpu_reserve_mb,
        ngl=candidate.ngl, env=env, probe_thinking=probe_thinking,
    )
    probe_cache[(split, ctx)] = r
    return r


def _known_ctx_ceiling(
    probe_cache: ProbeCache,
    split: tuple[float, ...],
    above_ctx: int,
    default: int,
) -> int:
    """Smallest already-probed ctx that did NOT fit at ``split`` above
    ``above_ctx`` — a proven ceiling for the upward search.

    At a fixed split the KV cache grows monotonically with ctx, so no ctx at
    or above a known failure can ever fit. Capping the upward window there
    skips a whole doomed climb (the TTS+VLM combo re-probing values the
    down-search already rejected). When the nearest reject sits one PRECISION
    above ``above_ctx`` the upward while-guard (``hi - lo > PRECISION``) is
    already false → the push runs zero probes. Returns ``default`` when no
    failure is known above ``above_ctx``.
    """
    return min(
        (c for (s, c), res in probe_cache.items()
         if s == split and c > above_ctx and not res.fits),
        default=default,
    )


async def _binary_search_fitting_ctx(
    *,
    current_split: tuple[float, ...],
    candidate: Candidate,
    model: Model,
    gpus: list[GPU],
    budget: Budget,
    full_cmd: str,
    port: int,
    env: Optional[dict[str, str]],
    probe_thinking: bool,
    thinks_seen: bool | None,
    status_prefix: str,
    lo: int,
    hi: int,
    iteration: int,
    initial_load_sig: tuple[int, ...] | None,
    probe_cache: ProbeCache,
    initial_bias_mb: int = 0,
) -> AsyncIterator:
    """Math-guided binary search for the highest ctx that fits at a FIXED
    split, in ``(lo, hi]``.

    SSOT for the post-shift ctx fallback of ALL variants (base, speed, VLM,
    best-effort). Math (:func:`_math_max_fitting_ctx`, ~ms, free) seeds the
    smartest ctx to probe; bias tracking adds the observed math-vs-real gap
    as an extra margin so the search jumps to a realistic ctx instead of
    crawling in 256-token steps; and a ctx-independent load-death signature
    stops it from re-running identical minutes-long crashes.

    Previously only the locked variants (speed/VLM) got this search — the
    base variant used 5 blind 10%-shrinks, whose crude, conservative anchor
    then forced the upward push (Step 3) to crawl back up over many probes
    (the giant-model slowdown). Unifying gives every variant a tight anchor
    from the cost model while keeping the exact 256-token end precision.

    Yields progress strings; the LAST item is a :class:`_CtxSearchResult`
    carrying ``best_r``/``best_ctx`` and the updated iteration/thinks_seen.
    """
    best_r: VerifyResult | None = None
    best_ctx = 0
    # Bias = how much the fit-params cost model OVER-predicts free VRAM vs. the
    # real load. Measured empirically it ranges from ~2 MB to ~1.5 GB depending
    # on model architecture (MoE/MTP runtime buffers), ctx and layer count — so
    # NO fixed cushion generalises. We track it adaptively and seed it from the
    # OOM that triggered this search (``initial_bias_mb``) AND from the
    # cross-variant cache (a prior variant of this model+hardware), whichever
    # is larger — so the math is trusted from probe 1 instead of re-learning
    # the gap (Point 2: engine 2/3 converge fast).
    math_bias_mb = max(initial_bias_mb, _MODEL_BIAS_CACHE.get(_bias_key(model, gpus), 0))
    # Only a real cost model makes the bias meaningful; without one the math
    # is a constant → keep bisecting.
    bias_measured = math_bias_mb > 0 and candidate.vram_model is not None
    # Math becomes unreliable after a probe crashed without leaving
    # measurement data (e.g. llama.cpp segfault on OOM — exit -11): we can't
    # update math_bias_mb, so the next math_max prediction would land within
    # one PRECISION of the failed value and crawl in 256-token decrements.
    # Force one true bisection step to escape that trap.
    math_unreliable = False
    # After MATH_OOM_GIVEUP math-driven OOMs in a row, stop trusting math for
    # the rest of the search — the bias model is broken in this region and we
    # just bisect (without this the loop oscillates math_max↔bisect, wasting
    # one probe per iteration).
    consecutive_math_oom = 0
    MATH_OOM_GIVEUP = 2
    # Pre-measurement trust floor: BEFORE we have any measured/seeded bias,
    # don't trust a math prediction whose raw free is below this — a high-bias
    # model would OOM on the first over-jump. Tied to the config safety margin,
    # NOT a magic constant. Once the bias IS known we trust the bias-adjusted
    # math down to the margin; the old fixed 512-MB floor made the search
    # bisect the whole way in the tight TTS regime even after the bias was
    # learned (150-200 MB free < 512 → always "too tight" → crawl).
    UNMEASURED_TRUST_FLOOR = 2 * budget.safety_margin
    # Load-death signature: when the server dies BEFORE getting ready
    # (measured empty, e.g. segfault at load), shrinking ctx re-runs the
    # identical load. If the next such death has the same per-GPU load minimum
    # the failure is ctx-independent and we stop.
    prev_load_sig = initial_load_sig
    while hi - lo > LLAMACPP_CALIBRATION_PRECISION:
        math_max, predicted_min = _math_max_fitting_ctx(
            current_split,
            lo + LLAMACPP_CALIBRATION_PRECISION,
            hi - LLAMACPP_CALIBRATION_PRECISION,
            candidate, model, gpus, budget,
            extra_safety_margin=math_bias_mb,
        )
        # Only distrust the math while the bias is still UNKNOWN. Once it has
        # been measured or seeded, ``predicted_min`` already carries it (via
        # extra_safety_margin in _math_max_fitting_ctx) and we trust it down to
        # the margin — no fixed floor.
        math_too_tight = (
            not bias_measured
            and math_max > lo
            and predicted_min < UNMEASURED_TRUST_FLOOR
        )
        math_burned_out = consecutive_math_oom >= MATH_OOM_GIVEUP
        math_usable = (
            math_max > lo
            and not math_unreliable
            and not math_too_tight
            and not math_burned_out
        )
        if math_usable:
            cand_ctx = math_max
            bias_note = f", bias +{math_bias_mb} MB" if math_bias_mb else ""
            src = f"math max → {predicted_min} MB free{bias_note}"
            used_math = True
        else:
            cand_ctx = ((lo + hi) // 2 // LLAMACPP_CALIBRATION_PRECISION) * LLAMACPP_CALIBRATION_PRECISION
            if cand_ctx <= lo or cand_ctx >= hi:
                break
            if math_burned_out:
                src = f"bisect (math gave up after {MATH_OOM_GIVEUP} OOMs)"
            elif math_too_tight:
                src = f"bisect (unmeasured bias, only {predicted_min} MB free predicted)"
            elif math_unreliable:
                src = "bisect (math unreliable after silent crash)"
            else:
                src = "bisect (math saw no fit)"
            used_math = False
        cached = probe_cache.get((current_split, cand_ctx))
        if cached is not None:
            r = cached
            yield (
                f"{status_prefix} ↺ ctx {format_number(cand_ctx)} "
                f"(range {format_number(lo)}–{format_number(hi)}, {src}) "
                f"— already probed ({'✓' if r.fits else '✗'}), skip reload"
            )
        else:
            iteration += 1
            yield (
                f"{status_prefix} 🧮 ctx {format_number(cand_ctx)} "
                f"(range {format_number(lo)}–{format_number(hi)}, {src}) "
                f"— probe..."
            )
            r = await _load_and_cache(
                probe_cache, current_split, cand_ctx,
                full_cmd=full_cmd, candidate=candidate, port=port,
                gpus=gpus, budget=budget, env=env,
                probe_thinking=probe_thinking and thinks_seen is None,
            )
            yield _fmt_verify(
                status_prefix, iteration, current_split, cand_ctx, r,
            )
        if r.thinks is not None:
            thinks_seen = r.thinks
        if r.fits:
            best_r = r
            best_ctx = cand_ctx
            lo = cand_ctx
            math_unreliable = False
            prev_load_sig = None
            if used_math:
                consecutive_math_oom = 0
        else:
            hi = cand_ctx
            if used_math:
                consecutive_math_oom += 1
            # Update math-vs-real bias if probe gave measurements
            if r.measured_free_mb:
                math_unreliable = False
                prev_load_sig = None
                # Only a real cost model gives a meaningful bias; without one
                # (_math_predicts_fit returns a constant) keep bisecting.
                if candidate.vram_model is not None:
                    bias_measured = True
                active_free_real = [
                    r.measured_free_mb[i] for i in range(len(current_split))
                    if i < len(r.measured_free_mb) and current_split[i] > 0
                ]
                if active_free_real:
                    real_min = min(active_free_real)
                    new_bias = max(0, predicted_min - real_min)
                    if new_bias > math_bias_mb:
                        yield (
                            f"{status_prefix} 🧮 math bias detected: "
                            f"predicted {predicted_min} MB vs real {real_min} MB "
                            f"→ bias +{new_bias} MB (was +{math_bias_mb} MB)"
                        )
                        math_bias_mb = new_bias
                        _remember_bias(model, gpus, new_bias)
            else:
                # Probe crashed silently (no measurement, likely SegFault on
                # OOM). Math has nothing to learn — force bisection next round;
                # and if the load dies IDENTICALLY regardless of ctx, stop.
                math_unreliable = True
                if (
                    prev_load_sig is not None
                    and r.load_min_free_mb == prev_load_sig
                ):
                    yield (
                        f"{status_prefix} load failure is ctx-independent "
                        f"(identical load minimum) — stopping ctx search"
                    )
                    break
                prev_load_sig = r.load_min_free_mb
    yield _CtxSearchResult(best_r, best_ctx, iteration, thinks_seen)


async def _verify_and_refine(
    candidate: Candidate,
    model: Model,
    gpus: list[GPU],
    budget: Budget,
    full_cmd: str,
    port: int,
    env: Optional[dict[str, str]],
    probe_thinking: bool,
    status_prefix: str,
    lock_active_gpus: bool = False,
    ctx_ceiling: Optional[int] = None,
    lock_split: bool = False,
):
    """Verify ``candidate``; refine split and context from measured VRAM.

    Yields streamed progress strings; the LAST item is a ``_Done`` carrying
    the final ``Result`` (or ``None`` if nothing fit).

    Structure:
      Step 1  First verify at candidate.max_context. On OOM, try up to 15
              fastest-first cascade shifts at that ctx (skipped for the
              VLM lock_split split); if still no fit, fall back to
              ``_binary_search_fitting_ctx`` — a cost-model-seeded binary
              search for the highest fitting ctx on the fixed split (SSOT
              for all variants).
      Step 2  Cheap cascade rebalance at the anchor ctx (only fires when a
              card is near-OOM).
      Step 3  Upward ctx push (binary search) toward the ceiling. On OOM at
              a probed ctx, ``_context_refine_swap`` relieves the context-
              limiting card onto the highest-ceiling destination (upstream
              allowed) before shrinking the search window.
    """
    current_split = candidate.tensor_split
    current_ctx = candidate.max_context
    iteration = 0
    seen_splits: set[tuple[float, ...]] = {current_split}
    last_good: tuple[VerifyResult, tuple[float, ...], int] | None = None
    # Probe cache (split, ctx) → result, shared across the down-search and the
    # upward push. Stops the two phases from re-loading the model (~3 min) for
    # a ctx one already measured, and its known failures cap the upward window
    # at a proven ceiling (_known_ctx_ceiling). Local to this call: the next
    # variant reserves different VRAM, so a fit here says nothing there.
    probe_cache: ProbeCache = {}

    # ── Step 1: first verify ───────────────────────────────────────
    iteration += 1
    r = await verify(
        full_cmd=proj.adjust_cmd_for_projection(
            full_cmd, current_split, candidate.kv_quant,
        ),
        context=current_ctx, port=port, gpus=gpus,
        safety_margin_mb=budget.safety_margin,
        reserve_mb=budget.gpu_reserve_mb,
        ngl=candidate.ngl, env=env, probe_thinking=probe_thinking,
    )
    probe_cache[(current_split, current_ctx)] = r
    yield (_fmt_verify(
        status_prefix, iteration, current_split, current_ctx, r,
    ))
    thinks_seen: bool | None = r.thinks

    if not r.fits:
        # OOM at native ctx → try LAYER SHIFTS first (keeps native ctx).
        # ctx-shrink is the LAST resort, used only after all 15 shift
        # attempts at native_ctx have been exhausted. Rationale: we want
        # max ctx with the fewest GPUs — redistributing layers preserves
        # both, while shrinking ctx loses the primary goal.
        #
        # Shifts sind für ALLE Varianten erlaubt — auch VLM/combo mit
        # proportional abgeleitetem Split. Der abgeleitete Split ist NICHT
        # immer optimal: der Reserve-Spill kann eine Nicht-reserve-Karte
        # überladen (RTX 8000 mit 18 Layern, 151 MB frei bei ctx 224k),
        # während eine idle V100 mit 17 GB danebensteht. Ein Shift der
        # überladenen Karte auf die freie rettet dann den vollen Kontext,
        # den der ctx-Shrink sonst opfern würde.
        #
        # Früher unterband ``lock_split`` (max_shifts=0) JEDEN Shift, weil
        # ein Blind-Shift mal einen Layer AUF die reserve-belastete VLM-GPU
        # schob (17:18:8:9:9 → 17:17:9:9:9). Das ist jetzt strukturell
        # verhindert: reserve-GPUs sind über ``_blocked_dest`` als Shift-
        # ZIEL ausgeschlossen (Blind- UND Smart-Pfad). Ein Layer auf einer
        # Side-Channel-GPU fräße genau den reservierten Container-Platz —
        # die Quelle darf jede Karte sein, nur das Ziel nie eine reserve.
        _blocked_dest = frozenset(
            i for i, m in enumerate(budget.gpu_reserve_mb) if m > 0
        )
        max_shifts = 15
        shift_attempt = 0
        while not r.fits and shift_attempt < max_shifts:
            # Smart shift if we have measurement data (server lived through
            # probe but tightest GPU fell below safety margin). Falls back to
            # blind shift when measurement is empty (server died at load —
            # that's a real OOM with no per-GPU info).
            shifted: tuple[float, ...] | None = None
            if r.measured_free_mb:
                # Point 1: at the FIRST OOM (with a steady-state measurement)
                # try the context-MAXIMIZING swap, not the fastest-first
                # cascade. It relieves the ctx-limiting card onto the
                # highest-ceiling card (upstream allowed) and so fixes a
                # ctx-suboptimal projected split RIGHT HERE — a fit at the
                # projected ctx then skips the whole down-search + climb-back
                # (the TTS variant's ~8 wasted probes). Only ceiling-raising +
                # reserve-adjusted measured free → never eats a side-channel
                # reserve, never degrades; keep_active_set honours speed/VLM.
                refined, _reason = _context_refine_swap(
                    current_split, gpus, r, budget,
                    vram_model=candidate.vram_model,
                    total_layers=model.total_layers,
                    model_size_mb=model.size_mb,
                    current_context=current_ctx,
                    keep_active_set=lock_active_gpus or lock_split,
                )
                if refined is not None:
                    shifted = refined
            if shifted is None:
                # Load-Frei um Side-Channel-Reserven bereinigen: die V100
                # zeigt beim Kalibrieren z.B. 14 GB "frei", die aber der
                # TTS-/VLM-Container später beansprucht (Reserve, hier noch
                # nicht physisch belegt). Ohne Abzug hielte der Shift die
                # Karte fälschlich für offen und schöbe immer wieder dorthin
                # (das "V100 -466 MB" im 122B-Combo-Log).
                _reserve = budget.gpu_reserve_mb
                _adj_free = tuple(
                    max(0, r.load_min_free_mb[i] - (_reserve[i] if i < len(_reserve) else 0))
                    for i in range(len(r.load_min_free_mb))
                )
                # Per-GPU-Kosten EINES Layers = Gewicht + KV bei diesem ctx.
                # Aus dem vram_model (falls vorhanden), sonst nur Gewicht.
                # Ohne den KV-Anteil hielte der Shift eine Karte mit z.B.
                # 4,9 GB frei für aufnahmefähig, die dann am KV eines ganzen
                # Layers (mehrere GB bei 262k) OOMt.
                _mb_per_layer = (
                    model.size_mb / model.total_layers
                    if model.total_layers else 0.0
                )
                if candidate.vram_model is not None:
                    from .optimizer import _per_gpu_coefficients
                    _, _slope = _per_gpu_coefficients(
                        candidate.vram_model, model.total_layers, model.size_mb,
                    )
                    _layer_cost = tuple(
                        _mb_per_layer + _slope[i] * current_ctx
                        for i in range(len(_slope))
                    )
                else:
                    _layer_cost = tuple(_mb_per_layer for _ in gpus)
                shifted = _shift_one_layer_blind(
                    current_split, gpus,
                    oom_cuda_id=r.oom_cuda_id,
                    keep_active_set=lock_active_gpus,
                    # Reserve-bereinigte Load-Messwerte + config-Mindest-
                    # reserve als Untergrenze: Der Layer geht auf die nächste
                    # Karte, die danach noch ≥ safety_margin frei hat — sonst
                    # die übernächste, bis ans Ende (Glas voll bis zur
                    # Reserve, nicht mehr).
                    free_estimate=_adj_free,
                    min_free_mb=budget.safety_margin,
                    layer_cost_per_gpu=_layer_cost,
                    blocked_dest=_blocked_dest,
                )
            if shifted is None:
                if lock_active_gpus:
                    yield (
                        f"{status_prefix} active set locked — no further "
                        f"layer shift possible without activating idle GPU"
                    )
                elif r.oom_cuda_id is None and r.measured_free_mb:
                    # "eff."-Pfad: der Server lief, unterschritt aber die
                    # Safety-Margin — die enge Karte IST aus den Messwerten
                    # bekannt (nur nicht als geparste stderr-OOM-Zeile). Der
                    # measurement-basierte Refine (oben) hat bereits mit voller
                    # Info kein Ziel gefunden; "not identifiable" wäre hier
                    # schlicht falsch (die eff.-Werte im Log zeigen die Karte).
                    yield (
                        f"{status_prefix} no further layer shift possible — "
                        f"measurement-based refine found no target within reserve"
                    )
                elif r.oom_cuda_id is None:
                    # Ohne geparste OOM-Karte UND ohne Messwerte shiftet der
                    # Blind-Shift bewusst nicht (falsche Quell-Karte wäre
                    # schlimmer als keine) — das ist KEIN "alle Ziele voll",
                    # sondern fehlende Info (z.B. Segfault exit -11 ohne
                    # OOM-Zeile im stderr).
                    yield (
                        f"{status_prefix} OOM GPU not identifiable from server "
                        f"log — cannot shift, falling back to ctx handling"
                    )
                else:
                    yield (
                        f"{status_prefix} no further layer shift possible at native ctx"
                    )
                break
            # Oszillations-Guard: measurement-based refine und blind shift
            # können gegeneinander arbeiten (refine schiebt A→B, blind
            # schiebt B→A), wenn die Zielkarte schon voll ist. Ohne diesen
            # Check pendelt der Loop bis max_shifts zwischen zwei längst
            # verworfenen Splits (397B: 64 min Leerlauf). Schon gesehener
            # Split → Shift-Phase beenden, auf ctx-shrink umsteigen.
            if shifted in seen_splits:
                yield (
                    f"{status_prefix} oscillation detected — "
                    f"{_split_str(shifted)} already tried, ending shift loop"
                )
                break
            seen_splits.add(shifted)
            shift_attempt += 1
            iteration += 1
            yield (
                f"{status_prefix} OOM at native — shift {shift_attempt}/{max_shifts}: "
                f"{_split_str(current_split)} → {_split_str(shifted)}"
            )
            # Pro Shift die Frei-Prognose je Karte zeigen — vorher sah man
            # nur die Split-Schieberei und nie, welche Karte wie eng wird.
            yield _planned_free_line(shifted, gpus, model.size_mb)
            current_split = shifted
            r = await verify(
                full_cmd=proj.adjust_cmd_for_projection(
                    full_cmd, current_split, candidate.kv_quant,
                ),
                context=current_ctx, port=port, gpus=gpus,
                safety_margin_mb=budget.safety_margin,
                reserve_mb=budget.gpu_reserve_mb,
                ngl=candidate.ngl, env=env,
                probe_thinking=probe_thinking and thinks_seen is None,
            )
            yield (_fmt_verify(
                status_prefix, iteration, current_split, current_ctx, r,
            ))
            if r.thinks is not None:
                thinks_seen = r.thinks

        # Shift-Loop-Ausgang: NUR wenn immer noch OOM (alle Shifts erschöpft
        # oder kein Shift mehr möglich) auf die math-geführte ctx-Suche
        # zurückfallen. Endete der Loop dagegen GRÜN bei native ctx, ist
        # native bereits die real verifizierte Obergrenze — dann KEINE
        # ctx-Suche: der konservative Kostenmodell-Bias würde sie sonst einen
        # Präzisions-Schritt (256 Tok) unter native starten lassen, dort grün
        # proben, und weil die Restlücke zu native == PRECISION ist, greift
        # der Aufwärts-Push (Step 3) nicht mehr (`hi - lo > PRECISION` ist
        # bei 256 > 256 falsch) → der bereits gemessene native-Erfolg wird
        # verworfen und ein unnötiger ~3-min-Probe verbrannt. Der erste Probe
        # oben überspringt den ganzen if-Block aus demselben Grund, wenn er
        # direkt grün ist — hier gilt dieselbe Logik nach dem Shift.
        if not r.fits:
            # SSOT for ALL variants (base, speed, VLM, best-effort): find the
            # highest fitting ctx down to MIN_USEFUL via a binary search the
            # cost model seeds; Step 3 then pushes it back up.
            init_load_sig = (
                r.load_min_free_mb if (not r.fits and not r.measured_free_mb)
                else None
            )
            # Seed the search's bias from the OOM we just measured, so the
            # math is trusted from probe 1 instead of bisecting until it
            # re-learns the gap (kills the fixed-512-floor crawl in the tight
            # TTS regime). Only when the failing probe left a steady-state
            # measurement and we have a model.
            init_bias = 0
            if r.measured_free_mb and candidate.vram_model is not None:
                _, _pred_min = _math_predicts_fit(
                    current_split, current_ctx, candidate, model, gpus, budget,
                )
                _active_real = [
                    r.measured_free_mb[i] for i in range(len(current_split))
                    if i < len(r.measured_free_mb) and current_split[i] > 0
                ]
                if _active_real:
                    init_bias = max(0, _pred_min - min(_active_real))
            search_res: _CtxSearchResult | None = None
            async for _sitem in _binary_search_fitting_ctx(
                current_split=current_split, candidate=candidate, model=model,
                gpus=gpus, budget=budget, full_cmd=full_cmd, port=port, env=env,
                probe_thinking=probe_thinking, thinks_seen=thinks_seen,
                status_prefix=status_prefix,
                lo=MIN_USEFUL_CONTEXT_TOKENS, hi=current_ctx,
                iteration=iteration, initial_load_sig=init_load_sig,
                initial_bias_mb=init_bias, probe_cache=probe_cache,
            ):
                if isinstance(_sitem, _CtxSearchResult):
                    search_res = _sitem
                else:
                    yield _sitem
            if search_res is not None:
                iteration = search_res.iteration
                thinks_seen = search_res.thinks_seen
                if search_res.best_r is not None:
                    r = search_res.best_r
                    current_ctx = search_res.best_ctx

            if not r.fits:
                yield _Done(None)
                return

    last_good = (r, current_split, current_ctx)

    # ── Step 2: keep refining split while it helps ─────────────────
    # Cheap safety rebalance at the fixed anchor ctx: the cascade's
    # 2×margin gate fires ONLY when a card is near-OOM, so at a comfortable
    # anchor this does no probes. The context-maximizing rebalance
    # (_context_refine_swap) lives in Step 3, where it triggers on the real
    # OOM as ctx is pushed up — running it here would chase a ceiling above
    # the useful ctx cap and burn probes for nothing.
    while True:
        refined, reason = _refine_split_from_measurement(
            current_split, gpus, r, budget,
            vram_model=candidate.vram_model,
            total_layers=model.total_layers,
            model_size_mb=model.size_mb,
            current_context=current_ctx,
        )
        if refined is None:
            # Always log why refinement stopped — makes it transparent
            # that the algorithm *did* consider rebalancing.
            yield (f"{status_prefix} balance check: {reason}")
            break
        if refined in seen_splits:
            yield (
                f"{status_prefix} split oscillation detected — keeping "
                f"{_split_str(current_split)}"
            )
            break
        seen_splits.add(refined)

        iteration += 1
        yield (
            f"{status_prefix} balance check: swap {reason} — "
            f"trying split {_split_str(refined)}"
        )
        r_new = await verify(
            full_cmd=proj.adjust_cmd_for_projection(
                full_cmd, refined, candidate.kv_quant,
            ),
            context=current_ctx, port=port, gpus=gpus,
            safety_margin_mb=budget.safety_margin,
            reserve_mb=budget.gpu_reserve_mb,
            ngl=candidate.ngl, env=env, probe_thinking=False,
        )
        yield (_fmt_verify(
            status_prefix, iteration, refined, current_ctx, r_new,
        ))
        if not r_new.fits:
            yield (f"{status_prefix} refinement OOM — keeping previous")
            break

        current_split = refined
        r = r_new
        last_good = (r, current_split, current_ctx)

    # ── Step 3: upward ctx push (binary search) ────────────────────
    # If the verified ctx is below native and the tightest GPU still has
    # plenty of headroom, try larger ctx values. Real measurement is more
    # generous than the math projection — this recovers ctx the projector
    # was too conservative about. Especially useful for the speed variant
    # whose target_ctx came from the n=2 math estimate.
    r, current_split, current_ctx = last_good
    # Upper cap for the upward push: by default native, but TTS variants
    # pass ``ctx_ceiling=base_ctx`` because going past base makes no sense
    # (TTS occupies VRAM → less free → can never hold more ctx than base).
    upward_ceiling = (
        min(ctx_ceiling, model.native_context) if ctx_ceiling
        else model.native_context
    )
    if (
        current_ctx < upward_ceiling
        and r.measured_free_mb
    ):
        active_free = [
            f for i, f in enumerate(r.measured_free_mb)
            if i < len(current_split) and current_split[i] > 0
        ]
        # Probe-first (2026-07-07): kein Headroom-Gate mehr. Früher lief
        # der Upward-Push nur bei > 2×safety_margin Luft auf der engsten
        # GPU — das nagelte das 397B auf 89k fest (CUDA1: 1833 MB), obwohl
        # real ~171k laufen. Die Binary-Search probt jetzt immer; die
        # Probes selbst sind die Kostenbremse und die Wahrheit.
        if active_free:
            lo = current_ctx
            # Cap the window at a proven ceiling: if a higher ctx already
            # failed at THIS split (Step 1's native OOM, or the down-search's
            # first reject above current_ctx), no ctx up to it can fit — KV
            # only grows. Without this the push re-climbs toward a doomed
            # upward_ceiling and re-probes rejected values (the TTS+VLM combo's
            # ~30-min crawl). When the reject sits one PRECISION above lo the
            # while-guard below is already false → zero wasted probes.
            hi = _known_ctx_ceiling(
                probe_cache, current_split, current_ctx, upward_ceiling,
            )
            iteration += 1
            yield (
                f"{status_prefix} headroom on tightest GPU "
                f"({min(active_free)} MB) — upward search to "
                f"{format_number(hi)}"
            )
            # Seed from the cross-variant cache — a prior variant of this
            # model+hardware already measured the bias, so trust the math from
            # probe 1. Without a cached value the floor below applies until the
            # first upward OOM measures it (same rule as the down-search).
            math_bias_mb = _MODEL_BIAS_CACHE.get(_bias_key(model, gpus), 0)
            bias_measured = math_bias_mb > 0 and candidate.vram_model is not None
            # See the downward search above for the rationale behind these
            # two guards (Fix C + D from the calibration audit).
            consecutive_math_oom = 0
            MATH_OOM_GIVEUP = 2
            UNMEASURED_TRUST_FLOOR = 2 * budget.safety_margin
            while hi - lo > LLAMACPP_CALIBRATION_PRECISION:
                math_max, predicted_min = _math_max_fitting_ctx(
                    current_split,
                    lo + LLAMACPP_CALIBRATION_PRECISION,
                    hi - LLAMACPP_CALIBRATION_PRECISION,
                    candidate, model, gpus, budget,
                    extra_safety_margin=math_bias_mb,
                )
                math_too_tight = (
                    not bias_measured
                    and math_max > lo
                    and predicted_min < UNMEASURED_TRUST_FLOOR
                )
                math_burned_out = consecutive_math_oom >= MATH_OOM_GIVEUP
                math_usable = (
                    math_max > lo
                    and not math_too_tight
                    and not math_burned_out
                )
                if math_usable:
                    cand_ctx = math_max
                    bias_note = f", bias +{math_bias_mb} MB" if math_bias_mb else ""
                    src = f"math max → {predicted_min} MB free{bias_note}"
                    used_math = True
                else:
                    cand_ctx = ((lo + hi) // 2 // LLAMACPP_CALIBRATION_PRECISION) * LLAMACPP_CALIBRATION_PRECISION
                    if cand_ctx <= lo or cand_ctx >= hi:
                        break
                    if math_burned_out:
                        src = f"bisect (math gave up after {MATH_OOM_GIVEUP} OOMs)"
                    elif math_too_tight:
                        src = f"bisect (unmeasured bias, only {predicted_min} MB free predicted)"
                    else:
                        src = "bisect (math saw no fit)"
                    used_math = False
                cached_up = probe_cache.get((current_split, cand_ctx))
                if cached_up is not None:
                    r_up = cached_up
                    yield (
                        f"{status_prefix} ↺ upward ctx "
                        f"{format_number(cand_ctx)} "
                        f"(range {format_number(lo)}–{format_number(hi)}, {src}) "
                        f"— already probed ({'✓' if r_up.fits else '✗'}), "
                        f"skip reload"
                    )
                else:
                    iteration += 1
                    yield (
                        f"{status_prefix} 🧮 upward ctx "
                        f"{format_number(cand_ctx)} "
                        f"(range {format_number(lo)}–{format_number(hi)}, {src}) "
                        f"— probe..."
                    )
                    r_up = await _load_and_cache(
                        probe_cache, current_split, cand_ctx,
                        full_cmd=full_cmd, candidate=candidate, port=port,
                        gpus=gpus, budget=budget, env=env, probe_thinking=False,
                    )
                    yield _fmt_verify(
                        status_prefix, iteration, current_split, cand_ctx, r_up,
                    )
                if r_up.thinks is not None:
                    thinks_seen = r_up.thinks
                if r_up.fits:
                    lo = cand_ctx
                    last_good = (r_up, current_split, cand_ctx)
                    if used_math:
                        consecutive_math_oom = 0
                else:
                    if used_math:
                        consecutive_math_oom += 1
                    # OOM at this ctx — before giving up on it, try a one-shot
                    # split refinement from the measurement: another active GPU
                    # may still have headroom that we can move layers onto.
                    # This is what saved the Qwen3-235B case where V100 had
                    # ~6 GB free while the RTX cards were tight.
                    refined_up: tuple[float, ...] | None = None
                    refined_up_split: tuple[float, ...] | None = None
                    refine_reason = ""
                    if r_up.measured_free_mb:
                        # Context objective for ALL variants incl. VLM: relieve
                        # the ctx-limiting card and move the layer to the
                        # highest-ceiling destination — even upstream onto a big
                        # card, the move the downstream-only cascade structurally
                        # cannot make (the 140.800-vs-114.944 Vigilantia gap,
                        # which IS the lock_split VLM variant 17:18:8:9:9).
                        #
                        # This only ever RAISES the measured ceiling, so it
                        # cannot reintroduce the 2026-07-07 VLM degradation the
                        # blanket lock guarded against: a ceiling-lowering swap
                        # (e.g. GPU1→reserve-loaded VLM GPU) is never selected,
                        # while the good GPU0→GPU2 move is. measured_free is
                        # already reserve-adjusted, so the reserve-loaded card
                        # shows its true (tight) headroom and is dodged by the
                        # ceiling math itself — no blanket lock needed.
                        #
                        # keep_active_set holds the VLM/speed GPU set fixed (no
                        # silent activation of an idle card mid-push).
                        refined_up_split, refine_reason = _context_refine_swap(
                            current_split, gpus, r_up, budget,
                            vram_model=candidate.vram_model,
                            total_layers=model.total_layers,
                            model_size_mb=model.size_mb,
                            current_context=cand_ctx,
                            keep_active_set=lock_active_gpus or lock_split,
                        )
                    if (
                        refined_up_split is not None
                        and refined_up_split not in seen_splits
                    ):
                        refined_up = refined_up_split
                        seen_splits.add(refined_up)
                        iteration += 1
                        yield (
                            f"{status_prefix} upward ctx {format_number(cand_ctx)} "
                            f"OOM — try refined split ({refine_reason}): "
                            f"{_split_str(current_split)} → {_split_str(refined_up)}"
                        )
                        r_re = await _load_and_cache(
                            probe_cache, refined_up, cand_ctx,
                            full_cmd=full_cmd, candidate=candidate, port=port,
                            gpus=gpus, budget=budget, env=env,
                            probe_thinking=False,
                        )
                        yield _fmt_verify(
                            status_prefix, iteration, refined_up, cand_ctx, r_re,
                        )
                        if r_re.fits:
                            current_split = refined_up
                            lo = cand_ctx
                            # ``hi`` was the OLD split's OOM ctx — it no longer
                            # bounds the NEW split, which relieved the limiter
                            # and can fit higher (its ceiling jumped, e.g. the
                            # VLM case 141k→209k). Reopen the upper bound to the
                            # ceiling so the search climbs the new split to its
                            # true edge instead of capping just under the stale
                            # ``hi`` (the 185.600-vs-~209k VLM undershoot). The
                            # reserve stays safe: the search runs on
                            # reserve-adjusted free and still stops at the margin.
                            hi = upward_ceiling
                            last_good = (r_re, current_split, cand_ctx)
                            continue
                        # Refined split also failed — fall through to
                        # math-bias update + hi shrink with r_up's data.
                    hi = cand_ctx
                    if r_up.measured_free_mb:
                        if candidate.vram_model is not None:
                            bias_measured = True  # trust bias-adjusted math
                        active_free_real = [
                            r_up.measured_free_mb[i] for i in range(len(current_split))
                            if i < len(r_up.measured_free_mb) and current_split[i] > 0
                        ]
                        if active_free_real:
                            real_min = min(active_free_real)
                            new_bias = max(0, predicted_min - real_min)
                            if new_bias > math_bias_mb:
                                yield (
                                    f"{status_prefix} 🧮 math bias detected: "
                                    f"predicted {predicted_min} MB vs real {real_min} MB "
                                    f"→ bias +{new_bias} MB (was +{math_bias_mb} MB)"
                                )
                                math_bias_mb = new_bias
                                _remember_bias(model, gpus, new_bias)
            r, current_split, current_ctx = last_good

    # Finaler Decken-Probe: Der Upward-Push (und die 256er-Bisect-Rundung)
    # lassen das Ergebnis genau PRECISION unter einem glatten Decken-
    # Vielfachen stehen — native (262.144) ist per Bisect unerreichbar, wenn
    # ``lo`` auf native−256 (261.888) landet (``hi-lo > PRECISION`` ist bei
    # 256>256 falsch, und der Bisect-Kandidat rundet auf ``lo`` zurück).
    # Sitzt der letzte grüne ctx nur EINEN Präzisions-Schritt unter der Decke
    # und wurde die Decke nie geprobt, probe sie einmal: der 256-Token-KV-
    # Zuwachs sind ein paar MB, passt fast immer und holt den vollen Kontext,
    # der sonst verschenkt würde (0,1 %, aber unlogisch). Nur EIN Probe, nur
    # wenn die Decke ohnehin schon fast erreicht ist.
    _up_ceiling = (
        min(ctx_ceiling, model.native_context) if ctx_ceiling
        else model.native_context
    )
    _lg_r, _lg_split, _lg_ctx = last_good
    if (
        _lg_r.fits
        and _lg_ctx < _up_ceiling
        and _up_ceiling - _lg_ctx <= LLAMACPP_CALIBRATION_PRECISION
    ):
        cached_ceil = probe_cache.get((_lg_split, _up_ceiling))
        if cached_ceil is not None:
            yield (
                f"{status_prefix} ↺ final ceiling {format_number(_up_ceiling)} "
                f"already probed ({'✓' if cached_ceil.fits else '✗'}) — skip"
            )
            if cached_ceil.fits:
                last_good = (cached_ceil, _lg_split, _up_ceiling)
        else:
            iteration += 1
            yield (
                f"{status_prefix} 🧮 final ceiling probe "
                f"{format_number(_up_ceiling)} (last good "
                f"{format_number(_lg_ctx)}, "
                f"gap {_up_ceiling - _lg_ctx} ≤ precision) — probe..."
            )
            r_ceil = await _load_and_cache(
                probe_cache, _lg_split, _up_ceiling,
                full_cmd=full_cmd, candidate=candidate, port=port,
                gpus=gpus, budget=budget, env=env, probe_thinking=False,
            )
            yield _fmt_verify(
                status_prefix, iteration, _lg_split, _up_ceiling, r_ceil,
            )
            if r_ceil.fits:
                last_good = (r_ceil, _lg_split, _up_ceiling)

    # Build the final result from the last successful run
    r_final, split_final, ctx_final = last_good
    final_candidate = Candidate(
        mode=candidate.mode,
        n_gpus=candidate.n_gpus,
        kv_quant=candidate.kv_quant,
        ngl=candidate.ngl,
        tensor_split=split_final,
        max_context=ctx_final,
        predicted_free_mb=candidate.predicted_free_mb,
        vram_model=candidate.vram_model,
    )
    if thinks_seen is not None:
        # Put the earliest thinking probe back into the verify result
        r_final = VerifyResult(
            fits=r_final.fits,
            measured_free_mb=r_final.measured_free_mb,
            thinks=thinks_seen,
            detail=r_final.detail,
        )
    result = _build_result(
        final_candidate, ctx=ctx_final, verify_r=r_final,
        num_active_gpus=_active_gpu_count(split_final),
    )
    yield _Done(result)
    return


def _refine_split_from_measurement(
    current_split: tuple[float, ...],
    gpus: list[GPU],
    verify_r: VerifyResult,
    budget: Budget,
    vram_model,
    total_layers: int,
    model_size_mb: float,
    current_context: int,
    keep_active_set: bool = False,
) -> tuple[tuple[float, ...] | None, str]:
    """Propose a whole-layer shift when an active GPU is near OOM.

    Returns ``(new_split, reason)``.  ``new_split`` is ``None`` when no
    downstream card can take the layer within the reserve; ``reason`` is a
    short human string the caller logs.

    Läuft, wenn der Server LÄDT, aber die knappste Karte < ``2 ×
    safety_margin`` frei hat. Die Zielwahl nutzt DIESELBE Glas-Kaskade wie
    der Blind-Shift (``_cascade_destination``, SSOT) — die nächste Karte
    nach dem Engpass, die den Layer samt KV noch ≥ ``safety_margin`` frei
    hält, sonst die übernächste bis ans Ende. FRÜHER wählte dieser Pfad
    statt der Kaskade das Ziel mit der "besten Balance" (höchstes Minimum
    über alle Karten) — eine zweite, widersprüchliche Verteil-Strategie im
    selben Kalibrierer, die Last auf langsame Karten legte und nicht
    randvoll packte. Jetzt fahren beide Pfade die Kaskade.
    """
    from .optimizer import _per_gpu_coefficients

    if not verify_r.measured_free_mb:
        return None, "no measurement"

    if vram_model is None:
        return None, "no vram model"

    active = [i for i, r in enumerate(current_split) if r > 0]
    if len(active) < 2:
        return None, "only one active GPU"

    active_free = [(i, verify_r.measured_free_mb[i]) for i in active
                   if i < len(verify_r.measured_free_mb)]
    if len(active_free) < 2:
        return None, "measurement short"
    active_free.sort(key=lambda t: t[1])
    bottleneck, b_free = active_free[0]

    if b_free >= 2 * budget.safety_margin:
        return None, (
            f"tightest GPU CUDA{bottleneck} has {b_free} MB free — "
            f"no OOM danger"
        )

    base_overhead, slope_per_layer = _per_gpu_coefficients(
        vram_model, total_layers, model_size_mb,
    )
    mb_per_layer = model_size_mb / total_layers if total_layers else 0.0

    # GANZE Layer (llama.cpp platziert nichts Feineres). Bedarfsgenau: so
    # viele ganze Layer, dass der Engpass über die 2×margin-Ruheschwelle
    # kommt — mindestens 1. Ein Layer trägt ~2,5 GB, ceil rundet höchstens
    # <1 Layer auf, überkorrigiert also nicht dramatisch; Kontext-Shrink
    # bleibt das LETZTE Mittel.
    import math as _math
    save_per_layer = mb_per_layer + slope_per_layer[bottleneck] * current_context
    deficit = 2 * budget.safety_margin - b_free
    if save_per_layer > 0:
        step = max(1.0, float(_math.ceil(deficit / save_per_layer)))
    else:
        step = 1.0
    # Nicht mehr verschieben als die Karte an ganzen Layern hat (sie darf
    # auf 0 = idle fallen, aber nicht negativ werden).
    step = min(step, float(int(current_split[bottleneck])))
    if step < 1.0:
        return None, f"CUDA{bottleneck} has no whole layer left to shift"

    # Zielwahl über die gemeinsame Glas-Kaskade (SSOT, identisch zum
    # Blind-Shift): nächste Karte nach dem Engpass, die den/die Layer samt
    # KV noch ≥ safety_margin frei hält. measured_free_mb ist in verify()
    # bereits reserve-bereinigt; die Layer-Kosten enthalten Gewicht + KV
    # bei diesem ctx (per-Karte-Slope aus dem vram_model).
    layer_cost = tuple(
        mb_per_layer + slope_per_layer[i] * current_context
        for i in range(len(gpus))
    )
    _blocked = frozenset(
        i for i, m in enumerate(budget.gpu_reserve_mb) if m > 0
    )
    dest = _cascade_destination(
        bottleneck, list(current_split), verify_r.measured_free_mb,
        layer_cost, budget.safety_margin, step, keep_active_set, _blocked,
    )
    if dest is None:
        return None, (
            f"CUDA{bottleneck} tight at {b_free} MB — no downstream card "
            f"holds {step:g} more layer(s) within reserve"
        )

    new_split = list(current_split)
    new_split[bottleneck] -= step
    new_split[dest] += step
    return (
        tuple(new_split),
        f"CUDA{bottleneck} ({b_free} MB) → CUDA{dest} ({step:g} layer, cascade)",
    )


def _context_refine_swap(
    current_split: tuple[float, ...],
    gpus: list[GPU],
    verify_r: VerifyResult,
    budget: Budget,
    vram_model,
    total_layers: int,
    model_size_mb: float,
    current_context: int,
    keep_active_set: bool = False,
) -> tuple[tuple[float, ...] | None, str]:
    """Single whole-layer swap that MAXIMIZES the analytical context ceiling.

    Used where the objective is maximum context (the upward ctx push), NOT
    fitting fastest-first. Unlike the cascade refine
    (:func:`_refine_split_from_measurement`), which relieves the card with
    the least *absolute* free VRAM and can only spill DOWNSTREAM, this:

      1. Relieves the CONTEXT-LIMITING card — the active GPU whose free VRAM
         runs out first as context grows (smallest ``free ÷ per-card
         KV-slope``). That is usually NOT the card with the least absolute
         free: a P40 at the cascade tail can sit at a few MB yet carry a
         shallow KV slope (few layers), so it limits nothing — which is why
         the cascade uselessly gave up on it.
      2. Moves the layer to whichever card yields the HIGHEST resulting
         context ceiling — ranked with the same cost model the optimizer
         uses (:func:`_context_ceiling_for_split`, SSOT), regardless of
         cascade position. This lets a layer move back UPSTREAM onto a big
         card with KV headroom, the move the downstream-only cascade
         structurally cannot make (the 140.800-vs-114.944 gap).

    Returns ``(new_split, reason)``; ``new_split`` is ``None`` when no swap
    raises the ceiling. Fully data-driven — limiting card and best
    destination both come from the measured VRAM model, so the same logic
    holds for any GPU mix (5 cards today, 7 tomorrow).
    """
    import math as _math

    from .optimizer import _per_gpu_coefficients

    if not verify_r.measured_free_mb:
        return None, "no measurement"
    if vram_model is None:
        return None, "no vram model"

    active = [i for i, r in enumerate(current_split) if r > 0]
    if len(active) < 2:
        return None, "only one active GPU"

    _, slope_per_layer = _per_gpu_coefficients(
        vram_model, total_layers, model_size_mb,
    )
    mb_per_layer = model_size_mb / total_layers if total_layers else 0.0
    measured = verify_r.measured_free_mb
    sm = budget.safety_margin

    def _free(i: int) -> float:
        return float(measured[i]) if i < len(measured) else 0.0

    # Additional context (tokens) a card can still absorb at its current
    # layer count and measured free, before hitting the safety margin.
    # Anchored to the MEASURED reality (post-load truth), not the pre-load
    # plan — that reality gap is exactly why we are in the upward push.
    def _card_ceiling(layers_i: int, free_i: float, i: int) -> float:
        kv_slope = layers_i * slope_per_layer[i]
        if layers_i <= 0 or kv_slope <= 0:
            return float("inf")
        return current_context + (free_i - sm) / kv_slope

    def _split_ceiling(layers: list[int], free: list[float]) -> float:
        return min(
            (_card_ceiling(layers[i], free[i], i) for i in range(len(layers))
             if layers[i] > 0),
            default=float("inf"),
        )

    # 1) Context-limiting card = the active GPU whose free VRAM runs out
    #    first as context grows (smallest free ÷ per-card KV-slope), among
    #    those still holding a whole layer to give away.
    def _ctx_headroom_tokens(i: int) -> float:
        kv_slope = current_split[i] * slope_per_layer[i]
        return _free(i) / kv_slope if kv_slope > 0 else float("inf")

    givers = [i for i in active if int(current_split[i]) >= 1]
    if not givers:
        return None, "no card has a whole layer to move"
    src = min(givers, key=_ctx_headroom_tokens)

    # Whole layers to lift ``src`` back above the 2×margin rest threshold
    # (mirrors the cascade refine's step so both paths move decisively).
    save_per_layer = mb_per_layer + slope_per_layer[src] * current_context
    deficit = 2 * sm - _free(src)
    if save_per_layer > 0 and deficit > 0:
        step = max(1, int(_math.ceil(deficit / save_per_layer)))
    else:
        step = 1
    step = min(step, int(current_split[src]))
    if step < 1:
        return None, f"CUDA{src} has no whole layer left to shift"

    cur_layers = [int(x) for x in current_split]
    cur_free = [_free(i) for i in range(len(current_split))]
    cur_ceiling = _split_ceiling(cur_layers, cur_free)

    # 2) Evaluate every destination; keep the one giving the highest ceiling.
    #    Moving ``step`` layers frees weight+KV on ``src`` and costs it on
    #    ``dest``; a dest that gets overloaded simply becomes the new limiter
    #    and scores a low ceiling, so feasibility is implicit — no separate
    #    reserve check, no cascade-position restriction (dest may sit
    #    upstream of src, the move the cascade structurally cannot make).
    cost_src = mb_per_layer + slope_per_layer[src] * current_context
    # Reserve-belastete Side-Channel-GPUs (TTS/VLM) nie als Ziel: ihr freier
    # Platz ist für den Container reserviert, nicht für Modell-Layer. Die
    # reserve-bereinigte measured_free würde sie zwar niedriger bewerten,
    # aber eine reserve-GPU mit wenigen Layern (flache KV-Slope) kann trotzdem
    # das höchste Ceiling scoren — hart ausschließen ist die klare Semantik.
    _blocked = frozenset(
        i for i, m in enumerate(budget.gpu_reserve_mb) if m > 0
    )
    best_dest: int | None = None
    best_ceiling = cur_ceiling
    for dest in range(len(current_split)):
        if dest == src:
            continue
        if dest in _blocked:
            continue
        if keep_active_set and current_split[dest] <= 0:
            continue
        cost_dest = mb_per_layer + slope_per_layer[dest] * current_context
        trial_layers = list(cur_layers)
        trial_layers[src] -= step
        trial_layers[dest] += step
        trial_free = list(cur_free)
        trial_free[src] += step * cost_src
        trial_free[dest] -= step * cost_dest
        c = _split_ceiling(trial_layers, trial_free)
        if c > best_ceiling:
            best_ceiling = c
            best_dest = dest

    if best_dest is None:
        headroom = _ctx_headroom_tokens(src)
        headroom_str = "∞" if headroom == float("inf") else str(int(headroom))
        return None, (
            f"CUDA{src} limits context ({headroom_str} tok headroom) but no "
            f"swap raises the {format_number(int(cur_ceiling))}-tok ceiling"
        )

    new_split = list(current_split)
    new_split[src] -= step
    new_split[best_dest] += step
    return (
        tuple(new_split),
        f"CUDA{src} (ctx-limiter) → CUDA{best_dest} ({step} layer, "
        f"ceiling {format_number(int(cur_ceiling))}→"
        f"{format_number(int(best_ceiling))} tok)",
    )


def _math_predicts_fit(
    split: tuple[float, ...],
    ctx: int,
    candidate: Candidate,
    model: Model,
    gpus: list[GPU],
    budget: Budget,
    extra_safety_margin: int = 0,
) -> tuple[bool, int]:
    """Math-only prediction whether ``(split, ctx)`` would fit.

    Returns ``(fits, min_active_free_mb)``. Uses the same fit-params VRAM
    model the optimizer used for the initial projection — cheap (no I/O),
    saves real probes during binary search by filtering hopeless ctx
    values before they cost 30-90 s each.

    ``extra_safety_margin`` adds an empirical safety buffer on top of
    ``budget.safety_margin`` — set this to the observed math-vs-real bias
    (predicted_free − measured_free) from a previous failed probe so the
    next math search picks a more conservative ctx.
    """
    if candidate.vram_model is None:
        # No VRAM model available (e.g. VLM-only candidate derived from
        # base_split without a fresh fit-params run).  Math prediction is
        # impossible — optimistically assume fit; the real probe decides.
        return (True, budget.safety_margin)
    from .optimizer import (
        _per_gpu_coefficients, _predicted_free, first_gpu_handicap_mb,
    )
    base, slope = _per_gpu_coefficients(
        candidate.vram_model, model.total_layers, model.size_mb,
    )
    mb_per_layer = model.size_mb / model.total_layers if model.total_layers else 0.0
    # Same model-derived handicap as fill_fastest_first (SSOT) — the math
    # filter must see the same number as the projection, or it green-lights
    # ctx values the optimizer already rejected (and vice versa).
    handicap = first_gpu_handicap_mb(
        candidate.vram_model, gpus, model.total_layers, model.size_mb, ctx,
    )
    extra_handicap = tuple(
        handicap if gpus[i].first_in_class else 0
        for i in range(len(gpus))
    )
    layers = [int(x) for x in split]
    free = _predicted_free(
        layers, ctx, base, slope, mb_per_layer, extra_handicap, budget,
    )
    active_free = [
        free[i] for i in range(len(layers))
        if i < len(free) and layers[i] > 0
    ]
    if not active_free:
        return False, 0
    min_free = min(active_free)
    threshold = budget.safety_margin + extra_safety_margin
    return (min_free >= threshold, min_free)


def _math_max_fitting_ctx(
    split: tuple[float, ...],
    lo: int,
    hi: int,
    candidate: Candidate,
    model: Model,
    gpus: list[GPU],
    budget: Budget,
    extra_safety_margin: int = 0,
) -> tuple[int, int]:
    """Math-only binary search for the highest ctx that fit-params predicts
    fits with ``split``, in the range ``[lo, hi]``.

    Returns ``(max_fitting_ctx, predicted_min_free_mb)``. ``max_fitting_ctx``
    is 0 if even ``lo`` doesn't fit. Pure math, no probes — runs in <100 ms
    for any range. Caller probes only the final result and shrinks ``hi``
    if the probe fails (math is sometimes too optimistic on MoE runtime
    activation memory).

    ``extra_safety_margin`` is forwarded to :func:`_math_predicts_fit` —
    pass the observed math-vs-real bias to make math conservative.
    """
    # Quick exits
    lo_ok, _ = _math_predicts_fit(
        split, lo, candidate, model, gpus, budget, extra_safety_margin,
    )
    if not lo_ok:
        return 0, 0
    hi_ok, hi_free = _math_predicts_fit(
        split, hi, candidate, model, gpus, budget, extra_safety_margin,
    )
    if hi_ok:
        return hi, hi_free

    # Bisect down from hi
    best_ctx = lo
    _, best_free = _math_predicts_fit(
        split, lo, candidate, model, gpus, budget, extra_safety_margin,
    )
    while hi - lo > LLAMACPP_CALIBRATION_PRECISION:
        mid = ((lo + hi) // 2 // LLAMACPP_CALIBRATION_PRECISION) * LLAMACPP_CALIBRATION_PRECISION
        if mid <= lo or mid >= hi:
            break
        ok, free = _math_predicts_fit(
            split, mid, candidate, model, gpus, budget, extra_safety_margin,
        )
        if ok:
            best_ctx = mid
            best_free = free
            lo = mid
        else:
            hi = mid
    return best_ctx, best_free


def _cascade_destination(
    src: int,
    layers: list[float],
    free_estimate: tuple[int, ...],
    layer_cost_per_gpu: tuple[float, ...],
    min_free_mb: int,
    step: float,
    keep_active_set: bool,
    blocked_dest: frozenset[int] = frozenset(),
) -> int | None:
    """Glas-Kaskade: die NÄCHSTE Karte nach ``src`` (in GPU-Listenreihenfolge
    = compute_cap DESC, total_mb DESC), die nach ``+step`` Layern noch
    ≥ ``min_free_mb`` frei bleibt — sonst die übernächste, bis ans Ende.
    ``None`` wenn keine Karte die Reserve hält.

    ``blocked_dest``: GPU-Indizes, die NIE Ziel sein dürfen — die
    reserve-belasteten TTS-/VLM-Side-Channel-GPUs. Ein Layer dort fräße
    genau den Platz, den der Container später beansprucht (das war die
    17:18:8:9:9 → 17:17:9:9:9-Degradation, die früher zum kompletten
    Shift-Verbot führte).

    SSOT der Zielwahl für BEIDE Shift-Pfade — den Blind-Shift (Load-OOM,
    Frei aus Load-Sampling) UND den Smart-Refine (geladen-aber-knapp, Frei
    aus Steady-State-Messung). Damit fahren beide dieselbe Kaskaden-
    Strategie (schnelle Karten randvoll bis zur Reserve, langsame erst bei
    Überlauf), egal welche Datenquelle das reserve-bereinigte Frei-Estimate
    liefert. Kosten pro Layer = Gewicht + KV bei diesem ctx
    (``layer_cost_per_gpu``), nicht nur Gewicht.

    ``keep_active_set``: idle Karten NICHT aktivieren (Speed-Variante).
    Ohne ``free_estimate``: Rückfall auf 'erste nachgelagerte (aktive) Karte'.
    """
    for i in range(src + 1, len(layers)):
        if i in blocked_dest:
            continue
        if keep_active_set and layers[i] <= 0:
            continue
        if free_estimate and i < len(free_estimate):
            cost = step * (
                layer_cost_per_gpu[i] if i < len(layer_cost_per_gpu) else 0.0
            )
            if free_estimate[i] - cost >= min_free_mb:
                return i
        elif layers[i] > 0 or not keep_active_set:
            # Keine Frei-Schätzung → erste nachgelagerte (aktive) Karte.
            return i
    return None


def _shift_one_layer_blind(
    split: tuple[float, ...],
    gpus: list[GPU],
    oom_cuda_id: int | None = None,
    keep_active_set: bool = False,
    free_estimate: tuple[int, ...] = (),
    min_free_mb: int = 0,
    layer_cost_per_gpu: tuple[float, ...] = (),
    blocked_dest: frozenset[int] = frozenset(),
) -> tuple[float, ...] | None:
    """Kaskaden-Spill: einen Layer-Anteil von der OOM-Karte nehmen und auf
    die NÄCHSTE nachgelagerte Karte legen, die danach noch über der
    config-Mindestreserve bleibt — sonst die übernächste, bis ans Ende
    (Glas-Kaskade: nächste Karte füllen, erst überlaufen lassen wenn sie
    die Reserve nicht mehr hält).

    Source: die tatsächliche OOM-Karte (``oom_cuda_id`` aus dem
    llama-server-stderr). Ohne diese Info kein Rateschluss → ``None``, der
    Caller geht auf ctx-Shrink.

    Destination (der eigentliche Fix): früher stur ``min(later_active)`` —
    die direkte Nachbarkarte, ungeachtet ob sie Platz hat. Beim 122B-Combo
    landete der Layer so auf der ebenfalls vollen GPU1, während die fast
    leere P40#4 (20 GB frei) danebenlag → OOM-Schleife ohne Konvergenz.
    Jetzt: die nächste Karte nach src, deren geschätzter Frei-Stand nach
    +STEP noch ≥ ``min_free_mb`` bleibt. ``free_estimate`` ist der tiefste
    Load-Frei-Stand pro Karte (aus dem Load-Sampling) — die einzige echte
    per-Karte-Info bei einem Load-OOM. Fehlt sie, Rückfall auf die alte
    Nachbar-Heuristik.

    ``keep_active_set=True`` (Speed-Variante) unterdrückt das Aktivieren
    bisher idler Karten.

    Returns ``None`` wenn kein Ziel die Reserve hält.
    """
    # GANZE Layer: llama.cpp platziert nur ganze Layer, ein halber Schritt
    # ist oft ein No-Op (rundet auf dieselbe Ganzzahl) und kostet nur einen
    # 125-GB-Reload. Ein ganzer Layer bewegt garantiert eine physische
    # Umverteilung. Splits sind ab hier ganzzahlig (die Ableitung
    # quantisiert), float nur zur Sicherheit erhalten.
    _STEP = 1.0
    layers = [float(x) for x in split]
    active_idx = [i for i, layers_i in enumerate(layers) if layers_i > 0]
    if not active_idx:
        return None

    # Take from the actually-OOM GPU (parsed from llama-server stderr).
    # Without that info we don't guess — return None so the caller falls
    # back to ctx-shrink instead of moving a layer off the wrong card.
    if (
        oom_cuda_id is not None
        and 0 <= oom_cuda_id < len(layers)
        and layers[oom_cuda_id] > 0
    ):
        src = oom_cuda_id
    else:
        return None

    if layers[src] <= _STEP:
        return None  # can't shift further

    # Zielwahl über die gemeinsame Kaskaden-SSOT (identisch zum Smart-Refine).
    dest = _cascade_destination(
        src, layers, free_estimate, layer_cost_per_gpu,
        min_free_mb, _STEP, keep_active_set, blocked_dest,
    )
    if dest is None:
        return None

    layers[src] -= _STEP
    layers[dest] += _STEP
    return tuple(layers)


def _shrink_to_fit(
    candidate: Candidate,
    gpus: list[GPU],
    budget: Budget,
    verify_r: VerifyResult,
    fallback_reduction: float = 0.1,
) -> int:
    """Compute a smaller context that should fit given the failure.

    If we have measurement data, we know exactly how many MiB we
    overshot on the tightest GPU.  Convert that into tokens via the
    model's slope.  Otherwise fall back to a fixed percentage shrink.
    """
    precision = LLAMACPP_CALIBRATION_PRECISION
    if verify_r.measured_free_mb:
        overshoot_mb = 0
        bottleneck_idx = -1
        for i, free in enumerate(verify_r.measured_free_mb):
            if i >= len(candidate.tensor_split) or candidate.tensor_split[i] == 0:
                continue
            short = budget.safety_margin - free
            if short > overshoot_mb:
                overshoot_mb = short
                bottleneck_idx = i
        if overshoot_mb > 0 and bottleneck_idx >= 0 and candidate.vram_model is not None:
            # Divide by the bottleneck GPU's slope, not the sum of all
            # slopes — the previous formula underestimated tokens_to_shed
            # by ~n_gpus×, so the next probe OOMed for the same reason.
            slopes = candidate.vram_model.slope_mb_per_tok
            bottleneck_slope = (
                slopes[bottleneck_idx]
                if bottleneck_idx < len(slopes)
                else sum(slopes)
            )
            if bottleneck_slope > 0:
                tokens_to_shed = int(overshoot_mb / bottleneck_slope * 1.1)
                new_ctx = candidate.max_context - tokens_to_shed
                new_ctx = max(0, int(new_ctx // precision) * precision)
                return new_ctx
    shrunk = int(candidate.max_context * (1 - fallback_reduction))
    return max(0, int(shrunk // precision) * precision)


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


# ═══════════════════════════════════════════════════════════════════
# Settings helpers
# ═══════════════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════════════
# AI calibration adapter
# ═══════════════════════════════════════════════════════════════════


def _ngl_from_cmd(cmd: str) -> int:
    m = re.search(r"-ngl\s+(\d+)", cmd)
    return int(m.group(1)) if m else 99


def _kv_quant_from_cmd(cmd: str) -> str:
    m = re.search(r"-ctk\s+(\S+)", cmd)
    return m.group(1) if m else "f16"


async def _try_ai_calibration(
    model_id: str,
    full_cmd: str,
    gguf_path: Path,
    safety_margin: int,
    port: int,
    env: Optional[dict[str, str]],
    model_size_mb: float,
    native_ctx: int,
    total_layers: int,
    config_path: Optional[Path],
    gpus: list[GPU],
    reserve_mb: tuple[int, ...] = (),
) -> AsyncIterator[str]:
    """Run the AI-agent calibration and translate its result protocol to
    the legacy ``__RESULT__:`` sentinel + on-disk config write.

    Yields progress lines. AI mode is terminal: on error it yields the
    honest failure sentinel ``__RESULT__:0:0:error`` (→ "Calibration
    failed" in the mixin). It NEVER falls back to the classic algorithm —
    the toggle picks one path, not a hybrid (CLAUDE.md: no fallbacks).
    """
    from .ai_agent import calibrate_with_ai

    # cloud_model=None → ai_agent reads provider + model from the calibration
    # system agent in agents.json (editable via the Agent Editor). The starting
    # ctx is found by the pre-search (fit-params from native ctx down), so
    # we don't seed a ctx here — only the capacity-based tensor split.
    seed_split = parse_tensor_split(full_cmd)

    ai_ctx: Optional[int] = None
    ai_split: Optional[list[float]] = None

    async for line in calibrate_with_ai(
        model_id=model_id,
        full_cmd=full_cmd,
        gguf_path=gguf_path,
        safety_margin_mb=safety_margin,
        seed_split=seed_split if seed_split else None,
        cloud_model=None,
        port=port,
        env=env,
        model_size_mb=model_size_mb,
        native_ctx=native_ctx,
        total_layers=total_layers,
        allow_hybrid=_hybrid_allowed_in_settings(),
        reserve_mb=reserve_mb,
    ):
        if line.startswith("__AI_RESULT__:"):
            payload = line.removeprefix("__AI_RESULT__:")
            parts = payload.split(":", 2)
            try:
                ai_ctx = int(parts[0])
                csv = parts[1]
                ai_split = [float(x) for x in csv.split(",") if x.strip()]
            except (ValueError, IndexError):
                yield f"⚠️ AI result unparseable: {payload[:80]}"
                yield "__RESULT__:0:0:error"
                return
            break
        if line.startswith("__AI_ERROR__:"):
            yield f"⚠️ {line.removeprefix('__AI_ERROR__:')}"
            yield "__RESULT__:0:0:error"
            return
        yield line

    if ai_ctx is None or ai_split is None:
        yield "__RESULT__:0:0:error"
        return

    ngl = _ngl_from_cmd(full_cmd)
    kv = _kv_quant_from_cmd(full_cmd)
    num_gpus = sum(1 for r in ai_split if r > 0)
    ts_colon = ":".join(str(int(round(r))) for r in ai_split)

    if config_path:
        result = Result(
            variant="base",
            mode="gpu",
            context=ai_ctx,
            ngl=ngl,
            kv_quant=kv,
            tensor_split=tuple(ai_split),
            num_gpus=num_gpus,
            thinks=True,  # not re-probed; reasoning is a runtime toggle
        )
        async for line in _write_base_config(config_path, model_id, result, gpus):
            yield line

    # ── Speed variant (AI mode) ──────────────────────────────────────
    # Without this the AI base returns early (caller returns on __RESULT__)
    # and Phase E never runs → no speed_split, so no variant-speed either.
    # _find_speed_candidate picks the smaller/faster GPU set deterministically
    # ("loop decides what"), then the AI optimizes ctx/split on exactly that
    # locked set ("AI decides how", as_speed=True → emits __SPEED__). Emitted
    # BEFORE the base __RESULT__ so the caller still sees it. A failed speed
    # variant must NEVER masquerade as / kill the base result.
    if num_gpus > 1:
        model_meta = _load_model_meta(model_id, gguf_path)
        if model_meta is not None:
            from dataclasses import replace as _replace
            speed_budget = build_budget(gpus, safety_margin=safety_margin)
            if reserve_mb and any(reserve_mb):
                speed_budget = _replace(speed_budget, gpu_reserve_mb=reserve_mb)
            speed_pick = await _find_speed_candidate(
                model_meta, gpus, speed_budget, full_cmd, [], num_gpus, kv,
            )
            if speed_pick is not None:
                speed_active = [
                    i for i, r in enumerate(speed_pick.tensor_split) if r > 0
                ]
                yield (
                    f"⚡ AI speed variant: {len(speed_active)} GPUs "
                    f"(vs {num_gpus} base) — optimizing on the faster set"
                )
                async for line in _ai_variant_from_base(
                    model=model_meta, gguf_path=gguf_path, full_cmd=full_cmd,
                    gpus=gpus, active=speed_active,
                    base_split=speed_pick.tensor_split,
                    base_ctx=native_ctx, base_kv=kv, budget=speed_budget,
                    port=port, env=env, known_thinking=True, as_speed=True,
                ):
                    if line.startswith("__RESULT__:"):
                        # speed branch failed (gate/ai-error) — keep base only.
                        yield "⚡ AI speed variant: no fit — keeping base only"
                        break
                    yield line

    yield (
        f"__RESULT__:{ai_ctx}:{ngl}:gpu:thinks:{kv}:{ts_colon}:{num_gpus}:"
        f"{_active_uuid_csv(tuple(ai_split), gpus)}"
    )


# ═══════════════════════════════════════════════════════════════════
# Config writers
# ═══════════════════════════════════════════════════════════════════

async def _write_base_config(
    config_path: Path, model_id: str, result: Result, gpus: list[GPU],
) -> AsyncIterator[str]:
    io.update_llamaswap_context(config_path, model_id, result.context)
    io.update_llamaswap_ngl(config_path, model_id, result.ngl)
    io.update_llamaswap_tensor_split(
        config_path, model_id, list(result.tensor_split),
    )
    # Result.tensor_split is parallel to the GPU list at calibration
    # time. Map back to UUIDs so the config pin is hardware-stable.
    all_uuids = [g.uuid for g in gpus]
    active_uuids = [
        gpus[i].uuid for i, v in enumerate(result.tensor_split)
        if i < len(gpus) and v > 0
    ]
    io.update_llamaswap_cuda_visible(
        config_path, model_id, active_uuids, all_uuids,
    )
    if result.kv_quant != "f16":
        io.update_llamaswap_kv_cache_quant(
            config_path, model_id, result.kv_quant,
        )
    else:
        io.remove_llamaswap_kv_cache_quant(config_path, model_id)
    yield f"Base config written: ctx={format_number(result.context)}, split={_split_str(result.tensor_split)}"


async def _write_speed_config(
    config_path: Path, model_id: str, result: Result,
) -> AsyncIterator[str]:
    split_colon = _split_str(result.tensor_split)
    io.add_llamaswap_speed_variant(
        config_path=config_path,
        model_id=model_id,
        speed_split_cuda0=0,  # legacy, unused when speed_layer_split given
        speed_split_rest=0,
        speed_context=result.context,
        num_gpus=result.num_gpus,
        kv_quant=result.kv_quant,
        speed_layer_split=split_colon,
    )
    yield f"Speed config written: ctx={format_number(result.context)}, split={split_colon}"


def _persist_cache(
    model: Model, result: Result, gpus: list[GPU],
    speed_result: Result | None = None,
) -> None:
    """Write the base result (and optional speed variant) to the persistent
    JSON cache.

    The UI reads ``speed_split`` from the cache to decide whether to show
    the Speed-Mode toggle. Writing it atomically here prevents the race
    where a follow-up calibration run (e.g. TTS variant) overwrites the
    cache before a separate ``update_llamacpp_speed_split`` call lands.
    """
    vram_per_gpu = ",".join(str(g.total_mb) for g in gpus)
    speed_split_cuda0 = 0
    if speed_result is not None and speed_result.tensor_split:
        layer_vals = [int(v) for v in speed_result.tensor_split]
        if layer_vals and layer_vals[0] > 0:
            speed_split_cuda0 = layer_vals[0]
    add_llamacpp_calibration(
        model_id=model.model_id,
        max_context=result.context,
        native_context=model.native_context,
        gguf_path=str(model.gguf_path),
        quantization=model.quantization,
        gpu_model=", ".join(g.name for g in gpus),
        model_size_gb=model.size_mb / 1024,
        ngl=result.ngl,
        mode=result.mode,
        speed_split=speed_split_cuda0,
        vram_per_gpu=vram_per_gpu,  # type: ignore[arg-type]
        gpu_uuids=[g.uuid for g in gpus],
        # Real leftover per card after the base loaded — the SSOT the
        # variant spill uses instead of the KV-blind ``free − weight``.
        remaining_free_mb=list(result.remaining_free_mb) or None,
    )
    # Patch in the rest of the speed details (rest_layers + ctx) — these
    # power the UI's "speed available" indicator and CUDA_VISIBLE_DEVICES.
    if speed_result is not None and speed_split_cuda0 > 0:
        from ..model_vram_cache import update_llamacpp_speed_split
        layer_vals = [int(v) for v in speed_result.tensor_split]
        rest = sum(layer_vals[1:]) if len(layer_vals) > 1 else 0
        update_llamacpp_speed_split(
            model.model_id,
            speed_split_cuda0,
            rest,
            speed_result.context,
        )


# ═══════════════════════════════════════════════════════════════════
# Hybrid fallback (reduce ngl to free GPU VRAM for more context)
# ═══════════════════════════════════════════════════════════════════

async def _calibrate_hybrid(
    model: Model,
    gpus: list[GPU],
    budget: Budget,
    full_cmd: str,
    port: int,
    env: Optional[dict[str, str]],
    known_thinking: Optional[bool],
    config_path: Optional[Path],
) -> AsyncIterator[str]:
    """Offload layers to CPU to free GPU VRAM for more context.

    Strategy: for each target context (descending from native), compute
    the smallest ``ngl`` whose fit-params projection still fits, then
    verify.  The old code did this with a binary search per target —
    we can derive ``ngl`` directly from the overrun::

        cpu_layers_needed = overrun_mb / mb_per_layer
        ngl = total_layers - cpu_layers_needed - safety
    """
    from ..gpu_utils import get_free_ram_mb

    # Cap the swap we will count as CPU-RAM headroom.  Enough to absorb
    # a load-time peak when the kernel swaps inactive pages out, but
    # not so much that we'd invite real inference thrashing.
    HYBRID_SWAP_BUDGET_MB = 4096

    def _get_free_swap_mb() -> int:
        """Free swap in MB — treated as a bounded extension of CPU-RAM
        headroom.  The system must still end up with at least
        ``MIN_FREE_RAM_MB`` of *real* RAM available after the model
        loads; the swap headroom only covers transient peaks.
        """
        try:
            import psutil
            return int(psutil.swap_memory().free / (1024 * 1024))
        except (ImportError, OSError):
            return 0

    yield "Entering hybrid mode (reducing ngl)..."
    targets = [c for c in (model.native_context, 131072, 65536, 32768, 16384)
               if c <= model.native_context]

    # Equal split used only for overrun measurement (ngl=99, all-GPU projection).
    # The split doesn't affect the summed overrun so 1:1:...:1 is fine here.
    ts_equal = tuple(float(1) for _ in range(len(gpus)))
    fit_env = {"CUDA_VISIBLE_DEVICES": cuda_visible_devices(gpus)}

    gpu_total_mb_t = tuple(g.total_mb for g in gpus)
    for target in targets:
        # Project oversize at ngl=99
        cmd_f16 = proj.adjust_cmd_for_projection(full_cmd, ts_equal, "f16")
        try:
            point = await proj.project(
                cmd_f16, model.gguf_path, target, ngl=99,
                env_override=fit_env, gpu_total_mb=gpu_total_mb_t,
            )
        except proj.FitParamsError:
            continue

        overrun = sum(
            max(0, used - (g.total_mb - budget.safety_margin))
            for used, g in zip(point.per_gpu_used_mb, gpus)
        )
        if overrun == 0:
            # Weirdly fits at ngl=99 — skip, GPU-only flow should have caught this
            continue

        cpu_layers = int(overrun / model.mb_per_layer * 1.15) + 1
        ngl = max(0, model.total_layers - cpu_layers)
        if ngl <= 0:
            yield f"Hybrid target {format_number(target)}: model too large even with ngl=0"
            continue

        cpu_ram_needed = int(cpu_layers * model.mb_per_layer * 1.1)
        free_ram = get_free_ram_mb() or 0
        swap_usable = min(_get_free_swap_mb(), HYBRID_SWAP_BUDGET_MB)
        # MIN_FREE_RAM_MB must remain free in *real* RAM after the
        # model is loaded — swap only widens the budget above that.
        available = free_ram + swap_usable
        if cpu_ram_needed > available - MIN_FREE_RAM_MB:
            swap_note = f" + {swap_usable} MB swap" if swap_usable else ""
            yield (
                f"Hybrid target {format_number(target)}: RAM insufficient "
                f"({cpu_ram_needed} MB needed, {free_ram} MB free{swap_note})"
            )
            continue

        # Dynamic split: distribute ngl GPU-layers proportional to free VRAM,
        # respecting speed classes and first-GPU handicap — same logic as the
        # GPU-only path.  Works for any hardware (1 GPU, 4 identical, mixed).
        seed = _seed_tensor_split(ngl, list(range(len(gpus))), gpus, budget)
        ts_ngl = tuple(float(x) for x in seed)

        yield f"Hybrid: ngl={ngl}, ctx={format_number(target)}, split={_split_str(ts_ngl)} — verifying..."
        r = await verify(
            full_cmd=proj.adjust_cmd_for_projection(full_cmd, ts_ngl, "f16"),
            context=target,
            port=port,
            gpus=gpus,
            safety_margin_mb=budget.safety_margin,
            reserve_mb=budget.gpu_reserve_mb,
            ngl=ngl,
            env=env,
            probe_thinking=known_thinking is None,
            health_timeout=LLAMACPP_HYBRID_HEALTH_TIMEOUT,
        )
        yield _fmt_verify("hyb", 1, ts_ngl, target, r)
        if r.fits:
            thinks = known_thinking if known_thinking is not None else bool(r.thinks)
            if config_path:
                io.update_llamaswap_context(config_path, model.model_id, target)
                io.update_llamaswap_ngl(config_path, model.model_id, ngl)
                io.update_llamaswap_tensor_split(
                    config_path, model.model_id, list(ts_ngl),
                )
                all_uuids = [g.uuid for g in gpus]
                io.update_llamaswap_cuda_visible(
                    config_path, model.model_id, all_uuids, all_uuids,
                )
                io.remove_llamaswap_kv_cache_quant(config_path, model.model_id)
            vram_per_gpu = ",".join(str(g.total_mb) for g in gpus)
            add_llamacpp_calibration(
                model_id=model.model_id,
                max_context=target,
                native_context=model.native_context,
                gguf_path=str(model.gguf_path),
                quantization=model.quantization,
                gpu_model=", ".join(g.name for g in gpus),
                model_size_gb=model.size_mb / 1024,
                ngl=ngl,
                mode="hybrid",
                vram_per_gpu=vram_per_gpu,  # type: ignore[arg-type]
                gpu_uuids=[g.uuid for g in gpus],
            )
            ts_csv = ",".join(f"{x:g}" for x in ts_ngl if x > 0)
            yield (
                f"__RESULT__:{target}:{ngl}:hybrid:"
                f"{'thinks' if thinks else 'nothink'}:f16:{ts_csv}:{len(gpus)}"
            )
            return

    yield "Hybrid: no configuration found"
    yield "__RESULT__:0:0:error"


# ═══════════════════════════════════════════════════════════════════
# TTS variant: derive from base + verify/refine (TTS-agnostic)
# ═══════════════════════════════════════════════════════════════════

@_tee_calibration_log
async def calibrate_tts_variant_from_base(
    *,
    model_id: str,
    gguf_path: Path,
    full_cmd: str,
    base_split: tuple[float, ...],
    base_ctx: int,
    base_kv: str,
    tts_gpu_uuid: Optional[str],
    port: int,
    env: Optional[dict[str, str]] = None,
    known_thinking: Optional[bool] = None,
    tts_gpu_extra_reserve_mb: int = 0,
    vlm_gpu_uuid: Optional[str] = None,
    vlm_gpu_extra_reserve_mb: int = 0,
    base_remaining_free: Optional[dict[str, int]] = None,
) -> AsyncIterator[str]:
    """Derive a TTS variant from an already-calibrated base config.

    Faster than a full re-calibration: assumes the base GPU set is the
    right one and only re-projects the same active set under the new
    free-VRAM picture (TTS already loaded → less free on the TTS GPU),
    then runs the standard verify/refine loop. Skips Phase 1's GPU-set
    enumeration entirely.

    The approach is TTS-agnostic — the function does not need to know
    which TTS backend is loaded or how much VRAM it takes. The caller
    starts the TTS service, ``enumerate_gpus()`` then sees the reduced
    free VRAM, and the projection adjusts the split accordingly.

    Yields:
      - human-readable progress messages
      - ``__RESULT__:`` sentinel on success
      - ``__RESULT__:0:0:error`` on failure (caller should fall back to
        a full re-calibration)

    Caller MUST start the TTS service before calling and stop it after.
    """
    # Re-enumerate GPUs *now*, with TTS already loaded. The TTS GPU's
    # free_mb will reflect whatever the running TTS container occupies.
    gpus = enumerate_gpus()
    if not gpus:
        yield "TTS variant: no GPUs detected"
        yield "__RESULT__:0:0:error"
        return

    # Optional: reserve extra VRAM on the TTS GPU on top of whatever the
    # container is currently consuming. Used for engines that allocate
    # dynamically during generate() (Qwen3-TTS grows from ~5 GB idle to
    # ~7 GB on a long bubble), so the LLM gets planned with a permanent
    # safety cushion instead of just the idle-measurement.
    # reserve_vec: Side-Channel-Reserven pro GPU — wandert ins Budget,
    # damit verify() sie auch von den Probe-Messwerten abzieht (die
    # Side-Channels sind während der Probes per Vertrag entladen).
    reserve_vec = [0] * len(gpus)
    if tts_gpu_extra_reserve_mb > 0:
        from dataclasses import replace
        adjusted = []
        for i, g in enumerate(gpus):
            if g.uuid == tts_gpu_uuid:
                old = g.free_mb
                new_free = max(0, g.free_mb - tts_gpu_extra_reserve_mb)
                yield (
                    f"TTS variant: reserving extra {tts_gpu_extra_reserve_mb} MB "
                    f"on {g.name} ({old} → {new_free} MB free) for TTS dynamic growth"
                )
                reserve_vec[i] += tts_gpu_extra_reserve_mb
                adjusted.append(replace(g, free_mb=new_free))
            else:
                adjusted.append(g)
        gpus = adjusted

    # VLM reserve — same rationale as in calibrate_llamacpp_model: the
    # configured VLM may be unloaded right now, but the next inference
    # call will pull its measured peak back into VRAM, so we plan around
    # it permanently.
    if vlm_gpu_extra_reserve_mb > 0 and vlm_gpu_uuid:
        from dataclasses import replace
        adjusted = []
        for i, g in enumerate(gpus):
            if g.uuid == vlm_gpu_uuid:
                old = g.free_mb
                new_free = max(0, g.free_mb - vlm_gpu_extra_reserve_mb)
                yield (
                    f"VLM reserve: holding {vlm_gpu_extra_reserve_mb} MB "
                    f"on {g.name} ({old} → {new_free} MB free)"
                )
                reserve_vec[i] += vlm_gpu_extra_reserve_mb
                adjusted.append(replace(g, free_mb=new_free))
            else:
                adjusted.append(g)
        gpus = adjusted

    model = _load_model_meta(model_id, gguf_path)
    if not model:
        yield f"TTS variant: could not load model meta for {model_id}"
        yield "__RESULT__:0:0:error"
        return

    # Probe-first: gleiche nackte Margin wie im Basis-Pfad — der
    # Vision-Bedarf kommt aus der 4K-Bild-Probe des Verifiers, nicht
    # aus einem Zuschlag.
    safety_margin = LLAMACPP_VRAM_SAFETY_MARGIN
    budget = build_budget(gpus, safety_margin=safety_margin)
    if any(reserve_vec):
        from dataclasses import replace as _replace
        budget = _replace(budget, gpu_reserve_mb=tuple(reserve_vec))

    # Map TTS UUID to position in our compute-DESC enumeration. When
    # called for a VLM-only variant (no TTS side-channel), tts_gpu_uuid
    # is None and we skip the position check entirely — the VLM reserve
    # block above already subtracted the cushion from the right GPU.
    if tts_gpu_uuid:
        tts_position = next(
            (i for i, g in enumerate(gpus) if g.uuid == tts_gpu_uuid), -1,
        )
        if tts_position < 0:
            yield (
                f"TTS variant: TTS GPU UUID {tts_gpu_uuid[:12]}… not visible — "
                f"falling back to full re-calibration"
            )
            yield "__RESULT__:0:0:error"
            return
    else:
        tts_position = -1

    # Active set = same GPUs as the base config. base_split is the full
    # tuple (length == total GPUs) with 0s on idle slots.
    active = [i for i, layers in enumerate(base_split) if layers > 0]
    if not active:
        yield "TTS variant: base split has no active GPUs"
        yield "__RESULT__:0:0:error"
        return
    if len(base_split) != len(gpus):
        yield (
            f"TTS variant: base split length {len(base_split)} ≠ visible "
            f"GPU count {len(gpus)} — falling back to full re-calibration"
        )
        yield "__RESULT__:0:0:error"
        return

    # ── Mode switch: AI vs. algorithm (no mixing — the toggle decides) ──
    # In AI mode the KI optimizes ctx/split for this cell instead of the
    # algorithmic _project_cell/_verify_and_refine below. The active set
    # (base_split>0) IS the hard GPU lock: calibrate_with_ai only ever sees
    # these cards, so a speed variant can't silently re-activate a slow one.
    from ..settings import load_settings as _load_cal_settings
    if str((_load_cal_settings() or {}).get("calibration_mode", "legacy")) == "ai":
        async for line in _ai_variant_from_base(
            model=model, gguf_path=gguf_path, full_cmd=full_cmd, gpus=gpus,
            active=active, base_split=base_split, base_ctx=base_ctx,
            base_kv=base_kv, budget=budget, port=port, env=env,
            known_thinking=known_thinking,
        ):
            yield line
        return

    # ── VLM/TTS variant: proportional derivation from base_split ─────────
    # _project_cell fails when an artificial side-channel reserve reduces a
    # reserved GPU's budget below the next integer-layer boundary.  The
    # conservative optimizer (safety_margin + first_gpu_handicap) then
    # assigns one fewer layer than the model can actually hold, leaving 2-4
    # layers unplaced at every context.  The binary search converges on an
    # overshoot result with context=0 and reports "no split leaves room for
    # even the minimum context" even though the model fits in practice.
    #
    # Fix: derive the tensor-split proportionally from the proven base_split
    # via _derive_reserved_split — the SSOT for "overflowing glasses".  It
    # relieves EVERY reserved GPU (VLM side-channel and, for combos, the TTS
    # GPU) proportionally and spills the freed layers onto the cards with
    # real headroom, capping the randvoll top-of-class cards so the
    # ctx-independent load peak can't OOM them (16→17 on CUDA0).  Setup-
    # agnostic: no card-name heuristics, no fixed GPU count — only
    # first_in_class / free_mb / total_mb / base_split.  The old single-GPU
    # derivation is the len(reserve_idxs)==1 special case.
    if vlm_gpu_uuid and vlm_gpu_extra_reserve_mb > 0:
        _vlm_idx = next(
            (i for i, g in enumerate(gpus) if g.uuid == vlm_gpu_uuid), -1
        )
        if _vlm_idx >= 0 and float(base_split[_vlm_idx]) > 0:
            # Reserve-belastete GPUs: der VLM-Side-Channel plus — bei
            # Kombis — die TTS-GPU. Beide werden in _derive_reserved_split
            # SYMMETRISCH proportional entlastet; der Überlauf fließt per
            # Wasserfall auf die Karten mit echtem Headroom (Deckel schützt
            # die randvolle Spitze jeder Compute-Klasse vor dem Load-Peak).
            # Vorher wurde NUR die VLM-GPU entlastet — die TTS-GPU behielt
            # ihren base-Anteil und sprengte bei großen TTS-Reserven
            # (Fish-Speech 26 GB) den weights_fit, obwohl die idle
            # Kaskaden-Karte leer danebenstand.
            _reserve_idxs = [_vlm_idx]
            if (
                tts_gpu_uuid
                and tts_gpu_extra_reserve_mb > 0
                and tts_position >= 0
                and float(base_split[tts_position]) > 0
            ):
                _reserve_idxs.append(tts_position)
            _adj_t, _reductions = _derive_reserved_split(
                base_split, _reserve_idxs, gpus, budget, model,
                base_remaining_free=base_remaining_free,
            )
            _adj = list(_adj_t)
            _total_split = sum(_adj) or 1.0
            _pred_free = tuple(
                int(gpus[i].free_mb - (_adj[i] / _total_split) * model.size_mb)
                for i in range(len(gpus))
            )
            _active_adj = [i for i, x in enumerate(_adj) if x > 0]
            # vram_model nachrüsten: Ohne Kostenmodell sind der
            # messungsbasierte Smart-Refine und der Math-Vorfilter im
            # Verify tot — bei OOM blieben nur Blind-Shifts (je Versuch ein
            # Minuten-Modell-Load). _project_cell läuft hier NUR als
            # Modell-Lieferant. Liefert die Projektion nichts, läuft der
            # Verify wie bisher ohne Modell — das wird geloggt, nicht
            # verschluckt.
            _side = "TTS+VLM" if tts_gpu_uuid else "VLM"
            # Der abgeleitete Split (_adj) steht fest und ist optimal — er
            # darf NICHT durch den Shift-Loop verschlechtert werden (der
            # würde einen Layer auf eine reserve-belastete GPU schieben).
            # Nur der Kontext ist die freie Variable. Zwei Gates:
            #   1. Gewichts-Check: passt schon das reine Gewicht nicht, ist
            #      der Split physikalisch unmöglich → skip.
            #   2. Kostenmodell für GENAU diesen Split (fit-params) →
            #      analytischer max ctx. Startet die Probe realistisch statt
            #      an der geerbten Base, wo der Load-KV die Karten sprengt.
            _wfit, _wreason = _weights_fit(tuple(_adj), gpus, budget, model)
            if not _wfit:
                yield (
                    f"{_side} variant infeasible ({_wreason}) — "
                    f"skipping probes"
                )
                yield "__RESULT__:0:0:error"
                return
            _vm = await _vram_model_for_fixed_split(
                model, gpus, full_cmd, base_kv, tuple(_adj),
            )
            _start_ctx = base_ctx
            if _vm is not None:
                _gpu_total = tuple(g.total_mb for g in gpus)
                _baseline = tuple(
                    _gpu_total[i] - gpus[i].free_mb for i in range(len(gpus))
                )
                _handi = tuple(
                    budget.first_gpu_handicap if gpus[i].first_in_class else 0
                    for i in range(len(gpus))
                )
                _fit_ctx, _pred_min = proj.max_context_for_budget(
                    _vm, _gpu_total, _baseline, _handi,
                    budget.safety_margin, ceiling=base_ctx,
                )
                if _fit_ctx < CALIBRATION_MIN_CONTEXT:
                    yield (
                        f"{_side} variant infeasible: derived split "
                        f"{_split_str(tuple(_adj))} max ctx {_fit_ctx} "
                        f"< minimum — skipping probes"
                    )
                    yield "__RESULT__:0:0:error"
                    return
                _start_ctx = _fit_ctx
                yield (
                    f"{_side} derived split {_split_str(tuple(_adj))} → "
                    f"cost-model max ctx {format_number(_fit_ctx)} "
                    f"(pred. {_pred_min} MB free on tightest) — probing "
                    f"split-locked"
                )
            else:
                yield (
                    f"{_side} derived split {_split_str(tuple(_adj))} — "
                    f"no cost model, probing at base ctx with ctx-shrink"
                )
            _vlm_cand = Candidate(
                mode="gpu",
                n_gpus=len(_active_adj),
                kv_quant=base_kv,
                ngl=99,
                tensor_split=tuple(_adj),
                max_context=_start_ctx,
                predicted_free_mb=_pred_free,
                vram_model=_vm,
            )
            _newly_active = [i for i in _active_adj if i not in active]
            _reduction_str = "; ".join(
                f"{gpu_label(gpus[_ri], _ri)} "
                f"{float(base_split[_ri]):.1f}→{_rnew:.1f} (ratio {_rr:.3f})"
                for _ri, (_rr, _rnew) in _reductions.items()
            )
            yield (
                f"{_side} variant from base: active GPUs "
                f"[{format_gpu_positions(_active_adj, gpus)}]"
                + (f" (idle spill → [{format_gpu_positions(_newly_active, gpus)}])"
                   if _newly_active else "")
                + f", start ctx {format_number(_start_ctx)}, KV={base_kv}, "
                f"reserve offload {_reduction_str}"
            )
            yield _format_candidate_line(_vlm_cand, gpus)
            _vlm_result: Result | None = None
            async for _item in _verify_and_refine(
                _vlm_cand, model, gpus, budget, full_cmd, port, env,
                probe_thinking=(known_thinking is None),
                status_prefix=f"[vlm/{base_kv}]",
                ctx_ceiling=base_ctx,
                lock_split=True,
            ):
                if isinstance(_item, _Done):
                    _vlm_result = _item.result
                else:
                    yield _item
            if _vlm_result is None:
                yield f"{_side} variant: derived config does not fit"
                yield "__RESULT__:0:0:error"
                return
            _thinks = (
                known_thinking
                if known_thinking is not None
                else _vlm_result.thinks
            )
            yield _result_sentinel(_vlm_result, bool(_thinks), gpus)
            return

    if tts_position >= 0:
        # Label the TTS card with its nvidia-smi index (SSOT gpu_uuid_labels)
        # instead of the compute-sorted list position — the two disagree for
        # same-compute cards (3× V100 tie-break by UUID here, by index in
        # nvidia-smi), so "GPU3" here was physically the card nvidia-smi calls
        # GPU1. Same anchor as the llama-swap config comments. Falls back to
        # the positional index + name when nvidia-smi is unavailable.
        _labels = gpu_uuid_labels()
        _tts_gpu = gpus[tts_position]
        _tts_label = gpu_label(_tts_gpu, tts_position, _labels)
        yield (
            f"TTS variant from base: active GPUs "
            f"[{format_gpu_positions(active, gpus, _labels)}], target ctx "
            f"{format_number(base_ctx)}, KV={base_kv}, free now "
            f"{format_number(total_free_mb(gpus))} MB "
            f"(TTS on {_tts_label}: "
            f"{format_number(_tts_gpu.free_mb)} MB free)"
        )
    else:
        yield (
            f"Variant from base: active GPUs "
            f"[{format_gpu_positions(active, gpus)}], target ctx "
            f"{format_number(base_ctx)}, KV={base_kv}, free now "
            f"{format_number(total_free_mb(gpus))} MB"
        )

    # Re-project the same active set with current (TTS-aware) free VRAM.
    # This produces a fresh Candidate (with vram_model) that the verify/
    # refine loop can use for refine measurements.
    candidate, reason = await _project_cell(
        model, gpus, budget, full_cmd, base_kv, active,
    )
    if candidate is None:
        yield f"TTS variant: projection failed ({reason})"
        yield "__RESULT__:0:0:error"
        return
    yield _format_candidate_line(candidate, gpus)

    # Cap target ctx at base_ctx — there's no point chasing more context
    # than the base config achieved (TTS only takes VRAM away, never adds).
    target_ctx = min(candidate.max_context, base_ctx)
    if target_ctx < MIN_USEFUL_CONTEXT_TOKENS:
        yield (
            f"TTS variant: projected max_ctx {format_number(candidate.max_context)} "
            f"too small to be useful"
        )
        yield "__RESULT__:0:0:error"
        return

    # Build a target-ctx candidate so verify+refine uses base_ctx as the
    # ceiling, not the (possibly larger) projected max_context.
    candidate_at_target = Candidate(
        mode=candidate.mode,
        n_gpus=candidate.n_gpus,
        kv_quant=candidate.kv_quant,
        ngl=candidate.ngl,
        tensor_split=candidate.tensor_split,
        max_context=target_ctx,
        predicted_free_mb=candidate.predicted_free_mb,
        vram_model=candidate.vram_model,
    )

    result: Result | None = None
    async for item in _verify_and_refine(
        candidate_at_target, model, gpus, budget, full_cmd, port, env,
        probe_thinking=(known_thinking is None),
        status_prefix=f"[tts/{base_kv}]",
        ctx_ceiling=base_ctx,
    ):
        if isinstance(item, _Done):
            result = item.result
        else:
            yield item

    if result is None:
        yield "TTS variant: verify/refine did not find a fitting config"
        yield "__RESULT__:0:0:error"
        return

    thinks = known_thinking if known_thinking is not None else result.thinks
    yield _result_sentinel(result, bool(thinks), gpus)


async def _ai_variant_from_base(
    *,
    model: "Model",
    gguf_path: Path,
    full_cmd: str,
    gpus: list[GPU],
    active: list[int],
    base_split: tuple[float, ...],
    base_ctx: int,
    base_kv: str,
    budget: "Budget",
    port: int,
    env: Optional[dict[str, str]],
    known_thinking: Optional[bool],
    as_speed: bool = False,
) -> AsyncIterator[str]:
    """AI calibration of one variant cell, restricted to the active GPU set.

    The ``active`` set (base_split>0) is at once the hard speed lock:
    ``calibrate_with_ai`` only ever sees ``gpus_active`` (reduced list +
    matching CUDA_VISIBLE_DEVICES), so it can't re-activate a deactivated
    card. Yields the same ``__RESULT__:``/progress sentinels as the
    algorithmic path; on AI failure it yields ``__RESULT__:0:0:error``
    (no algorithmic fallback — the toggle picked AI).

    Index-safety: gpus/reserve/seed are all sliced by the same ``active``
    indices, and the result split is reported as the active (>0) values in
    that same order — identical to ``_result_sentinel``'s contract.
    """
    from .ai_agent import calibrate_with_ai
    from .gpu import cuda_visible_devices

    gpus_active = [gpus[i] for i in active]
    reserve_active = (
        tuple(budget.gpu_reserve_mb[i] for i in active)
        if budget.gpu_reserve_mb else ()
    )
    seed_active = [base_split[i] for i in active]

    # Speed/feasibility gate (cheap, no probes): does the model WEIGHT even
    # fit on the active cards after subtracting the side-channel reserve?
    # If not, the reduced (speed) set is too small — skip without burning a
    # single AI probe. (KV-cache + compute come on top; the AI's own
    # fit-params pre-search rejects those tighter cases.)
    usable_mb = sum(g.free_mb for g in gpus_active) - sum(reserve_active)
    if usable_mb < model.size_mb:
        yield (
            f"AI variant: model weight {format_number(model.size_mb)} MB "
            f"exceeds usable VRAM on the active set "
            f"({format_number(usable_mb)} MB) — not feasible, skipping"
        )
        yield "__RESULT__:0:0:error"
        return
    env_active = {
        **(env or {}),
        "CUDA_VISIBLE_DEVICES": cuda_visible_devices(gpus_active),
    }

    ai_ctx: Optional[int] = None
    ai_split: Optional[list[float]] = None
    async for line in calibrate_with_ai(
        model_id=model.model_id,
        full_cmd=full_cmd,
        safety_margin_mb=budget.safety_margin,
        gguf_path=gguf_path,
        seed_split=seed_active,
        port=port,
        env=env_active,
        model_size_mb=model.size_mb,
        native_ctx=base_ctx,  # a variant never gets more ctx than the base
        total_layers=model.total_layers,
        reserve_mb=reserve_active,
        gpus=gpus_active,
    ):
        if line.startswith("__AI_RESULT__:"):
            payload = line.removeprefix("__AI_RESULT__:")
            parts = payload.split(":", 2)
            try:
                ai_ctx = int(parts[0])
                ai_split = [float(x) for x in parts[1].split(",") if x.strip()]
            except (ValueError, IndexError):
                yield f"AI variant: unparseable result {payload[:60]}"
                yield "__RESULT__:0:0:error"
                return
            break
        if line.startswith("__AI_ERROR__:"):
            yield f"AI variant: {line.removeprefix('__AI_ERROR__:')}"
            yield "__RESULT__:0:0:error"
            return
        yield line

    if ai_ctx is None or not ai_split:
        yield "__RESULT__:0:0:error"
        return

    num_gpus = sum(1 for x in ai_split if x > 0)
    # UUID companion field — same contract as _active_uuid_csv: the UUIDs
    # of the GPUs that actually carry layers, in enumeration order.
    uuid_csv = ",".join(
        gpus[idx].uuid for j, idx in enumerate(active)
        if j < len(ai_split) and ai_split[j] > 0
    )
    if as_speed:
        # __SPEED__ wants the FULL split (all GPUs, colon-separated, 0 for
        # inactive) like _split_str — map the active result back onto the
        # full GPU list so the mixin's parser keeps the right CUDA order.
        full_split = [0] * len(gpus)
        for j, idx in enumerate(active):
            full_split[idx] = int(round(ai_split[j])) if j < len(ai_split) else 0
        split_colon = ":".join(str(x) for x in full_split)
        yield f"__SPEED__:{split_colon},{ai_ctx},{num_gpus},{base_kv},{uuid_csv}"
        return
    # Same sentinel contract as _result_sentinel: only the active (>0)
    # split values, in active-index order; num_gpus = their count. Variants
    # run gpu-mode (ngl=99) and inherit thinking from the base.
    ts_csv = ",".join(f"{x:g}" for x in ai_split if x > 0)
    thinks = "thinks" if known_thinking else "nothink"
    yield f"__RESULT__:{ai_ctx}:99:gpu:{thinks}:{base_kv}:{ts_csv}:{num_gpus}:{uuid_csv}"


# ═══════════════════════════════════════════════════════════════════
# Formatting helpers
# ═══════════════════════════════════════════════════════════════════

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


