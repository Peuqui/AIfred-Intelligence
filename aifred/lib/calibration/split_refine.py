"""Split-Verfeinerung aus Messwerten.

Glas-Kaskade (Blind-Shift + Smart-Refine, gemeinsame Zielwahl-SSOT
``_cascade_destination``) und der kontext-maximierende Swap für den
Upward-Push. Helfer-Schicht: darf :mod:`flow` NICHT importieren.
"""

from __future__ import annotations

from typing import Sequence

from ..formatting import format_number
from .types import Budget, GPU
from .verifier import VerifyResult


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

    Hält stromabwärts keine Karte die Reserve (typisch: ``src`` IST die
    letzte Karte der Kaskade, wie beim 397B-8b-Combo-Lauf auf CUDA4),
    fällt die Wahl auf STROMAUFWÄRTS zurück: unter allen Karten vor
    ``src``, die dieselben Filter bestehen, gewinnt die mit dem größten
    Rest-Headroom nach ``+step`` (highest-ceiling destination, analog
    :func:`_context_refine_swap`). Ohne ``free_estimate`` gibt es
    stromaufwärts KEINEN Rückfall — blind auf die (meist randvollen)
    schnellen Karten zu raten wäre eine OOM-Schleife.

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

    # Upstream-Fallback (nur mit Frei-Schätzung): größter Rest-Headroom
    # gewinnt — dieselben Filter wie stromabwärts.
    if not free_estimate:
        return None
    best: int | None = None
    best_headroom = 0.0
    for i in range(src):
        if i in blocked_dest:
            continue
        if keep_active_set and layers[i] <= 0:
            continue
        if i >= len(free_estimate):
            continue
        cost = step * (
            layer_cost_per_gpu[i] if i < len(layer_cost_per_gpu) else 0.0
        )
        headroom = free_estimate[i] - cost
        if headroom >= min_free_mb and (best is None or headroom > best_headroom):
            best = i
            best_headroom = headroom
    return best


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
    card (downstream first, upstream as fallback) can take the layer
    within the reserve; ``reason`` is a short human string the caller logs.

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
            f"CUDA{bottleneck} tight at {b_free} MB — no card (downstream "
            f"or upstream) holds {step:g} more layer(s) within reserve"
        )

    new_split = list(current_split)
    new_split[bottleneck] -= step
    new_split[dest] += step
    return (
        tuple(new_split),
        f"CUDA{bottleneck} ({b_free} MB) → CUDA{dest} ({step:g} layer, cascade)",
    )


def _measured_split_ceiling(
    layers: list[int],
    free: list[float],
    slope_per_layer: "Sequence[float]",
    current_context: int,
    safety_margin: int,
) -> float:
    """Analytical ctx ceiling of a split, anchored to MEASURED free VRAM.

    Per active card: ``current_context + (free - margin) / (layers ×
    per-layer KV-slope)`` — the ctx at which that card's measured free
    is eaten by KV growth. The split ceiling is the minimum. Anchoring
    on the measured post-load reality (not the fit-params intercepts)
    is what makes this more generous — and more truthful — than the
    projection math. SSOT shared by :func:`_context_refine_swap` and
    the upward search's silent-crash rescue probe.
    """
    def _card(i: int) -> float:
        kv_slope = layers[i] * slope_per_layer[i]
        if layers[i] <= 0 or kv_slope <= 0:
            return float("inf")
        return current_context + (free[i] - safety_margin) / kv_slope

    return min(
        (_card(i) for i in range(len(layers)) if layers[i] > 0),
        default=float("inf"),
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

    # Anchored to the MEASURED reality (post-load truth), not the pre-load
    # plan — that reality gap is exactly why we are in the upward push.
    def _split_ceiling(layers: list[int], free: list[float]) -> float:
        return _measured_split_ceiling(
            layers, free, slope_per_layer, current_context, sm,
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
    die Reserve nicht mehr hält). Hält stromabwärts nichts die Reserve,
    fällt die Zielwahl auf stromaufwärts zurück (``_cascade_destination``).

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
