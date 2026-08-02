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
- TTS-Container (XTTS, MOSS, …) und das Vigilantia-VLM (Ollama) belegen
  jeweils einen Teil einer GPU, NICHT eine ganze GPU. Sie laufen auf dem
  **Side-Channel-Tier** — der Compute-Klasse unterhalb der schnellsten,
  die fürs Haupt-LLM reserviert bleibt. Details siehe Abschnitt
  [Side-Channel-Platzierung](#side-channel-platzierung-vlm--tts).

## Sortierung (generisch)

GPUs werden sortiert nach:

1. **Compute Capability** (desc) — RTX 8000 (7.5) vor V100 (7.0) vor P40 (6.1)
2. **VRAM-Total** (desc) als Tiebreaker bei gleicher CC — z.B. zwei RTX 8000 ×
   48 GB sind gleichberechtigt nach diesem Kriterium
3. **CUDA-ID** (asc) als finaler Tiebreaker

Implementiert in [`_gpu_ranking()`](../../../aifred/lib/process_utils.py).

## Side-Channel-Platzierung (VLM + TTS)

> SSOT: [`aifred/lib/vision_gpu_select.py`](../../../aifred/lib/vision_gpu_select.py).
> Hardware-agnostisch — es wird **nichts** auf „immer V100" hartkodiert.

Die schnellste Compute-Klasse bleibt komplett frei für die Chat-LLMs
(Haupt + Automatik via llama-swap). Die Side-Channels — das
Vigilantia-VLM (Ollama) und die TTS-Container — laufen auf dem
**Side-Channel-Tier**: der Compute-Klasse direkt darunter.

### Tier-Bildung (`_side_channel_tier()`)

1. **Kandidaten** = alle Karten **unterhalb** der schnellsten
   Compute-Klasse (Top-Tier bleibt LLM-only).
2. **Homogenes Setup** (alle Karten gleiche Klasse, z.B. nur V100s):
   die schnellste Karte bleibt fürs LLM, alle übrigen bilden den Tier.
3. **Compute-Floor (weich):** Kandidaten mit Compute ≥ 7.0 (Volta+)
   werden bevorzugt. Eine P40 (Pascal, 6.1) wird nur dann
   Side-Channel-Host, wenn es gar keine schnellere Karte gibt
   (letzter Notnagel statt „keine Vision"). Konstante:
   `SIDE_CHANNEL_MIN_COMPUTE = (7, 0)`.

### Aufteilung TTS vs. VLM

- **`pick_tts_gpu()`** → erste Karte des Tiers.
- **`pick_vlm_gpu()`** → zweite Karte des Tiers; gibt es nur eine,
  teilt sich das VLM sie mit dem TTS (wie bisher).
- **`pick_face_gpu()`** → folgt dem VLM (InsightFace ist mit ~280 MB
  winzig und gehört thematisch zur Vision).

Sobald der Tier ≥ 2 Karten hat, gibt es **keine VRAM-Konkurrenz** mehr
zwischen TTS-Container und VLM auf einer Karte. Vorher konnten z.B.
Fish-TTS und Vigilantia-8B nicht koexistieren (Combo-Capacity-Check
verwarf das Profil). Bei einer einzelnen Tier-Karte greift dieser
Check weiterhin: passen TTS-Reserve + VLM-Reserve nicht zusammen drauf,
fällt genau diese Combo raus (Rest läuft).

### Beispiele

| Setup | LLM-Tier | TTS | VLM |
|---|---|---|---|
| 2× RTX 8000 + 1× V100 + 2× P40 | RTX 8000 ×2 | V100 | V100 (geteilt, P40 per Floor raus) |
| 2× RTX 8000 + 3× V100 (heute) | RTX 8000 ×2 | V100 #1 | V100 #2 |
| 3× RTX 8000 | RTX 8000 #1 | RTX 8000 #2 | RTX 8000 #3 |
| nur P40s | P40 #1 | P40 #2 | P40 #3 (weicher Fallback) |
| 1× RTX 8000 + 1× P40 | RTX 8000 | P40 | P40 (Notnagel) |

### P40-Floor: Messdaten

Gemessen (qwen3-vl Q8_0, vlm_stress_image, warm, 100 Decode-Tokens):

| GPU | Prefill 4B | Prefill 8B | Decode 4B | Decode 8B |
|---|---|---|---|---|
| V100 | 0,94 s | 1,92 s | 96,6 tok/s | 68,8 tok/s |
| RTX 8000 | 1,03 s | 2,34 s | 93,0 tok/s | 59,1 tok/s |
| **P40** | **4,07 s** | **6,89 s** | **45,7 tok/s** | **29,0 tok/s** |

Eine komplette 8B-Analyse (Prefill + Decode, Modell resident) kostet auf
der V100 **~3,4 s**, auf der P40 **~10,3 s** (3×). Der Prefill — das
Vision-Encoding, dominanter Anteil bei VLM — ist auf der P40 3,6–4,3×
langsamer. Daher der Floor: Pascal-Karten sind als Vision-Host die
falsche Wahl, solange etwas Schnelleres da ist.

### Deployment-Hinweis (Ollama)

Die Kalibration rechnet das VLM auf der vom Picker gewählten Karte ein.
Damit Ollama das Modell zur Laufzeit auch dort lädt, muss der
systemd-Drop-in (`CUDA_VISIBLE_DEVICES`, siehe `ollama_override_text()`)
auf diese Karte gepinnt sein — Ollama wählt sonst greedy first-fit.
Bei einer einzelnen Tier-Karte ist das identisch zum bisherigen Pin,
also kein Handlungsbedarf; relevant sobald TTS und VLM auf zwei
verschiedene Karten aufgeteilt werden.

## User-Präferenzen (verbindlich)

In dieser Reihenfolge:

1. **Geringste GPU-Anzahl** — weniger Inter-GPU-Sync = schneller. Wenn
   `[a,b,c,0]` über 3 GPUs nativen Kontext schafft, NIEMALS `[w,x,y,z]` über
   4 GPUs vorziehen.
2. **Schnellste GPU-Klasse zuerst füllen** — Layer landen primär auf RTX
   8000s, P40s nur als Spillover.
3. **Knallvoll bis Safety-Margin** ([`LLAMACPP_VRAM_SAFETY_MARGIN`](../../../aifred/lib/config.py),
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

## First-GPU-Handicap

Innerhalb der schnellsten Compute-Klasse wird eine GPU als `first_in_class`
markiert — die mit dem geringsten Free-VRAM (üblicherweise die
display-tragende GPU bei Desktop-Systemen). Dieser GPU rechnet der Optimizer
einen **Handicap** vom nutzbaren VRAM ab, damit sie am Ende nicht knapper
wird als ihre Geschwister.

**Zwei Effekte, die durch das Handicap ausgeglichen werden:**

1. **Display/Compositor-Overhead** — die display-tragende GPU hat im Idle
   schon einige hundert MB belegt (X-Server, Compositor, Browser-GPU-
   Beschleunigung).
2. **KV-Cache-/Output-Tensor-Asymmetrie** — llama.cpp pinnt mit
   `-sm layer` den Output-Tensor und Teile des KV-Cache-Setups auf die
   erste CUDA-Device. Dadurch wird GPU0 auch ohne Display stärker
   belastet als die Geschwister mit derselben Layer-Anzahl.

**Bemessung:**

- Empirisch gemessen als `max_sibling_free − first.free_mb` innerhalb der
  schnellsten Klasse.
- **Floor:** `_MIN_FIRST_GPU_HANDICAP_MB` = **256 MB** (immer mindestens).
- **Ceiling:** Wenn die gemessene Differenz > `_HARDWARE_HANDICAP_THRESHOLD_MB`
  (500 MB), fällt das Handicap auf den Floor zurück. Sonst würde ein bereits
  geladenes Fremd-Modell auf GPU0 doppelt abgezogen.
- Nur eine GPU in der schnellsten Klasse → Floor (kein Geschwister-Vergleich
  möglich).

Implementiert in [`measure_first_gpu_handicap()`](../../../aifred/lib/calibration/gpu.py).
Im Log sichtbar als Zeile `📊 first-GPU handicap: <N> MB`.

**Praktischer Effekt:** Im Split `[27, 29, 19, 14, 5]` für ein 5-GPU-235B-
Modell hat GPU1 zwei Layer mehr als GPU0 — genau weil GPU0 den Handicap
bekam und sich beide am Ende **gleich knapp** an der Safety-Margin treffen.

## Algorithmus

### Layer-Shift: Glas-Kaskade (SSOT)

Wenn ein Probe OOMt, wird umverteilt — genau **ein ganzer Layer** von der
überladenen GPU weg. Sowohl der Blind-Shift in Phase 1
([`_shift_one_layer_blind()`](../../../aifred/lib/calibration/flow.py)) als auch
das messgestützte Refinement
([`_refine_split_from_measurement()`](../../../aifred/lib/calibration/flow.py))
wählen das Ziel über **dieselbe** SSOT-Funktion
[`_cascade_destination()`](../../../aifred/lib/calibration/flow.py). Zwei
verschiedene Verteil-Philosophien für dasselbe Problem sind verboten
(siehe [First-GPU-Handicap](#first-gpu-handicap) und die
Projektregel „einmal getroffene Architekturentscheidungen strikt
durchhalten").

**Kaskade statt „leerste GPU":** Der Layer läuft nicht zur global leersten
Karte, sondern **über** zur nächsten Karte in Fastest-First-Reihenfolge, die
ihn noch trägt. `_cascade_destination()` iteriert `src+1 … len(gpus)-1` und
nimmt die **erste** GPU `i` mit
`reserve_adjusted_free[i] − step × layer_cost[i] ≥ min_free`. Findet keine
nachfolgende Karte Platz, gibt es kein Ziel (→ ctx-Shrink als Notausgang).
So bleibt die schnellste Klasse randvoll und der Spillover fließt geordnet
nach unten — die Glas-Kaskade aus den [User-Präferenzen](#user-präferenzen-verbindlich).

**Ganze Layer, nie Bruchteile (`_STEP = 1.0`):** llama.cpp mit `-sm layer`
platziert ausschließlich **ganze** Layer; ein fraktionaler `--tensor-split`
wird intern auf ganze Layer gerundet. Ein 0,5er-Shift bewegt darum physisch
oft **gar nichts**, kostet aber einen vollen Modell-Reload (bei einem 122B-
Modell ~125 GB Lesen). Deshalb bewegt ein Shift immer genau einen ganzen
Layer, und der finale fraktionale Split wird per
[`_quantize_split_to_layers()`](../../../aifred/lib/calibration/flow.py)
(Largest-Remainder-Rundung) auf ganze Layer gerundet, die exakt
`total_layers` summieren.

**Reserve-adjusted free:** Geprüft wird nicht der rohe nvidia-smi-Free-Wert,
sondern der um Side-Channel-Reserven (residente TTS-/VLM-Container)
bereinigte `load_min_free`. Eine V100, die 14 GB „frei" meldet, aber 14 GB
für einen laufenden TTS-Container reserviert, gilt als **voll** → der Shift
überspringt sie. Ohne diese Bereinigung würde ein Layer auf eine nur nominal
freie Karte gedumpt und der nächste Probe OOMt erneut.

**Idle-Skip bei gelocktem Active-Set:** Trägt der Aufrufer `keep_active_set`
(Speed-Variante, konstante GPU-Zahl), überspringt die Kaskade idle GPUs
(`split[i] == 0`) — sonst würde ein Shift die Speed-Variante heimlich zur
Base-Konfig aufblähen.

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
        shift 1 GANZEN Layer via _cascade_destination (fastest-first,
          reserve-adjusted, idle NICHT aktivieren — hält n GPUs konstant)
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
      1. Shifts bei target_ctx (max 15) via _cascade_destination
         mit keep_active_set=True — KEIN Aktivieren idle GPUs
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

## Betrieb: Gate, Cancel, Timeout

Die Kalibration läuft als Reflex-Background-Event
([`calibrate_context`](../../../aifred/state/_calibration_mixin.py), `@rx.event(background=True)`),
damit die UI während der minutenlangen Probes bedienbar bleibt (Abbrechen-
Button, Debug-Konsole). Progress-Meldungen laufen über einen modulweiten
Puffer, der unter dem State-Lock geleert wird — so triggert das Debugging
keinen Session-Sync-Sturm.

- **Prozessweites Inferenz-Gate**
  ([`calibration_gate.py`](../../../aifred/lib/calibration_gate.py)): Während
  einer laufenden Kalibration wird die reguläre Chat-Inferenz geblockt —
  ein zweiter llama-server auf denselben GPUs würde die VRAM-Messung
  verfälschen. `set_calibration_active()` / `is_calibration_active()`.
- **Sofort-Abbruch:** Der Abbrechen-Button setzt `request_cancel()`; die
  Verify-Schleife
  ([`verifier.py`](../../../aifred/lib/calibration/verifier.py)) prüft
  `is_cancel_requested()` beim Server-Warten und vor jedem Spawn und beendet
  Test-Server sauber.
- **Größen-skalierter Health-Timeout:** Der Ladetimeout wächst mit der
  Modellgröße (`LLAMACPP_HEALTH_TIMEOUT_PER_GB` in config.py, Summe über
  Multi-Part-GGUF). Ein 122B-Modell braucht ~750 s zum Laden — ein fixer
  360-s-Timeout meldete früher fälschlich „Fehlschlag", obwohl der Server
  noch lud.

## KI-Calibration (Alternative)

Bei `calibration_mode = "ai"`: ein DashScope-Qwen-Agent steuert den Loop
über Function Calls (`estimate_config`, `probe_config`, `finalize`).
Folgt der gleichen Strategie via System-Prompt
([prompts/de/calibration/system.txt](../../../prompts/de/calibration/system.txt)).
Vorteil: kann ungewöhnliche Hardware-Mixe (z.B. heterogene Karten) besser
handhaben als der deterministische Algorithmus.

## Was NIEMALS gemacht wird

- Layer "ausbalanciert verteilen" damit alle GPUs gleich free haben — das
  aktiviert unnötig zusätzliche GPUs.
- ctx über `native_context` pushen — physikalisch unmöglich, llama.cpp clampt.
- Mehr GPUs aktivieren als nötig "weil's etwas mehr ctx geben würde".
- ctx reduzieren wenn der Layer-Shift-Loop noch nicht ausgeschöpft ist.
- Bruchteile eines Layers shiften — llama.cpp rundet ohnehin auf ganze
  Layer, ein Sub-Layer-Shift bewegt physisch oft nichts und verbrennt nur
  einen vollen Modell-Reload.
- Einen Layer auf eine GPU dumpen, deren freier VRAM nur nominal frei ist
  (Side-Channel-Reserve) — immer reserve-adjusted prüfen.

## Wichtige Invarianten

- `len(active_gpus_in_split) ≤ len(active_gpus_in_speed_split)` ist NICHT zu
  garantieren — Speed darf weniger GPUs haben, das ist sein Sinn.
- `base_split[i] == 0` für eine GPU bedeutet diese GPU ist ungenutzt — kein
  CUDA_VISIBLE_DEVICES nötig (llama.cpp ignoriert sie automatisch).
- Compute-Capability-Sortierung ist die einzige authoritative Quelle für
  Speed-Klassen — keine Hardcoded-Listen ("RTX 8000 ist schnell").

## Referenzen im Code

- Algorithmus: [`aifred/lib/calibration/flow.py`](../../../aifred/lib/calibration/flow.py)
  - `calibrate_llamacpp_model()` — Entry Point
  - `_verify_and_refine()` — Verify + Shift + Native-Push
  - `_shift_one_layer_blind()` — Blind-Shift (Phase 1)
  - `_refine_split_from_measurement()` — messgestütztes Refinement
  - `_cascade_destination()` — SSOT Ziel-Wahl (Kaskade, reserve-adjusted, idle-skip)
  - `_quantize_split_to_layers()` — fraktionalen Split auf ganze Layer runden
- Optimizer: [`aifred/lib/calibration/optimizer.py`](../../../aifred/lib/calibration/optimizer.py)
  - `fill_fastest_first()` — greedy fill nach Speed-Klasse
- Hardware: [`aifred/lib/process_utils.py`](../../../aifred/lib/process_utils.py)
  - `_gpu_ranking()` — Compute-Capability-Sortierung
  - `get_tts_gpu_uuid()` — TTS-GPU-Pinning (UUID, via `pick_tts_gpu`)
- Side-Channel-Platzierung: [`aifred/lib/vision_gpu_select.py`](../../../aifred/lib/vision_gpu_select.py)
  - `_side_channel_tier()` — Tier-Bildung + Compute-Floor
  - `pick_tts_gpu()` / `pick_vlm_gpu()` / `pick_face_gpu()` — Karten-Wahl
- KI-Variante: [`aifred/lib/calibration/ai_agent.py`](../../../aifred/lib/calibration/ai_agent.py)
- Prompt: [`prompts/de/calibration/system.txt`](../../../prompts/de/calibration/system.txt)
