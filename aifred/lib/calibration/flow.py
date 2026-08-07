"""Top-level calibration orchestrator.

Five sequential phases (``A``–``E``) each documented inline.  The output
protocol (``__RESULT__`` / ``__SPEED__`` strings) is preserved so that
existing state-mixin parsers keep working without change.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import AsyncIterator, Optional

from ..config import (
    CALIBRATION_MIN_CONTEXT,
    LLAMACPP_CALIBRATION_PORT,
    LLAMACPP_HYBRID_HEALTH_TIMEOUT,
    LLAMACPP_VRAM_SAFETY_MARGIN,
    MIN_FREE_RAM_MB,
    MIN_USEFUL_CONTEXT_TOKENS,
)
from ..formatting import format_number
from ..model_vram_cache import add_llamacpp_calibration
from . import llamaswap_io as io
from . import projection as proj
from .ctx_search import _Done, _verify_and_refine
from .fit_math import (
    _SOLO_NEAR_MISS_RATIO,
    _derive_reserved_split,
    _enumerate_gpu_configs,
    _is_vision_model,
    _kv_levels_from,
    _max_ctx_where_all_layers_fit,
    _seed_tensor_split,
    _weights_fit,
)
from .flow_log import _tee_calibration_log
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
from .llamaswap_io import parse_tensor_split
from .model_meta import _load_model_meta
from .optimizer import OptResult, fill_fastest_first
from .persist import _persist_cache, _write_base_config, _write_speed_config
from .reporting import (
    _active_uuid_csv,
    _fmt_verify,
    _format_candidate_line,
    _hybrid_allowed_in_settings,
    _kv_quant_from_cmd,
    _ngl_from_cmd,
    _result_sentinel,
    _speed_sentinel,
    _split_str,
    _track_failed_solo,
)
from .types import Budget, Candidate, GPU, Model, Result
from .verifier import kill_orphan_on_port, verify

logger = logging.getLogger(__name__)


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
    # Draft-Sidecar-Profile (--model-draft) zahlen extra Marge: Spec-Decode-
    # Puffer allozieren spät (Produktions-OOM 2026-08-03); Plain-/MTP-Profile
    # behalten die knappe Marge und damit ihren Kontext.
    from .projection import draft_gguf_path
    if draft_gguf_path(full_cmd) is not None:
        from ..config import LLAMACPP_DRAFT_SAFETY_MARGIN_EXTRA_MB
        safety_margin += LLAMACPP_DRAFT_SAFETY_MARGIN_EXTRA_MB
        yield (
            f"Draft sidecar detected — safety margin "
            f"{LLAMACPP_VRAM_SAFETY_MARGIN} + "
            f"{LLAMACPP_DRAFT_SAFETY_MARGIN_EXTRA_MB} MB"
        )

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
    # Real verifizierte, aber unter nativ kollabierte Zellen. Früher galt
    # "first successfully-verified config wins": Sagte die Math bei der
    # 2-Karten-Zelle "nativ passt", kollabierte der Verify aber real (MoE-
    # Bias bis ~1,5 GB), wurde das kollabierte Ergebnis trotzdem als BASE
    # akzeptiert — die 3-Karten-Zelle, die echtes Nativ gepackt hätte,
    # wurde nie probiert. Jetzt: kollabierte Ergebnisse hier merken,
    # weitersuchen; die finale Auswahl unten vergleicht alle ECHTEN
    # Messungen (KV-Qualität zuerst, dann Kontext).
    verified_fallbacks: list[tuple[Candidate, Result]] = []
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
            if v_result is not None and v_result.thinks is not None:
                known_thinks = v_result.thinks
            if (
                v_result is not None
                and v_result.context >= model.native_context
            ):
                base_pick = c
                base_result_obj = v_result
                yield (
                    f"  ✓ Phase 1 success: {label}, KV={kv}, "
                    f"split={_split_str(base_result_obj.tensor_split)}, "
                    f"ctx={format_number(base_result_obj.context)}"
                )
                break
            if v_result is not None:
                # Math sagte nativ, die Messung kollabierte darunter —
                # NICHT als Base akzeptieren, sondern merken und die
                # nächste (größere) Zelle proben: die packt nativ evtl.
                # wirklich. Kostet pro Zelle Minuten, kann aber ein
                # Vielfaches an Kontext retten.
                verified_fallbacks.append((c, v_result))
                yield (
                    f"  ↳ [{label} / KV={kv}] verified BELOW native "
                    f"(ctx {format_number(v_result.context)}) — kept as "
                    f"fallback, trying next config"
                )
                continue

    # No candidate reached native context. Before giving up to hybrid,
    # try a **best-effort GPU-only fit**. Prefer the HIGHEST KV QUALITY
    # (f16 > q8_0) whose best GPU-set still reaches a useful context —
    # full-precision KV is faster on these GPUs (P40/V100/RTX8000 have no
    # fast quantized-KV attention path) and higher quality. Only drop to a
    # lower KV quality if the better one can't even reach a useful context.
    # (Previously this just took max(max_context), which silently traded
    # full-precision KV for ~20% more context — slower on this hardware.)
    if base_result_obj is None and all_tried:
        # Nur noch UNverifizierte Zellen kommen für den Best-Effort-Probe
        # infrage: die kollabierten Zellen sind schon real gemessen, ihre
        # Math-Werte damit widerlegt — ein Re-Probe wäre reine Zeit-
        # verschwendung (Identitätsvergleich: dieselben Objekte landen in
        # all_tried UND verified_fallbacks).
        unverified = [
            c for c in all_tried
            if all(c is not vc for vc, _ in verified_fallbacks)
        ]
        best = None
        for kv in kv_levels:  # quality-ordered: f16 first
            kv_best = max(
                (c for c in unverified if c.kv_quant == kv),
                key=lambda c: c.max_context,
                default=None,
            )
            if kv_best is not None and kv_best.max_context >= MIN_USEFUL_CONTEXT_TOKENS:
                best = kv_best
                break
        if best is None and unverified:
            # no KV level reached useful ctx → absolute best
            best = max(unverified, key=lambda c: c.max_context)
        if best is not None and best.max_context >= MIN_USEFUL_CONTEXT_TOKENS:
            best_label = (
                f"{best.n_gpus} GPUs / KV={best.kv_quant}"
            )
            yield (
                f"  💡 No native-ctx fit — probing best unverified candidate: "
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
                if v_result_fb.thinks is not None:
                    known_thinks = v_result_fb.thinks
                # In den Pool statt direkt zur Base — die finale Auswahl
                # unten vergleicht gegen die kollabierten Phase-1-Zellen.
                verified_fallbacks.append((best, v_result_fb))

    # Finale Auswahl über ALLE real verifizierten Ergebnisse (kollabierte
    # Phase-1-Zellen + Best-Effort-Probe): dieselbe Philosophie wie die
    # Kandidaten-Wahl — höchste KV-Qualität zuerst (f16 ist auf dieser
    # Hardware schneller), innerhalb der Qualität der größte Kontext.
    # Jedes Pool-Ergebnis ist eine echte Messung ≥ MIN_USEFUL (die
    # ctx-Suche verwirft alles darunter), kein erneuter Probe nötig.
    if base_result_obj is None and verified_fallbacks:
        picked: Result | None = None
        for kv in kv_levels:
            kv_pool = [r for _, r in verified_fallbacks if r.kv_quant == kv]
            if kv_pool:
                picked = max(kv_pool, key=lambda r: r.context)
                break
        if picked is None:
            picked = max(
                (r for _, r in verified_fallbacks), key=lambda r: r.context,
            )
        base_result_obj = picked
        yield (
            f"  ✓ Phase 1 fallback base (best of "
            f"{len(verified_fallbacks)} verified): KV={picked.kv_quant}, "
            f"split={_split_str(picked.tensor_split)}, "
            f"ctx={format_number(picked.context)}"
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
            async for msg in _write_speed_config(
                config_path, model_id, speed_result, gpus,
            ):
                yield msg

    # ── Emit sentinels ─────────────────────────────────────────────
    yield _result_sentinel(final, thinks=thinks, gpus=gpus)
    if speed_result:
        yield _speed_sentinel(speed_result, gpus)


# ═══════════════════════════════════════════════════════════════════
# Phase helpers
# ═══════════════════════════════════════════════════════════════════


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
            # Re-use from base search only if the same (n, kv) cell used
            # the SAME GPU set as this cascade stage (the fastest n cards,
            # i.e. CUDA 0..n-1). The base search also projects homogeneous
            # slow-class cells (e.g. 3× V100 = [2,3,4]) with identical
            # n_gpus/kv — matching those put the "speed" variant on the
            # slow cards.
            _wanted_active = set(range(n))
            # Vision-Modelle (--mmproj): Sweep-Zellen NICHT wiederverwenden.
            # Deren Projektion entstand VOR der ersten Probe — also vor
            # einem frisch gemessenen Encode-Buffer-Burn-In
            # (mmproj_encode_vram_cache, 2026-07-31). Eine frische
            # Projektion (~2 s fit-params) trägt den gemessenen Peak noch
            # im selben Kalibrierungslauf in die Speed-Phase, statt ihn
            # über Vision-Crash-Probes vom Bias ertasten zu lassen.
            cached: Candidate | None = None
            if "--mmproj" not in full_cmd:
                cached = next(
                    (c for c in already_tried
                     if c.n_gpus == n and c.kv_quant == base_kv
                     and {i for i, s in enumerate(c.tensor_split) if s > 0}
                     == _wanted_active),
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


# ═══════════════════════════════════════════════════════════════════
# AI calibration adapter
# ═══════════════════════════════════════════════════════════════════


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
    # Sentinel grammar: __RESULT__ is COLON-delimited, so the split field
    # must be comma-CSV of the ACTIVE values — same as _result_sentinel.
    # A colon-joined split here shifted every following field in the parser
    # (_parse_calibration_result splits on ":"): tensor_split became the
    # first layer count, num_gpus the second, uuids the third.
    ts_csv = ",".join(str(int(round(r))) for r in ai_split if r > 0)

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
        f"__RESULT__:{ai_ctx}:{ngl}:gpu:thinks:{kv}:{ts_csv}:{num_gpus}:"
        f"{_active_uuid_csv(tuple(ai_split), gpus)}"
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
            # 8th field (active-GPU UUIDs) like every other __RESULT__ —
            # the parser docstring requires it as the CUDA_VISIBLE source.
            yield (
                f"__RESULT__:{target}:{ngl}:hybrid:"
                f"{'thinks' if thinks else 'nothink'}:f16:{ts_csv}:{len(gpus)}:"
                f"{_active_uuid_csv(ts_ngl, gpus)}"
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
    # Draft-Sidecar-Profile zahlen extra Marge (siehe Basis-Pfad).
    from .projection import draft_gguf_path
    if draft_gguf_path(full_cmd) is not None:
        from ..config import LLAMACPP_DRAFT_SAFETY_MARGIN_EXTRA_MB
        safety_margin += LLAMACPP_DRAFT_SAFETY_MARGIN_EXTRA_MB
        yield (
            f"Draft sidecar detected — safety margin "
            f"{LLAMACPP_VRAM_SAFETY_MARGIN} + "
            f"{LLAMACPP_DRAFT_SAFETY_MARGIN_EXTRA_MB} MB"
        )
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
