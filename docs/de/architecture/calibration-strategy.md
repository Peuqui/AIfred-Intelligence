# Calibration-Strategie

> SSOT für die Layer-Distribution-Strategie. Wird verwendet von Algorithmus
> und KI-Agent. Nicht doppelt diskutieren — bei Unklarheiten hier nachsehen
> oder diese Datei aktualisieren.

## Ziel

Für ein gegebenes Modell auf einem Multi-GPU-Rig: finde die Konfiguration mit
**maximalem Kontext bei minimaler GPU-Anzahl**, plus optional eine
Speed-Variante mit weniger GPUs (= weniger Inter-GPU-Sync = höherer Throughput)
bei reduziertem Kontext.

## Hardware-Annahmen

- Mehrere GPUs verschiedener Speed-Klassen (Compute Capability + VRAM-Größe)
- TTS-Container (XTTS, MOSS) belegen einen Teil einer GPU, NICHT eine ganze
  GPU. Aktuelle Pinning auf zweitschnellster GPU der schnellsten Klasse
  (z.B. RTX 8000 #2). Konfigurierbar via `get_tts_gpu_id()`.
- Sobald V100 verfügbar: TTS auf V100 → LLM bekommt RTX 8000 #2 zurück.

## Sortierung (generisch)

GPUs werden sortiert nach:

1. **Compute Capability** (desc) — RTX 8000 (7.5) vor V100 (7.0) vor P40 (6.1)
2. **VRAM-Total** (desc) als Tiebreaker bei gleicher CC — z.B. zwei RTX 8000 ×
   48 GB sind gleichberechtigt nach diesem Kriterium
3. **CUDA-ID** (asc) als finaler Tiebreaker

Implementiert in [`_gpu_ranking()`](../../aifred/lib/process_utils.py).

## User-Präferenzen (verbindlich)

In dieser Reihenfolge:

1. **Geringste GPU-Anzahl** — weniger Inter-GPU-Sync = schneller. Wenn
   `[a,b,c,0]` über 3 GPUs nativen Kontext schafft, NIEMALS `[w,x,y,z]` über
   4 GPUs vorziehen.
2. **Schnellste GPU-Klasse zuerst füllen** — Layer landen primär auf RTX
   8000s, P40s nur als Spillover.
3. **Knallvoll bis Safety-Margin** ([`LLAMACPP_VRAM_SAFETY_MARGIN`](../../aifred/lib/config.py),
   192 MB Linux, 1536 MB WSL) — KEIN Headroom-Verteilen. Eine GPU mit 2 GB
   free ist ok, eine mit 20 GB free ist Verschwendung wenn wir nicht-aktive
   GPUs hinzunehmen müssten.
4. **Nativen Kontext erreichen** — niemals reduzieren wenn vermeidbar.
   `model.native_context` ist die HARTE Obergrenze (llama.cpp clampt darüber).

Beispiel-Splits für 80B-Modell, 4 GPUs (2× RTX 8000, 2× P40):

| Split | Bewertung |
|---|---|
| `[23, 23, 2, 0]` | ✅ GUT — fastest GPUs nahezu voll, 2 Layer Spillover, 4. GPU idle |
| `[23, 24, 1, 0]` | ❌ OOM — eine RTX 8000 mit 24 Layern hat keinen Platz mehr für KV-Cache |
| `[22, 22, 9, 9]` | ❌ schlecht — aktiviert unnötig 4. GPU, lässt Headroom auf RTX 8000s liegen |

## Algorithmus

### Phase 1: Min-GPUs für native ctx

```
für n in 1..len(gpus):                          // sortiert fastest-first
    candidate = math_estimate(n, native_ctx)    // 1-2 s, kein Modell-Load
    wenn candidate fail oder ctx < native:
        next n                                  // Math sagt nein → kein Probe
    
    real_probe(candidate.split, native_ctx)     // 30-90 s
    wenn passt: BASE = candidate; BREAK
    
    // Probe schlug fehl trotz Math-OK
    // SHIFT-LOOP läuft bei NATIVE ctx (nicht shrunk!) — Ziel: max ctx halten
    bis 15 Layer-Shifts:
        shift 1 Layer von voller zu leerster ACTIVE GPU
        // (idle GPU NICHT aktivieren — hält n GPUs konstant)
        real_probe(shifted_split, native_ctx)
        wenn passt: BASE = candidate mit shifted_split; BREAK
    
    // ctx-shrink NUR als letzter Ausweg, wenn alle 15 Shifts bei native fail
    wenn alle Shifts fail:
        shrunk_ctx = _shrink_to_fit(...)
        real_probe(current_split, shrunk_ctx)
        wenn passt: BASE = candidate mit shrunk_ctx
    
    // Wenn nichts → next n
    wenn n == len(gpus): kein Fit (suggest hybrid)
```

**Reihenfolge ist verbindlich:** zuerst Layer-Shift bei native ctx (15
Versuche), dann erst ctx-shrink als Notfall. NIE umgekehrt — Layer-
Verteilung kann meist OOM beheben ohne ctx zu opfern.

**Upward push (Step 3):** Nach erfolgreichem Verify+Refinement, wenn
`current_ctx < native_context` UND tightest aktive GPU noch
`> 2 × safety_margin` free hat → binary search ctx aufwärts. Math-
Projektion ist konservativ; real-world Probe-Headroom kann mehr ctx
tragen. Besonders relevant für Speed-Variante (target_ctx kommt aus
2-GPU-Math-Estimate, oft kleiner als real machbar).

### Phase 2: Speed-Variante (n_speed < n_base)

Speed-Variante = weniger GPUs als Base, ctx darf reduziert werden.
**Active set ist gelockt**: der Algorithmus darf KEINE idle GPUs
aktivieren während des Shift-Loops, sonst landet man bei der Base-Konfig.

```
für n_speed in (n_base - 1) downto 1:
    candidate = math_estimate für n_speed GPUs
    wenn ctx >= MIN_USEFUL_CONTEXT_TOKENS: real_probe(candidate.split, target_ctx)
    
    bei OOM, in dieser Reihenfolge:
      1. Shifts bei target_ctx (max 15) — KEIN Aktivieren idle GPUs
      2. Wenn alle Shifts fail: BINARY SEARCH ctx runter
         lo = MIN_USEFUL_CONTEXT_TOKENS (32768 aus config.py)
         hi = target_ctx
         iteriere bis hi - lo < precision (256 tokens):
             mid = (lo+hi)/2
             real_probe(mid)
             fit → lo = mid (probiere höher)
             fail → hi = mid (geh tiefer)
         resultiert in HÖCHSTEM passenden ctx
    
    SPEED = (best_split, best_ctx, n_speed)
    BREAK aus n_speed-Loop
```

**Drop-Bedingungen für Speed-Variante:**
- Speed-ctx ≥ native_ctx UND gleicher KV-Quant → promoted to Base (Speed-
  Variante ist strikt besser, einzige config wird geschrieben)
- Speed-Split == Base-Split → keine Speed-Variante geschrieben (kein Gain)

User wählt zwischen BASE (max ctx) und SPEED (weniger GPUs, reduzierter
ctx). Beide werden in llama-swap als separate Konfig geschrieben
(`<model>` und `<model>-speed`).

### Phase 3: TTS-Varianten

Pro TTS-Backend (XTTS, MOSS):

1. TTS-Container starten → belegt VRAM auf der TTS-GPU
2. Live-Hardware-Erkennung: `enumerate_gpus()` sieht aktuelle Free-VRAM-Werte
3. **Vollständigen Calibration-Generator laufen lassen** (Phase 1 + Phase 2):
   - Phase 1: Estimate + Probe + 15-Layer-Shifts bei native ctx, dann optional
     ctx-Shrink
   - Phase 2: Speed-Variante (n_speed < n_base) mit Binary Search bis
     MIN_USEFUL_CONTEXT_TOKENS
4. Resultate als `<model>-tts-<backend>` (Base) UND ggf.
   `<model>-tts-<backend>-speed` (Speed-Variante) in llama-swap config
   schreiben. Speed-Variante wird übersprungen wenn:
   - `n_base == min_gpus_for_weights` (kein Speed-Spielraum) — typisch bei
     großen Modellen mit MOSS, weil 2 GPUs für Modell+MOSS-Container nicht
     reichen
   - Speed-Split == Base-Split (kein Speed-Gain möglich)

**Generisch:** Egal ob TTS auf GPU1 (heute) oder V100 (Zukunft) — der
Algorithmus passt sich automatisch an, weil free VRAM live gemessen wird.
Der Algorithmus ist identisch zum Base-Pfad, nur mit anderen
Free-VRAM-Werten als Input.

**Ctx-Reduktion nur wenn unvermeidbar:** Mit Layer-Shifts bei native ctx
versucht der Algorithmus aggressiv den nativen Kontext zu halten. Erst wenn
auch das nicht reicht ("die GPUs sind alle wirklich voll") wird ctx
reduziert. Bei Speed via Binary Search bis MIN_USEFUL.

## Estimate vs. Probe

- **Estimate** (`_project_cell` / `llama-fit-params`, 1-2 s): Math-Projektion
  ohne Modell-Load. Liefert vorhergesagten Free-VRAM pro GPU. Optimistisch
  bei MoE-Modellen — runtime-aktivierungsspeicher wird unterschätzt.
- **Probe** (`verify`, 30-90 s): Echter Server-Start + kurze Inferenz +
  VRAM-Messung. Die Wahrheit. Notwendig zum verifizieren weil Estimate kann
  bei native_ctx fälschlicherweise GO sagen wo Probe OOMt.

**Strategie:** Estimate als billiger Vorfilter — wenn Math sagt
"passt nicht bei native_ctx" → kein Probe. Wenn Math sagt "passt" → Probe.
Beim Probe-OOM: Layer-Shift, dann erneut Estimate-Filter (sehr billig)
gefolgt von Probe wenn Math grün.

**Bei Binary-Searches** (sowohl downward in Phase 2 / Speed-Variante als
auch upward in Step 3) wird der `_math_max_fitting_ctx`-Helper genutzt:
Math durchsucht das ganze Range in <100 ms (binary search via `_math_predicts_fit`)
und liefert den höchsten ctx den die fit-params-Modellierung als passend
einschätzt. Genau dort wird real-probed:
- Probe ✓ → das ist der neue known-fit (lo).
- Probe ✗ → Math war zu optimistisch, hi auf den failed-ctx setzen, Math
  searches erneut im engeren Range.

**Fine-Tuning unterhalb der Math-Auflösung:** Wenn Math nichts mehr
findet was höher als der aktuelle known-fit (lo) liegt, fällt der
Algorithmus auf reine Mittelwert-Bisection ohne Math zurück. Das ist
der Endspurt bis zur 256-Token-Genauigkeit (`LLAMACPP_CALIBRATION_PRECISION`
in config.py) — Math wird auf dieser Skala uninformativ, real-probes
sind hier die einzige verlässliche Quelle.

**Bias-Tracking (Math-vs-Real-Korrektur):** fit-params ist bei MoE-
Modellen oft konstant um z.B. ~110 MB zu optimistisch — wenn der
Algorithmus nicht aufpasst, läuft er in 256-Token-Schritten an dieser
Lücke entlang (jede Iteration → ~4 MB mehr free, also 25+ Probes für
100 MB-Korrektur, langsamer als plain Binary Search). Lösung: nach
jedem failed Probe wird `bias = predicted_min_free − measured_min_free`
ermittelt und als `extra_safety_margin` für die nächste Math-Suche
durchgereicht. Math wählt damit direkt einen realistischen ctx weiter
unten (nur 3–5 Probes statt 25+).

## KI-Calibration (Alternative)

Bei `calibration_mode = "ai"`: ein DashScope-Qwen-Agent steuert den Loop
über Function Calls (`estimate_config`, `probe_config`, `finalize`).
Folgt der gleichen Strategie via System-Prompt
([prompts/de/calibration/system.txt](../../prompts/de/calibration/system.txt)).
Vorteil: kann ungewöhnliche Hardware-Mixe (z.B. heterogene Karten) besser
handhaben als der deterministische Algorithmus.

## Was NIEMALS gemacht wird

- Layer "ausbalanciert verteilen" damit alle GPUs gleich free haben — das
  aktiviert unnötig zusätzliche GPUs.
- ctx über `native_context` pushen — physikalisch unmöglich, llama.cpp clampt.
- Mehr GPUs aktivieren als nötig "weil's etwas mehr ctx geben würde".
- ctx reduzieren wenn der Layer-Shift-Loop noch nicht ausgeschöpft ist.

## Wichtige Invarianten

- `len(active_gpus_in_split) ≤ len(active_gpus_in_speed_split)` ist NICHT zu
  garantieren — Speed darf weniger GPUs haben, das ist sein Sinn.
- `base_split[i] == 0` für eine GPU bedeutet diese GPU ist ungenutzt — kein
  CUDA_VISIBLE_DEVICES nötig (llama.cpp ignoriert sie automatisch).
- Compute-Capability-Sortierung ist die einzige authoritative Quelle für
  Speed-Klassen — keine Hardcoded-Listen ("RTX 8000 ist schnell").

## Referenzen im Code

- Algorithmus: [`aifred/lib/calibration/flow.py`](../../aifred/lib/calibration/flow.py)
  - `calibrate_llamacpp_model()` — Entry Point
  - `_verify_and_refine()` — Verify + Shift + Native-Push
  - `_shift_one_layer_blind()` — Layer-Shift-Logik
- Optimizer: [`aifred/lib/calibration/optimizer.py`](../../aifred/lib/calibration/optimizer.py)
  - `fill_fastest_first()` — greedy fill nach Speed-Klasse
- Hardware: [`aifred/lib/process_utils.py`](../../aifred/lib/process_utils.py)
  - `_gpu_ranking()` — Compute-Capability-Sortierung
  - `get_tts_gpu_id()` — TTS-GPU-Pinning
- KI-Variante: [`aifred/lib/calibration/ai_agent.py`](../../aifred/lib/calibration/ai_agent.py)
- Prompt: [`prompts/de/calibration/system.txt`](../../prompts/de/calibration/system.txt)
