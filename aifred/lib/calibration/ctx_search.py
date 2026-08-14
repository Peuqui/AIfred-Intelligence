"""Verify-/Refine-Loop und die math-geführten ctx-Binärsuchen.

Kernstück der Phasen C/E: :func:`_verify_and_refine` (Probe + Shift-Loop +
Abwärts-/Aufwärts-Suche) und die SSOT-Binärsuche
:func:`_binary_search_fitting_ctx` samt Probe-Cache. Helfer-Schicht:
darf :mod:`flow` NICHT importieren.
"""

from __future__ import annotations

from typing import AsyncIterator, Optional

from ..config import (
    LLAMACPP_CALIBRATION_PRECISION,
    MIN_USEFUL_CONTEXT_TOKENS,
)
from ..formatting import format_number
from . import projection as proj
from .fit_math import (
    _MODEL_BIAS_CACHE,
    _batch_signature,
    _bias_key,
    _BiasState,
    _math_max_fitting_ctx,
    _math_predicts_fit,
)
from .reporting import (
    _active_gpu_count,
    _build_result,
    _fmt_verify,
    _planned_free_line,
    _split_str,
)
from .split_refine import (
    _context_refine_swap,
    _measured_split_ceiling,
    _refine_split_from_measurement,
    _shift_one_layer_blind,
)
from .types import Budget, Candidate, GPU, Model, Result
from .verifier import VerifyResult, verify


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


# After _MATH_OOM_GIVEUP math-driven OOMs in a row, stop trusting math for
# the rest of the search — the bias model is broken in this region and we
# just bisect (without this the loop oscillates math_max↔bisect, wasting
# one probe per iteration).
_MATH_OOM_GIVEUP = 2


def _learn_bias(
    bias: _BiasState,
    current_split: tuple[float, ...],
    probed_ctx: int,
    candidate: Candidate,
    model: Model,
    gpus: list[GPU],
    budget: Budget,
    r: VerifyResult,
) -> str | None:
    """SSOT: Bias aus einer Probe-Messung lernen — Abwärts- UND
    Aufwärts-Suche, ✓- UND ✗-Probes (gerade die ✓-Probes tragen die
    "Mathe zu pessimistisch"-Information, die der alte Nur-✗-Pfad nie sah).

    Die Vorhersage wird am TATSÄCHLICH geprobten ctx gerechnet: bei einem
    Bisect-Kandidaten (≠ math_max) wanderte sonst der KV-Zuwachs zwischen
    beiden ctx mit in den Bias (<1 ms Rechnung spart ~3-min-Loads).

    Liefert die Log-Meldung, wenn sich der angewandte Bias änderte.
    """
    if not r.measured_free_mb or candidate.vram_model is None:
        # Ohne Messwerte oder ohne echtes Kostenmodell (Vorhersage wäre
        # eine Konstante) gibt es nichts zu lernen.
        return None
    active_free = [
        r.measured_free_mb[i] for i in range(len(current_split))
        if i < len(r.measured_free_mb) and current_split[i] > 0
    ]
    if not active_free:
        return None
    real_min = min(active_free)
    _, pred_at_probe = _math_predicts_fit(
        current_split, probed_ctx, candidate, model, gpus, budget,
    )
    upd = bias.observe(pred_at_probe, real_min, fits=r.fits)
    if upd is None:
        return None
    old, new = upd
    return (
        f"🧮 math bias updated: predicted {pred_at_probe} MB vs real "
        f"{real_min} MB free → bias {new:+d} MB (was {old:+d} MB)"
    )


def _pick_next_ctx(
    current_split: tuple[float, ...],
    lo: int,
    hi: int,
    candidate: Candidate,
    model: Model,
    gpus: list[GPU],
    budget: Budget,
    *,
    math_bias_mb: int,
    bias_measured: bool,
    math_unreliable: bool,
    consecutive_math_oom: int,
) -> tuple[int | None, str, bool]:
    """SSOT für die Kandidatenwahl der Binärsuche (Abwärts-Suche UND
    Aufwärts-Probe): traue der bias-korrigierten Mathe-Vorhersage oder
    bisektiere das Fenster.

    Returns ``(cand_ctx, src, used_math)`` — ``cand_ctx is None`` heißt
    Bisect-Fenster degeneriert (Kandidat außerhalb (lo, hi)), Suche beenden.
    """
    math_max, predicted_min = _math_max_fitting_ctx(
        current_split,
        lo + LLAMACPP_CALIBRATION_PRECISION,
        hi - LLAMACPP_CALIBRATION_PRECISION,
        candidate, model, gpus, budget,
        extra_safety_margin=math_bias_mb,
    )
    # Pre-measurement trust floor: BEFORE we have any measured/seeded bias,
    # don't trust a math prediction whose raw free is below this — a high-bias
    # model would OOM on the first over-jump. Tied to the config safety margin,
    # NOT a magic constant. Once the bias IS known, ``predicted_min`` already
    # carries it (via extra_safety_margin) and we trust it down to the margin;
    # the old fixed 512-MB floor made the search bisect the whole way in the
    # tight TTS regime even after the bias was learned.
    unmeasured_trust_floor = 2 * budget.safety_margin
    math_too_tight = (
        not bias_measured
        and math_max > lo
        and predicted_min < unmeasured_trust_floor
    )
    math_burned_out = consecutive_math_oom >= _MATH_OOM_GIVEUP
    if (
        math_max > lo
        and not math_unreliable
        and not math_too_tight
        and not math_burned_out
    ):
        bias_note = f", bias {math_bias_mb:+d} MB" if math_bias_mb else ""
        return math_max, f"math max → {predicted_min} MB free{bias_note}", True

    cand_ctx = ((lo + hi) // 2 // LLAMACPP_CALIBRATION_PRECISION) * LLAMACPP_CALIBRATION_PRECISION
    if cand_ctx <= lo or cand_ctx >= hi:
        return None, "", False
    if math_burned_out:
        src = f"bisect (math gave up after {_MATH_OOM_GIVEUP} OOMs)"
    elif math_unreliable:
        src = "bisect (math unreliable after silent crash)"
    elif math_too_tight:
        src = f"bisect (unmeasured bias, only {predicted_min} MB free predicted)"
    else:
        src = "bisect (math saw no fit)"
    return cand_ctx, src, False


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
    initial_bias_mb: int | None = None,
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
    # Bias = fit-params-Kostenmodell vs. reale Load-Messung, BIDIREKTIONAL
    # (positiv = Mathe zu optimistisch, negativ = zu pessimistisch — Details
    # am ``_MODEL_BIAS_CACHE``). Geseedet vom Trigger-OOM dieser Suche
    # (``initial_bias_mb``, None = kein Seed) und vom Cross-Variant-Cache;
    # der OOM-Seed ist zugleich der ``oom_floor`` (die Mathe darf nie
    # mutiger werden, als der real gemessene OOM erlaubt).
    _cached_bias = _MODEL_BIAS_CACHE.get(
        _bias_key(model, gpus, _batch_signature(full_cmd)),
    )
    bias = _BiasState(
        cache_key=_bias_key(model, gpus, _batch_signature(full_cmd)),
        applied=(
            max(initial_bias_mb, _cached_bias)
            if initial_bias_mb is not None and _cached_bias is not None
            else (
                initial_bias_mb if initial_bias_mb is not None
                else (_cached_bias if _cached_bias is not None else 0)
            )
        ),
        # Only a real cost model makes the bias meaningful; without one the
        # math is a constant → keep bisecting.
        measured=(
            (initial_bias_mb is not None or _cached_bias is not None)
            and candidate.vram_model is not None
        ),
        oom_floor=initial_bias_mb,
    )
    # Math becomes unreliable after a probe crashed without leaving
    # measurement data (e.g. llama.cpp segfault on OOM — exit -11): the bias
    # can't learn from it, so the next math_max prediction would land within
    # one PRECISION of the failed value and crawl in 256-token decrements.
    # Force one true bisection step to escape that trap.
    math_unreliable = False
    consecutive_math_oom = 0
    # Load-death signature: when the server dies BEFORE getting ready
    # (measured empty, e.g. segfault at load), shrinking ctx re-runs the
    # identical load. If the next such death has the same per-GPU load minimum
    # the failure is ctx-independent and we stop.
    prev_load_sig = initial_load_sig
    while hi - lo > LLAMACPP_CALIBRATION_PRECISION:
        cand_ctx_opt, src, used_math = _pick_next_ctx(
            current_split, lo, hi, candidate, model, gpus, budget,
            math_bias_mb=bias.applied,
            bias_measured=bias.measured,
            math_unreliable=math_unreliable,
            consecutive_math_oom=consecutive_math_oom,
        )
        if cand_ctx_opt is None:
            break
        cand_ctx = cand_ctx_opt
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
        # Bias aus JEDER Probe mit Messwerten lernen (✓ und ✗) — SSOT in
        # ``_learn_bias``; schreibt auch den Cross-Variant-Cache.
        bias_msg = _learn_bias(
            bias, current_split, cand_ctx, candidate, model, gpus, budget, r,
        )
        if bias_msg:
            yield f"{status_prefix} {bias_msg}"
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
            if r.measured_free_mb:
                math_unreliable = False
                prev_load_sig = None
            else:
                # Probe crashed silently (no measurement, likely SegFault on
                # OOM). Math has nothing to learn — force bisection next round;
                # and if the load dies IDENTICALLY regardless of ctx, stop.
                math_unreliable = True
                # Nur NICHT-leere Signaturen vergleichen: liefert das
                # Load-Sampling zweimal keine Daten, wäre () == () sonst
                # fälschlich "identisch" und ein ctx-abhängiger Crash würde
                # den ganzen Kandidaten verwerfen.
                if (
                    prev_load_sig
                    and r.load_min_free_mb
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
        # Kontinuität über die Shift-Sequenz: Ein Vision-Probe-Crash loggt
        # oft keine parsebare OOM-Zeile. Dieselbe Grenz-Konfiguration
        # stirbt je nach Allokations-Glück mal parsebar beim Load, mal
        # stumm erst bei der Bildanfrage (Qwen3.8 64:1@262k: beides
        # beobachtet). Innerhalb einer laufenden Shift-Sequenz ist die
        # zuletzt identifizierte OOM-Karte der beste Kandidat — weiter in
        # die bekannte Richtung schieben statt nach EINEM Shift auf
        # ctx-shrink umzuschwenken. max_shifts + Oszillations-Guard
        # begrenzen eine Fehlannahme; ctx-shrink bleibt letzter Ausweg.
        last_oom_cuda_id = r.oom_cuda_id
        while not r.fits and shift_attempt < max_shifts:
            _eff_oom_id = r.oom_cuda_id
            if _eff_oom_id is None and last_oom_cuda_id is not None:
                _eff_oom_id = last_oom_cuda_id
                yield (
                    f"{status_prefix} OOM GPU not in server log — "
                    f"continuing shift away from CUDA{_eff_oom_id} "
                    f"(last identified OOM source)"
                )
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
                    oom_cuda_id=_eff_oom_id,
                    # lock_split zählt hier genauso wie in den Smart-Pfaden
                    # (1663, Step 3): der Blind-Shift darf einer split-
                    # gelockten VLM-Variante keine idle Karte aktivieren —
                    # gerade beim Load-OOM wirken unbefüllte Karten leer
                    # und wären das bevorzugte (falsche) Ziel.
                    keep_active_set=lock_active_gpus or lock_split,
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
                elif _eff_oom_id is None:
                    # Ohne geparste OOM-Karte, ohne Messwerte UND ohne eine
                    # zuvor identifizierte Karte in dieser Sequenz shiftet
                    # der Blind-Shift bewusst nicht (falsche Quell-Karte
                    # wäre schlimmer als keine) — das ist KEIN "alle Ziele
                    # voll", sondern fehlende Info (z.B. Segfault exit -11
                    # ohne OOM-Zeile im stderr).
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
            r = await _load_and_cache(
                probe_cache, current_split, current_ctx,
                full_cmd=full_cmd, candidate=candidate, port=port,
                gpus=gpus, budget=budget, env=env,
                probe_thinking=probe_thinking and thinks_seen is None,
            )
            yield (_fmt_verify(
                status_prefix, iteration, current_split, current_ctx, r,
            ))
            if r.thinks is not None:
                thinks_seen = r.thinks
            if r.oom_cuda_id is not None:
                last_oom_cuda_id = r.oom_cuda_id

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
            # TTS regime). Bidirektional: auch ein NEGATIVER Wert ("Mathe zu
            # pessimistisch, trotzdem real-OOM") ist ein gültiger Seed — er
            # wird zugleich der oom_floor der Suche. None = kein Seed. Only
            # when the failing probe left a steady-state measurement and we
            # have a model.
            init_bias: int | None = None
            if r.measured_free_mb and candidate.vram_model is not None:
                _, _pred_min = _math_predicts_fit(
                    current_split, current_ctx, candidate, model, gpus, budget,
                )
                _active_real = [
                    r.measured_free_mb[i] for i in range(len(current_split))
                    if i < len(r.measured_free_mb) and current_split[i] > 0
                ]
                if _active_real:
                    init_bias = _pred_min - min(_active_real)
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
            # Ohne den Lock durfte die Kaskade hier idle Karten aktivieren
            # — Step 1 und Step 3 setzen ihn an denselben Stellen (1663,
            # 2060), nur Step 2 hatte das Loch (Speed-Set wuchs still um
            # eine langsame Karte).
            keep_active_set=lock_active_gpus or lock_split,
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
        r_new = await _load_and_cache(
            probe_cache, refined, current_ctx,
            full_cmd=full_cmd, candidate=candidate, port=port,
            gpus=gpus, budget=budget, env=env, probe_thinking=False,
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
            # Ceiling-refine at the anchor BEFORE pushing up (2026-07-10):
            # the fit probe carries the full measurement, and a large
            # headroom gap (DeepSeek base: CUDA0 1051 MB vs CUDA2 4445 MB)
            # caps the whole upward search at the tight card's ceiling —
            # one probed swap raises it for every subsequent step. And when
            # later loads die silently (no measurement), Step 3's OOM-refine
            # never fires, so this anchor pass is the ONLY rebalance chance.
            # The Step-2 concern (chasing a ceiling past the useful cap) is
            # already guarded here by the surrounding
            # ``current_ctx < upward_ceiling``.
            while True:
                refined_a, reason_a = _context_refine_swap(
                    current_split, gpus, r, budget,
                    vram_model=candidate.vram_model,
                    total_layers=model.total_layers,
                    model_size_mb=model.size_mb,
                    current_context=current_ctx,
                    keep_active_set=lock_active_gpus or lock_split,
                )
                if refined_a is None:
                    # Always say WHY no anchor swap happened — a silent
                    # skip is indistinguishable from the refine never
                    # running (the 2026-07-10 DeepSeek confusion).
                    yield (
                        f"{status_prefix} anchor ceiling-refine: {reason_a}"
                    )
                    break
                if refined_a in seen_splits:
                    yield (
                        f"{status_prefix} anchor ceiling-refine: split "
                        f"{_split_str(refined_a)} already probed — keeping "
                        f"{_split_str(current_split)}"
                    )
                    break
                seen_splits.add(refined_a)
                iteration += 1
                yield (
                    f"{status_prefix} anchor ceiling-refine ({reason_a}): "
                    f"{_split_str(current_split)} → {_split_str(refined_a)}"
                )
                r_a = await _load_and_cache(
                    probe_cache, refined_a, current_ctx,
                    full_cmd=full_cmd, candidate=candidate, port=port,
                    gpus=gpus, budget=budget, env=env, probe_thinking=False,
                )
                yield _fmt_verify(
                    status_prefix, iteration, refined_a, current_ctx, r_a,
                )
                if not r_a.fits:
                    yield (
                        f"{status_prefix} anchor refine OOM — keeping "
                        f"previous split"
                    )
                    break
                current_split = refined_a
                r = r_a
                last_good = (r, current_split, current_ctx)
                active_free = [
                    f for i, f in enumerate(r.measured_free_mb)
                    if i < len(current_split) and current_split[i] > 0
                ]

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
            # model+hardware+batch-regime already measured the bias, so trust
            # the math from probe 1. Without a cached value the trust floor
            # applies until the first probe measures it (same rule as the
            # down-search). Bidirektional wie dort; kein oom_floor-Seed (in
            # diesem Fenster wurde noch kein OOM gemessen).
            _up_cached = _MODEL_BIAS_CACHE.get(
                _bias_key(model, gpus, _batch_signature(full_cmd)),
            )
            up_bias = _BiasState(
                cache_key=_bias_key(model, gpus, _batch_signature(full_cmd)),
                applied=_up_cached if _up_cached is not None else 0,
                measured=(
                    _up_cached is not None
                    and candidate.vram_model is not None
                ),
            )
            # See the downward search above for the rationale behind these
            # two guards (Fix C + D from the calibration audit).
            consecutive_math_oom = 0
            # Stille Load-Crashes wie in der Abwärts-Suche behandeln: ohne
            # Messwerte kann Math nichts lernen (Bisect erzwingen), und ein
            # ctx-unabhängig identisch sterbender Load beendet die Suche,
            # statt das Fenster in ~3-min-Crashes leer zu bisektieren.
            up_math_unreliable = False
            up_load_sig: tuple[int, ...] | None = None
            up_rescue_done = False
            while hi - lo > LLAMACPP_CALIBRATION_PRECISION:
                cand_ctx_opt, src, used_math = _pick_next_ctx(
                    current_split, lo, hi, candidate, model, gpus, budget,
                    math_bias_mb=up_bias.applied,
                    bias_measured=up_bias.measured,
                    math_unreliable=up_math_unreliable,
                    consecutive_math_oom=consecutive_math_oom,
                )
                if cand_ctx_opt is None:
                    break
                cand_ctx = cand_ctx_opt
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
                # Bias aus JEDER Probe mit Messwerten lernen (✓ und ✗) —
                # gleiche SSOT wie in der Abwärts-Suche.
                bias_msg = _learn_bias(
                    up_bias, current_split, cand_ctx,
                    candidate, model, gpus, budget, r_up,
                )
                if bias_msg:
                    yield f"{status_prefix} {bias_msg}"
                if r_up.fits:
                    lo = cand_ctx
                    last_good = (r_up, current_split, cand_ctx)
                    up_math_unreliable = False
                    up_load_sig = None
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
                            # Load-Signatur ist split-abhängig — nach dem
                            # Split-Wechsel darf eine alte Signatur nicht
                            # gegen den neuen Split verglichen werden.
                            up_math_unreliable = False
                            up_load_sig = None
                            continue
                        # Refined split also failed — fall through to
                        # math-bias update + hi shrink with r_up's data.
                    hi = cand_ctx
                    if not r_up.measured_free_mb:
                        # Stiller Load-Crash (kein Steady-State-Messwert) —
                        # Spiegel der Abwärts-Suche: Math nichts zu lernen →
                        # Bisect erzwingen. Identische NICHT-leere Signatur
                        # sieht ctx-unabhängig aus — aber ``lo`` LIEF
                        # nachweislich, der Fehler hat also real eine
                        # Schwelle in (lo, cand_ctx) (DeepSeek-V4 #25468:
                        # ctx-proportionaler Indexer-Buffer). Bevor die
                        # Suche das Restfenster verschenkt: EIN Rettungs-
                        # Probe am messungs-verankerten Ceiling des letzten
                        # Fits — die einzige Stelle, die real noch fitten
                        # kann. Crasht auch der, ist Schluss.
                        up_math_unreliable = True
                        if (
                            up_load_sig
                            and r_up.load_min_free_mb
                            and r_up.load_min_free_mb == up_load_sig
                        ):
                            rescue_ctx = 0
                            lg_r, lg_split, lg_ctx = last_good
                            if (
                                not up_rescue_done
                                and candidate.vram_model is not None
                                and lg_r.fits
                                and lg_r.measured_free_mb
                                and lg_split == current_split
                            ):
                                from .optimizer import _per_gpu_coefficients
                                _, _slope = _per_gpu_coefficients(
                                    candidate.vram_model,
                                    model.total_layers, model.size_mb,
                                )
                                _ceiling = _measured_split_ceiling(
                                    [int(x) for x in current_split],
                                    [float(f) for f in lg_r.measured_free_mb],
                                    _slope, lg_ctx, budget.safety_margin,
                                )
                                if _ceiling != float("inf"):
                                    rescue_ctx = (
                                        int(_ceiling)
                                        // LLAMACPP_CALIBRATION_PRECISION
                                        * LLAMACPP_CALIBRATION_PRECISION
                                    )
                                    rescue_ctx = min(
                                        rescue_ctx,
                                        hi - LLAMACPP_CALIBRATION_PRECISION,
                                    )
                            if rescue_ctx <= lo:
                                yield (
                                    f"{status_prefix} load failure is "
                                    f"ctx-independent (identical load minimum) "
                                    f"— stopping upward search"
                                )
                                break
                            up_rescue_done = True
                            iteration += 1
                            yield (
                                f"{status_prefix} crashes look ctx-independent, "
                                f"but {format_number(lo)} did load — one rescue "
                                f"probe at the measured ceiling "
                                f"{format_number(rescue_ctx)}"
                            )
                            r_rescue = await _load_and_cache(
                                probe_cache, current_split, rescue_ctx,
                                full_cmd=full_cmd, candidate=candidate,
                                port=port, gpus=gpus, budget=budget, env=env,
                                probe_thinking=False,
                            )
                            yield _fmt_verify(
                                status_prefix, iteration, current_split,
                                rescue_ctx, r_rescue,
                            )
                            if r_rescue.fits:
                                lo = rescue_ctx
                                last_good = (r_rescue, current_split, rescue_ctx)
                                up_math_unreliable = False
                                up_load_sig = None
                                continue
                            if r_rescue.measured_free_mb:
                                # Real OOM with measurement (not a crash) —
                                # the normal search can keep learning below.
                                hi = rescue_ctx
                                up_math_unreliable = False
                                up_load_sig = None
                                continue
                            yield (
                                f"{status_prefix} rescue probe crashed too — "
                                f"stopping upward search"
                            )
                            break
                        up_load_sig = r_up.load_min_free_mb
                    if r_up.measured_free_mb:
                        # Bias-Lernen ist bereits oben (vor dem fits-Branch)
                        # passiert — hier nur den Crash-Zustand zurücksetzen.
                        up_math_unreliable = False
                        up_load_sig = None
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
