# Calibration-Strategie

> **English version:** [calibration-strategy.md](../../en/architecture/calibration-strategy.md)

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
- Die GPU-TTS-Container (XTTS, MOSS, Fish-Speech, Qwen3-TTS — jede
  Engine mit `needs_gpu = True` in
  [`aifred/lib/tts_engines/`](../../../aifred/lib/tts_engines/)) und das
  Vigilantia-VLM (Ollama) belegen jeweils einen Teil einer GPU, NICHT eine
  ganze GPU. Sie teilen sich **eine Karte** des **Side-Channel-Tiers** — der
  Compute-Klasse unterhalb der schnellsten, die fürs Haupt-LLM reserviert
  bleibt. Details siehe Abschnitt
  [Side-Channel-Platzierung](#side-channel-platzierung-vlm--tts).

## Sortierung (generisch)

GPUs werden sortiert nach:

1. **Compute Capability** (desc) — RTX 8000 (7.5) vor V100 (7.0) vor P40 (6.1)
2. **VRAM-Total** (desc) als Tiebreaker bei gleicher CC — z.B. zwei RTX 8000 ×
   48 GB sind gleichberechtigt nach diesem Kriterium
3. **GPU-Name**, dann **UUID** (asc) als finale, deterministische Tiebreaker

Implementiert in [`enumerate_gpus()`](../../../aifred/lib/calibration/gpu.py).
GPUs werden über ihre NVIDIA-UUID identifiziert, und llama-server sieht sie
per `CUDA_VISIBLE_DEVICES=<UUIDs>` in genau dieser Reihenfolge. Der
Side-Channel-Picker rankt separat mit `_rank()` in
[`vision_gpu_select.py`](../../../aifred/lib/vision_gpu_select.py)
(Compute Capability desc, VRAM-Total desc, PCI_BUS_ID-Index asc).

## Side-Channel-Platzierung (VLM + TTS)

> SSOT: [`aifred/lib/vision_gpu_select.py`](../../../aifred/lib/vision_gpu_select.py).
> Hardware-agnostisch — es wird **nichts** auf „immer V100" hartkodiert.

Die schnellste Compute-Klasse bleibt komplett frei für die Chat-LLMs
(Haupt + Automatik via llama-swap). Die Side-Channels — das
Vigilantia-VLM (Ollama) und die TTS-Container — laufen auf dem
**Side-Channel-Tier**: der Compute-Klasse direkt darunter. Beide teilen
sich **eine Karte** dieses Tiers (Entscheidung 2026-08-29), damit alle
übrigen Karten für Backend-Topologien frei bleiben (z.B. TP2×PP2 über vier
Karten bei der vLLM-Kalibration).

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

### Gemeinsame Karte für TTS und VLM (`pick_side_channel_gpu()`)

- **`pick_side_channel_gpu()`** → die Sammelkarte: die **zweite** Karte des
  Tiers; hat der Tier nur eine Karte, diese.
- **Schwächste Anbindung zuerst:** Sitzen die Tier-Karten unterschiedlich
  tief im PCI-Baum (`attachment_depth()`, z.B. eine Karte hinter einem
  USB4-/Thunderbolt-Tunnel mit zusätzlichen Bridges), wird die am tiefsten
  angebundene Karte zur Sammelkarte. Side-Channels sind Einzelkarten-Lasten
  und vertragen den Tunnel; eine TP/PP-Gruppe nicht, weil dort jedes Token
  über alle ihre Karten synchronisiert. Bei gleicher Tiefe (oder
  unbekannten Bus-IDs) bleibt es bei „zweite Tier-Karte".
- **`pick_tts_gpu()`** und **`pick_vlm_gpu()`** liefern beide diese
  Sammelkarte. TTS-Container bekommen deren UUID über `get_tts_gpu_uuid()`
  ([`process_utils.py`](../../../aifred/lib/process_utils.py)).
- **InsightFace** (Gesichtserkennung) folgt dem VLM: `gpu_id: "auto"` in
  den `face_recognition`-Settings des Vision-Plugins wird über
  `resolve_gpu_id()` → `pick_vlm_gpu()` aufgelöst.

Der Preis der Sammelkarte: sehr große TTS-Engines (Fish, MOSS) passen nicht
mehr gleichzeitig mit dem VLM auf eine Karte. Das fängt der
Combo-Capacity-Check der Kalibration ab: übersteigen TTS-Reserve +
VLM-Reserve den Gesamt-VRAM der Sammelkarte, wird genau dieses
Combo-Profil nicht geschrieben (Rest läuft).

### Beispiele

| Setup | LLM-Tier | Gemeinsame TTS- + VLM-Karte |
|---|---|---|
| 2× RTX 8000 + 1× V100 + 2× P40 | RTX 8000 ×2 | V100 (einzige Tier-Karte, P40 per Floor raus) |
| 2× RTX 8000 + 3× V100 (heute) | RTX 8000 ×2 | V100 #2 — oder die tiefer angebundene V100, wenn die Tiefen abweichen (am Mini die getunnelte Karte, siehe `TestWeakAttachmentPreference` in [`tests/test_vision_gpu_select.py`](../../../tests/test_vision_gpu_select.py)) |
| 3× RTX 8000 | RTX 8000 #1 | RTX 8000 #3 (zweite Tier-Karte) |
| nur 3× P40 | P40 #1 | P40 #3 (weicher Fallback) |
| 1× RTX 8000 + 1× P40 | RTX 8000 | P40 (Notnagel) |

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
systemd-Drop-in (`CUDA_DEVICE_ORDER=PCI_BUS_ID` + `CUDA_VISIBLE_DEVICES`,
Text erzeugt von `ollama_override_text()`) auf diese Karte gepinnt sein —
Ollama wählt sonst greedy first-fit. Dieser VLM-Pin steckt in extern
verwalteten Configs (`ollama-vlm.service`, `-visiond`-Einträge der
llama-swap-Config), die **nicht** automatisch umgeschrieben werden. Deshalb
ist die Sammelkarte bewusst die zweite Tier-Karte, auf der das VLM schon
immer lag; TTS löst seine Karte selbst über `get_tts_gpu_uuid()` auf.
Ändert sich die Wahl des Pickers (Hardware-Umbau, Anbindungstiefe), musst
du den VLM-Pin von Hand nachziehen.

## User-Präferenzen (verbindlich)

In dieser Reihenfolge:

1. **Geringste GPU-Anzahl** — weniger Inter-GPU-Sync = schneller. Wenn
   `[a,b,c,0]` über 3 GPUs nativen Kontext schafft, NIEMALS `[w,x,y,z]` über
   4 GPUs vorziehen.
2. **Schnellste GPU-Klasse zuerst füllen** — Layer landen primär auf RTX
   8000s, P40s nur als Spillover.
3. **Knallvoll bis Safety-Margin** ([`LLAMACPP_VRAM_SAFETY_MARGIN`](../../../aifred/lib/config.py),
   192 MB Linux, 1536 MB unter WDDM, also WSL2/Windows) — KEIN Headroom-Verteilen. Eine GPU mit 2 GB
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
display-tragende GPU bei Desktop-Systemen). Sie ist die einzige markierte
Karte im ganzen System: in der gepinnten Füll-Reihenfolge ist sie
CUDA-Device 0; langsamere Klassen tragen keine ihrer Puffer und bekommen
kein Handicap. Dieser GPU rechnet der Optimizer
einen **Handicap** vom nutzbaren VRAM ab, damit sie am Ende nicht knapper
wird als ihre Geschwister.

**Zwei Effekte, die durch das Handicap ausgeglichen werden:**

1. **Display/Compositor-Overhead** — die display-tragende GPU hat im Idle
   schon einige hundert MB belegt (X-Server, Compositor, Browser-GPU-
   Beschleunigung).
2. **Main-Device-Puffer** — llama.cpp legt mit `-sm layer` seine
   Main-Device-Puffer (Logits/Output-Tensor, Compute-Workspace, MTP-Draft)
   auf die erste CUDA-Device. Dadurch wird GPU0 auch ohne Display stärker
   belastet als die Geschwister mit derselben Layer-Anzahl.

**Bemessung (zweistufig):**

1. **Idle-Messung** (Budget-Wert vor fit-params,
   [`measure_first_gpu_handicap()`](../../../aifred/lib/calibration/gpu.py)):
   - Empirisch gemessen als `max_sibling_free − first.free_mb` innerhalb der
     schnellsten Klasse.
   - **Floor:** `_MIN_FIRST_GPU_HANDICAP_MB` = **256 MB** (immer mindestens).
   - **Ceiling:** Wenn die gemessene Differenz > `_HARDWARE_HANDICAP_THRESHOLD_MB`
     (500 MB), fällt das Handicap auf den Floor zurück. Sonst würde ein bereits
     geladenes Fremd-Modell auf GPU0 doppelt abgezogen.
   - Nur eine GPU in der schnellsten Klasse → Floor (kein Geschwister-Vergleich
     möglich).
2. **Modellbasiert** (sobald fit-params gelaufen ist,
   [`first_gpu_handicap_mb()`](../../../aifred/lib/calibration/optimizer.py)):
   Das Idle-Delta sieht den transienten Load-Peak der Main-Device-Puffer
   nicht, fit-params schon — als Asymmetrie gegenüber den aktiven
   Geschwistern: `(base_overhead[first] − mean(base_overhead[siblings])) +
   max(0, Slope-Asymmetrie) × layers[first] × ctx`. Skaliert mit dem
   Ziel-Kontext; der 256-MB-Floor bleibt Untergrenze. `fill_fastest_first()`
   nutzt diesen Wert.

Im Log sichtbar als Zeile
`Free VRAM: …, first-GPU handicap (idle floor): <N> MB — model-derived per cell once fit-params ran`.

**Praktischer Effekt:** Im Split `[27, 29, 19, 14, 5]` für ein 5-GPU-235B-
Modell hat GPU1 zwei Layer mehr als GPU0 — genau weil GPU0 den Handicap
bekam und sich beide am Ende **gleich knapp** an der Safety-Margin treffen.

## Algorithmus

[`calibrate_llamacpp_model()`](../../../aifred/lib/calibration/flow.py)
durchläuft die Phasen unter denselben Namen wie seine Code-Abschnitte und
Log-Zeilen:

| Phase | Inhalt | Log-Marker |
|-------|--------|------------|
| A | GGUF-Metadaten, stabiler VRAM, Budget pro GPU (Margins, Side-Channel-Reserven) | `Reading GGUF metadata: …` |
| 1 | Base-Konfiguration: wenigste GPUs, höchste KV-Qualität bei nativem ctx | `Phase 1: searching …` |
| — | Hybrid (CPU-Offload), nur wenn Phase 1 nichts verifiziert und Hybrid in den Settings erlaubt ist | `No GPU-only configuration verified — trying hybrid` |
| E | Speed-Variante: weniger GPUs als die Base | `Phase E: speed variant …` |
| D | llama-swap-Einträge und VRAM-Cache schreiben | — |

Die TTS-/VLM-Varianten sind keine Phase dieses Laufs: Das Calibration-Mixin
kalibriert sie danach aus der fertigen Base (siehe
[TTS-Varianten](#tts-varianten-nach-dem-base-lauf)).

### Layer-Shift: Glas-Kaskade (SSOT)

Wenn ein Probe OOMt, wird umverteilt — genau **ein ganzer Layer** von der
überladenen GPU weg. Sowohl der Blind-Shift in Phase 1
([`_shift_one_layer_blind()`](../../../aifred/lib/calibration/split_refine.py)) als auch
das messgestützte Refinement
([`_refine_split_from_measurement()`](../../../aifred/lib/calibration/split_refine.py))
wählen das Ziel über **dieselbe** SSOT-Funktion
[`_cascade_destination()`](../../../aifred/lib/calibration/split_refine.py). Zwei
verschiedene Verteil-Philosophien für dasselbe Problem sind verboten
(siehe [First-GPU-Handicap](#first-gpu-handicap) und die
Projektregel „einmal getroffene Architekturentscheidungen strikt
durchhalten").

**Kaskade statt „leerste GPU":** Der Layer läuft nicht zur global leersten
Karte, sondern **über** zur nächsten Karte in Fastest-First-Reihenfolge, die
ihn noch trägt. Eine Karte `i` trägt ihn, wenn
`free_estimate[i] − step × layer_cost_per_gpu[i] ≥ min_free_mb`
(Kosten pro Layer = Gewicht + KV bei diesem ctx). `_cascade_destination()`
prüft in dieser Reihenfolge:

1. **Idle Karte vor `src`** (nur ohne `keep_active_set`): eine komplett
   leere Karte, die in der Reihenfolge früher kommt, ist per Sortierung
   schneller oder gleich schnell und frei — sie schlägt den Überlauf auf
   langsamere Karten.
2. **Stromabwärts:** iteriert `src+1 … len(gpus)-1` und nimmt die **erste**
   Karte, die den Layer trägt.
3. **Stromaufwärts-Rückfall:** Trägt ihn stromabwärts keine Karte (typisch:
   `src` ist die letzte Karte der Kaskade), gewinnt unter den Karten vor
   `src` die mit dem größten Rest-Headroom nach `+step`. Nur mit
   Frei-Schätzung — ohne sie wird stromaufwärts nicht geraten.

Trägt ihn keine, gibt es kein Ziel (→ ctx-Suche als Notausgang).
So bleibt die schnellste Klasse randvoll und der Spillover fließt geordnet
nach unten — die Glas-Kaskade aus den [User-Präferenzen](#user-präferenzen-verbindlich).

**Reserve-Karten sind nie Ziel (`blocked_dest`):** GPUs mit
Side-Channel-Reserve (TTS/VLM) sind als Shift-Ziel ausgeschlossen
([`_verify_and_refine()`](../../../aifred/lib/calibration/ctx_search.py)
bildet die Menge aus `budget.gpu_reserve_mb`). Ein Layer dort fräße genau
den Platz, den der Container später beansprucht. Die Quelle darf jede Karte
sein.

**Kontext-maximierender Swap zuerst:** Hat der gescheiterte Probe eine
Steady-State-Messung hinterlassen, versucht der Shift-Loop zuerst
[`_context_refine_swap()`](../../../aifred/lib/calibration/split_refine.py):
ein einzelner Ganz-Layer-Swap, der die ctx-limitierende Karte auf die Karte
mit der höchsten ctx-Decke entlastet (stromaufwärts erlaubt). Erst wenn der
nichts findet, läuft der Blind-Shift über `_cascade_destination()`.

**Ganze Layer, nie Bruchteile (`_STEP = 1.0` in `_shift_one_layer_blind()`):**
llama.cpp mit `-sm layer`
platziert ausschließlich **ganze** Layer; ein fraktionaler `--tensor-split`
wird intern auf ganze Layer gerundet. Ein 0,5er-Shift bewegt darum physisch
oft **gar nichts**, kostet aber einen vollen Modell-Reload (bei einem 122B-
Modell ~125 GB Lesen). Deshalb bewegt ein Shift immer genau einen ganzen
Layer, und der finale fraktionale Split wird per
[`_quantize_split_to_layers()`](../../../aifred/lib/calibration/fit_math.py)
(Largest-Remainder-Rundung) auf ganze Layer gerundet, die exakt
`total_layers` summieren.

**Reserve-adjusted free:** Geprüft wird nicht der rohe nvidia-smi-Free-Wert,
sondern der tiefste beim Laden beobachtete Frei-Stand (`load_min_free_mb`
des `VerifyResult`) abzüglich der Side-Channel-Reserven
(`budget.gpu_reserve_mb`). Eine V100, die 14 GB „frei" meldet, aber 14 GB
für einen TTS-Container reserviert, gilt als **voll** → der Shift
überspringt sie. Ohne diese Bereinigung würde ein Layer auf eine nur nominal
freie Karte gedumpt und der nächste Probe OOMt erneut.

**Idle-Skip bei gelocktem Active-Set:** Trägt der Aufrufer `keep_active_set`
(Speed-Variante, konstante GPU-Zahl), überspringt die Kaskade idle GPUs
(`split[i] == 0`) — sonst würde ein Shift die Speed-Variante heimlich zur
Base-Konfig aufblähen.

### Phase 1: Min-GPUs für native ctx

In [`calibrate_llamacpp_model()`](../../../aifred/lib/calibration/flow.py);
Verify, Shifts und ctx-Suche in
[`_verify_and_refine()`](../../../aifred/lib/calibration/ctx_search.py).
Die GPU-Sets liefert
[`_enumerate_gpu_configs()`](../../../aifred/lib/calibration/fit_math.py):
erst Einzel-GPUs (Compute-DESC), dann pro Anzahl `n` aufsteigend das
homogene Set einer Speed-Klasse, danach der Compute-first-Fill (die ersten
`n` Karten, Klassen gemischt).

```
für kv in KV-Stufen (f16 zuerst, dann q8_0, …):  // KV-Qualität vor GPU-Anzahl
  für active in _enumerate_gpu_configs(...):      // wenigste GPUs zuerst
    candidate = _project_cell(active, kv)         // Math, 1-2 s, kein Modell-Load
    wenn candidate fail oder ctx < native:
        nächste Config                            // Math sagt nein → kein Probe

    real_probe(candidate.split, native_ctx)       // 30-90 s
    wenn passt: BASE = candidate; BREAK

    // Probe schlug fehl trotz Math-OK
    // SHIFT-LOOP läuft bei NATIVE ctx (nicht shrunk!) — Ziel: max ctx halten
    bis 15 Layer-Shifts:
        mit Messung: zuerst _context_refine_swap
        sonst 1 GANZEN Layer von der OOM-Karte weg via
          _cascade_destination (fastest-first, reserve-adjusted,
          Reserve-Karten als Ziel gesperrt)
        real_probe(shifted_split, native_ctx)
        wenn passt: raus aus dem Shift-Loop
        Split schon gesehen (Oszillation) → Shift-Loop beenden

    // ctx-Suche NUR als letzter Ausweg, wenn die Shifts bei native erschöpft sind
    wenn weiter OOM:
        _binary_search_fitting_ctx(lo=MIN_USEFUL_CONTEXT_TOKENS, hi=native_ctx)
        // Math-geführt, bias-korrigiert, 256-Token-Präzision

    Ergebnis ≥ native → BASE; BREAK
    Ergebnis < native → als Fallback merken, nächste Config probieren

// keine Config erreicht native:
//   Best-Effort-Probe des besten unverifizierten Kandidaten (≥ MIN_USEFUL),
//   dann finale Auswahl über alle echten Messungen: höchste KV-Qualität,
//   innerhalb davon größter ctx
// immer noch nichts → Hybrid (nur wenn in den Settings erlaubt), sonst Fehler
```

**Reihenfolge ist verbindlich:** zuerst Layer-Shift bei native ctx (15
Versuche), dann erst ctx-Suche nach unten als Notfall. NIE umgekehrt — Layer-
Verteilung kann meist OOM beheben ohne ctx zu opfern.

**Upward push (Step 3):** Nach erfolgreichem Verify+Refinement, wenn
`current_ctx < native_context` (bei Varianten: `< ctx_ceiling`, z.B. der
Base-ctx) und eine Steady-State-Messung vorliegt → binary search ctx
aufwärts. Probe-first seit 2026-07-07: es gibt **kein** Headroom-Gate mehr
(früher nur bei `> 2 × safety_margin` free auf der engsten GPU — das
nagelte das 397B auf 89k fest, obwohl real ~171k liefen). Math-Projektion
ist konservativ; real-world Probe-Headroom kann mehr ctx tragen. Bei einem
OOM während des Pushs entlastet `_context_refine_swap()` die
ctx-limitierende Karte, bevor das Suchfenster schrumpft. Besonders relevant
für die Speed-Variante (ihr Start-ctx kommt aus dem Math-Estimate des
kleineren GPU-Sets, oft kleiner als real machbar).

### Phase E: Speed-Variante (n_speed < n_base)

Speed-Variante = weniger GPUs als Base, ctx darf reduziert werden.
**Active set ist gelockt**: der Algorithmus darf KEINE idle GPUs
aktivieren während des Shift-Loops, sonst landet man bei der Base-Konfig.

Nur wenn es mehr als eine GPU gibt und BASE mehr als eine nutzt.
Kandidatenwahl in
[`_find_speed_candidate()`](../../../aifred/lib/calibration/flow.py)
(nur Math, kein Probe):

```
wenn n_base <= find_min_gpus_for_weights(...): keine Speed-Variante

für jede Speed-Klasse (schnellste zuerst, kumulativ):
    für n in (kleinstmögliches n) .. (n_base - 1):
        candidate = Projektion (aus Phase 1 wiederverwendet bei gleichem
                    GPU-Set, sonst _project_cell; gleiche KV wie BASE)
        wenn candidate.max_ctx >= MIN_USEFUL_CONTEXT_TOKENS:
            candidate nehmen; Ende
    // nichts gefunden → um die nächstlangsamere Klasse erweitern

_verify_and_refine(candidate, lock_active_gpus=True):
    real_probe(candidate.split, candidate.max_ctx)
    bei OOM, in dieser Reihenfolge:
      1. Shifts (max 15) mit keep_active_set=True — KEIN Aktivieren idle GPUs
      2. Wenn die Shifts erschöpft sind: _binary_search_fitting_ctx
         lo = MIN_USEFUL_CONTEXT_TOKENS (32768 aus config.py)
         hi = candidate-ctx
         Math-geführt + Bias-Tracking, Mittelwert-Bisection im Endspurt
         bis 256-Token-Präzision
         → HÖCHSTER passender ctx
    danach Upward-Push (Step 3) bis native

SPEED = (best_split, best_ctx, n_speed)
```

**Drop-Bedingungen für Speed-Variante:**
- Speed-ctx ≥ native_ctx UND gleicher KV-Quant → promoted to Base (Speed-
  Variante ist strikt besser, einzige config wird geschrieben)
- Speed-Split == Base-Split → keine Speed-Variante geschrieben (kein Gain)

User wählt zwischen BASE (max ctx) und SPEED (weniger GPUs, reduzierter
ctx). Beide werden in llama-swap als separate Konfig geschrieben
(`<model>` und `<model>-speed`).

### TTS-Varianten (nach dem Base-Lauf)

Orchestriert im Calibration-Mixin
([`_calibration_mixin.py`](../../../aifred/state/_calibration_mixin.py)).
Pro installierter GPU-TTS-Engine (`installed_gpu_engines()` aus
[`tts_engines/registry.py`](../../../aifred/lib/tts_engines/registry.py) —
XTTS, MOSS, Fish-Speech, Qwen3-TTS, soweit ihr Docker-Image auf diesem Host
existiert), deren `|<engine>`-Zelle im Calibration-Picker angehakt ist
(`calibration_matrix`):

1. **Isolated Mode:** Ist das GPU-Set des Base- (bzw. Speed-)Profils
   disjunkt zur TTS-Karte (`side_channel_disjoint()` in
   [`llamaswap_io.py`](../../../aifred/lib/calibration/llamaswap_io.py)),
   wird das Profil unverändert als `<model>-tts-<engine>` (bzw. `-speed`)
   kopiert — keine Projektion, kein Probe. Sind so alle Kandidaten
   abgedeckt, ist die Engine fertig.
2. Sonst: TTS-Container starten → belegt VRAM auf der TTS-Karte. Die
   **TTS-Reserve** kommt aus `resolve_tts_reserve()`
   ([`tts_stress_burnin.py`](../../../aifred/lib/tts_stress_burnin.py)):
   gemessener Peak aus `data/tts_vram_cache.json` +
   `LLAMACPP_TTS_BURNIN_HEADROOM_MB` (512 MB); bei Cache-Miss läuft vorher
   der Stress-Burn-In.
3. **Fast Path:** [`calibrate_tts_variant_from_base()`](../../../aifred/lib/calibration/flow.py)
   projiziert **dasselbe aktive GPU-Set** wie BASE unter dem TTS-bewussten
   Free-VRAM neu (`enumerate_gpus()` live + Reserve) und fährt
   `_verify_and_refine()`; der ctx ist auf den Base-ctx gedeckelt (TTS nimmt
   nur VRAM weg, gibt nie welches dazu). Gibt es eine Speed-Variante, läuft
   derselbe Fast Path ein zweites Mal mit Speed-Split + Speed-ctx als „Base".
4. **Volle Kalibration nur als Rückfall:** Findet der Fast Path keinen Fit,
   läuft der vollständige Calibration-Generator (Phase A, 1, E) mit
   TTS-Karte und Reserve als Input.
5. Resultate als `<model>-tts-<engine>` (Base) UND ggf.
   `<model>-tts-<engine>-speed` (Speed-Variante) in llama-swap config
   schreiben. Die Speed-Variante entfällt, wenn:
   - `n_base <= find_min_gpus_for_weights` (kein Speed-Spielraum) — typisch
     bei großen Modellen mit MOSS, weil 2 GPUs für Modell+MOSS-Container
     nicht reichen
   - Speed-Split == Base-Split (kein Speed-Gain möglich)

VLM- und Combo-Varianten (TTS × VLM) laufen durch dasselbe
`calibrate_tts_variant_from_base()`, leiten ihren Split aber proportional
aus dem Base-Split ab — über
[`_derive_reserved_split()`](../../../aifred/lib/calibration/fit_math.py)
(entlastet jede reserve-belastete Karte) — und verifizieren mit
`lock_split=True`; frei ist dort nur der Kontext.

**Generisch:** Die TTS-Karte ist immer die gemeinsame Side-Channel-Karte
(`pick_tts_gpu()` → `get_tts_gpu_uuid()`), egal welche Karte das auf der
aktuellen Hardware ist — der Algorithmus passt sich automatisch an, weil
free VRAM live gemessen wird. Der Verify-/Refine-Loop ist identisch zum
Base-Pfad, nur mit anderen Free-VRAM-Werten und Reserven als Input.

**Ctx-Reduktion nur wenn unvermeidbar:** Mit Layer-Shifts bei native ctx
versucht der Algorithmus aggressiv den nativen Kontext zu halten. Erst wenn
auch das nicht reicht ("die GPUs sind alle wirklich voll") wird ctx
reduziert — bei jeder Variante über dieselbe Binary Search
(`_binary_search_fitting_ctx()`) bis MIN_USEFUL.

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

**Bei Binary-Searches** (sowohl downward nach erschöpften Shifts —
[`_binary_search_fitting_ctx()`](../../../aifred/lib/calibration/ctx_search.py),
SSOT für Base, Speed, VLM und Best-Effort — als auch upward in Step 3) wird
der [`_math_max_fitting_ctx`](../../../aifred/lib/calibration/fit_math.py)-Helper genutzt:
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
unten (nur 3–5 Probes statt 25+). Der Bias wird bidirektional geführt
(positiv = Math zu optimistisch, negativ = zu pessimistisch; `_learn_bias()`
in `ctx_search.py`) und vom OOM geseedet, der die Suche ausgelöst hat.

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

Bei `calibration_mode = "ai"` (Default: `"legacy"` = der Algorithmus): ein
Cloud-LLM steuert den Loop über Function Calls (`estimate_config`,
`probe_config`, `finalize`) —
[`calibrate_with_ai()`](../../../aifred/lib/calibration/ai_agent.py).
Provider, Modell und Reasoning-Toggle kommen aus dem System-Agenten
`calibration` in `data/agents.json` (`cloud_provider`, Default `qwen` =
DashScope; im Agent-Editor änderbar). In der UI ist die KI-Option nur mit
DashScope-Key aktiv. Folgt der gleichen Strategie via System-Prompt
([prompts/en/calibration/system.txt](../../../prompts/en/calibration/system.txt);
`ai_agent.py` lädt ihn immer mit `lang="en"`).

Umfang: BASE (`_try_ai_calibration()` in `flow.py`), die Speed-Variante
(das GPU-Set wählt `_find_speed_candidate()` deterministisch, die KI
optimiert ctx/Split auf genau diesem gelockten Set) und die
TTS-/VLM-/Combo-Zellen (`calibrate_tts_variant_from_base()` verzweigt im
KI-Modus auf `_ai_variant_from_base()`). Der KI-Modus ist **terminal**: bei
Fehlschlag meldet er einen Fehler und fällt nie auf den Algorithmus zurück.
Vorteil: kann ungewöhnliche Hardware-Mixe (z.B. heterogene Karten) besser
handhaben als der deterministische Algorithmus. Bewertung dieses Pfads:
[calibration-llm-challenge.md](calibration-llm-challenge.md).

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
- `base_split[i] == 0` für eine GPU bedeutet diese GPU ist ungenutzt. Das
  geschriebene Profil pinnt genau die aktiven Karten per UUID über
  `CUDA_VISIBLE_DEVICES` (in Kalibrations-Reihenfolge) und schreibt den
  Tensor-Split auf einen Wert pro aktiver Karte um
  (`update_llamaswap_cuda_visible()` in
  [`llamaswap_io.py`](../../../aifred/lib/calibration/llamaswap_io.py)) —
  Env und Split dürfen nie auseinanderlaufen.
- Compute-Capability-Sortierung ist die einzige authoritative Quelle für
  Speed-Klassen — keine Hardcoded-Listen ("RTX 8000 ist schnell").

## Referenzen im Code

- Algorithmus: [`aifred/lib/calibration/flow.py`](../../../aifred/lib/calibration/flow.py)
  - `calibrate_llamacpp_model()` — Entry Point (Phase A, 1, E, D)
  - `_find_speed_candidate()` — Speed-Kandidat (weniger GPUs, Klassen-Kaskade)
  - `calibrate_tts_variant_from_base()` — TTS-/VLM-/Combo-Variante aus der Base
- Verify + ctx-Suche: [`aifred/lib/calibration/ctx_search.py`](../../../aifred/lib/calibration/ctx_search.py)
  - `_verify_and_refine()` — Verify + Shift + ctx-Suche + Upward-Push
  - `_binary_search_fitting_ctx()` — Math-geführte ctx-Suche auf festem Split
- Split-Verfeinerung: [`aifred/lib/calibration/split_refine.py`](../../../aifred/lib/calibration/split_refine.py)
  - `_shift_one_layer_blind()` — Blind-Shift (Load-OOM)
  - `_refine_split_from_measurement()` — messgestütztes Refinement
  - `_context_refine_swap()` — ctx-maximierender Swap (erster OOM, Upward-Push)
  - `_cascade_destination()` — SSOT Ziel-Wahl (Kaskade, reserve-adjusted, idle-skip, `blocked_dest`)
- Math: [`aifred/lib/calibration/fit_math.py`](../../../aifred/lib/calibration/fit_math.py)
  - `_enumerate_gpu_configs()` — GPU-Sets in Prioritätsreihenfolge
  - `_quantize_split_to_layers()` — fraktionalen Split auf ganze Layer runden
  - `_math_max_fitting_ctx()` / `_math_predicts_fit()` — Math-Vorfilter
  - `_derive_reserved_split()` — proportionaler Split für reserve-belastete Karten
- Optimizer: [`aifred/lib/calibration/optimizer.py`](../../../aifred/lib/calibration/optimizer.py)
  - `fill_fastest_first()` — greedy fill nach Speed-Klasse
  - `first_gpu_handicap_mb()` — modellbasiertes First-GPU-Handicap
- Hardware: [`aifred/lib/calibration/gpu.py`](../../../aifred/lib/calibration/gpu.py)
  - `enumerate_gpus()` — Compute-Capability-Sortierung, Speed-Klassen, `first_in_class`
  - `measure_first_gpu_handicap()` — Idle-Handicap (Floor/Ceiling)
- TTS-Pinning: [`aifred/lib/process_utils.py`](../../../aifred/lib/process_utils.py)
  - `get_tts_gpu_uuid()` — TTS-GPU-Pinning (UUID, via `pick_tts_gpu`)
- Side-Channel-Platzierung: [`aifred/lib/vision_gpu_select.py`](../../../aifred/lib/vision_gpu_select.py)
  - `_side_channel_tier()` — Tier-Bildung + Compute-Floor
  - `pick_side_channel_gpu()` — Sammelkarte (zweite Tier-Karte, schwächste Anbindung zuerst)
  - `pick_tts_gpu()` / `pick_vlm_gpu()` — liefern beide die Sammelkarte
  - `resolve_gpu_id()` — `"auto"` → `pick_vlm_gpu()` (InsightFace)
- KI-Variante: [`aifred/lib/calibration/ai_agent.py`](../../../aifred/lib/calibration/ai_agent.py)
  - `calibrate_with_ai()` — Tool-Loop (`estimate_config`, `probe_config`, `finalize`)
- Prompt: [`prompts/en/calibration/system.txt`](../../../prompts/en/calibration/system.txt)
