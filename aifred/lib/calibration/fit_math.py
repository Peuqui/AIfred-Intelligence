"""Reine Fit-/Projektions-Mathematik der Kalibrierung.

Kostenmodell-Vorhersagen, GPU-Konfigurations-Enumeration, Split-Seeding/
-Quantisierung und der prozessweite Cross-Variant-Bias-Cache. Helfer-Schicht:
darf :mod:`flow` NICHT importieren.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from ..config import (
    CALIBRATION_MIN_CONTEXT,
    LLAMACPP_CALIBRATION_PRECISION,
)
from .gpu import gpu_label
from .optimizer import OptResult, fill_fastest_first
from .types import Budget, Candidate, GPU, Model


# KV-quant levels in order of quality (higher = better, larger VRAM).
# Q4 is intentionally excluded from the default sweep: it sacrifices
# too much quality for marginal VRAM savings.  A caller can opt in by
# passing ``min_kv="q4_0"`` (used by edge-case re-runs on very small
# remaining budgets, never by the default flow).
_DEFAULT_KV_LEVELS = ("f16", "q8_0")
_ALL_KV_LEVELS = ("f16", "q8_0", "q4_0")


# 1-GPU-Sweep-Dominanz: Eine gleich große Schwesterkarte unterscheidet sich
# nur ums Display-Handicap (~256 MB ≈ wenige tausend Tokens). Sie wird nur
# noch getestet, wenn die gescheiterte Karte den nativen Kontext um weniger
# als diesen Anteil verfehlt hat — ein 25%-Fehlbetrag ist damit nie aufholbar.
_SOLO_NEAR_MISS_RATIO = 0.05


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


# ── Cross-variant bias cache (the "cross-engine derivation") ──────────────
# The fit-params-vs-real gap ("bias") is a property of the MODEL + HARDWARE +
# batch regime — runtime buffers (compute/MTP/activation) that fit-params
# models differently than llama-server allocates them — NOT of the per-engine
# TTS/VLM reserve (that's subtracted separately, on the reserve-adjusted
# measured free). So once ANY variant of a model measures the bias, every
# later variant on the same GPUs seeds its ctx search with it and trusts the
# cost model from probe 1 instead of re-learning it.
#
# The bias is BIDIRECTIONAL (2026-07-31): positive = math over-predicts free
# (too optimistic, be more careful), negative = math under-predicts free (too
# pessimistic — fit-params overstates e.g. the ub-2048 compute buffers by
# ~2.5 GB/GPU on the 397B, which froze every search into blind bisection).
# The cache stores the LATEST observed value, not a max-ratchet: a ratchet
# would permanently ignore negative measurements after one positive one. The
# OOM floor in :class:`_BiasState` guards the risky (negative) direction, and
# every math jump is still verified by a real probe — a wrong bias costs one
# probe, never a wrong result.
#
# Keyed by (model_id, gpu-uuids, batch-signature): the compute-buffer part of
# the bias scales with -b/-ub, so a value learned at ub 512 must not seed a
# ub 2048 run. Process-local: a service restart starts clean.
_MODEL_BIAS_CACHE: dict[tuple[str, tuple[str, ...], str], int] = {}


def _batch_signature(full_cmd: str) -> str:
    """Extract the ``-b``/``-ub`` regime from a llama-server cmd.

    Falls back to the llama.cpp defaults (b=2048, ub=512) for flags not
    present in the cmd — the bias cache must distinguish batch regimes
    because the compute buffers (= the bias) scale with them.
    """
    tokens = full_cmd.split()
    b_val, ub_val = "2048", "512"
    for i, tok in enumerate(tokens[:-1]):
        if tok == "-b":
            b_val = tokens[i + 1]
        elif tok == "-ub":
            ub_val = tokens[i + 1]
    return f"b{b_val}-ub{ub_val}"


def _bias_key(
    model: Model, gpus: list[GPU], batch_sig: str,
) -> tuple[str, tuple[str, ...], str]:
    return (model.model_id, tuple(g.uuid for g in gpus), batch_sig)


@dataclass
class _BiasState:
    """Adaptive math-vs-real bias for one ctx search (SSOT for both the
    downward binary search and the upward push).

    ``applied`` is fed into :func:`_math_predicts_fit` as
    ``extra_safety_margin``: positive shrinks the trusted window (math too
    optimistic), negative widens it (math too pessimistic). ``oom_floor``
    is the hardest bias measured at a NON-fitting probe — ``applied`` never
    drops below it, so a too-optimistic slope cannot re-trigger a proven
    OOM (oscillation guard). ``measured`` gates the pre-measurement trust
    floor in ``_pick_next_ctx``.
    """
    cache_key: tuple[str, tuple[str, ...], str]
    applied: int = 0
    measured: bool = False
    oom_floor: int | None = None

    def observe(
        self, pred_min_mb: int, real_min_mb: int, fits: bool,
    ) -> tuple[int, int] | None:
        """Learn from one probe measurement; returns ``(old, new)`` when the
        applied bias changed, ``None`` otherwise. Caches the new value for
        cross-variant seeding either way."""
        raw = pred_min_mb - real_min_mb
        if not fits:
            self.oom_floor = (
                raw if self.oom_floor is None else max(self.oom_floor, raw)
            )
        new = raw if self.oom_floor is None else max(raw, self.oom_floor)
        old = self.applied
        self.applied = new
        self.measured = True
        _MODEL_BIAS_CACHE[self.cache_key] = new
        return (old, new) if new != old else None


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
    ``budget.safety_margin`` — the observed math-vs-real bias
    (predicted_free − measured_free) from previous probes. May be NEGATIVE
    when the cost model under-predicts free VRAM (fit-params overstating
    compute buffers): the threshold then drops below the safety margin,
    which is sound because the REAL free will exceed the prediction by
    exactly that bias.
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
