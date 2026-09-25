# Calibration-Challenge: LLM gegen Algorithmus

> **English version:** [calibration-llm-challenge.md](../../en/architecture/calibration-llm-challenge.md)

> Übergabe-Dokument für die experimentelle Bewertung, ob eine LLM die
> aktuelle algorithmische Calibration ablösen oder ergänzen kann.
> Ausgangspunkt der Diskussion: aktuelle Calibration mit 6-13 Varianten
> dauert ~45 Min auf dem MiniPC-Setup, größter Anteil entfällt auf
> Binary-Searches nach fehlgeschlagenen Probes.

## Hintergrund

Der bestehende algorithmische Calibration-Pfad ist in
[calibration-strategy.md](calibration-strategy.md) dokumentiert.
Kurzfassung:

- **BASE**: greedy fill auf die schnellste Compute-Klasse, Spillover auf
  die nächstlangsamere wenn nötig. Min. GPUs, max. ctx.
- **SPEED**: weniger GPUs als BASE durch ctx-Reduktion.
- **TTS-Varianten** (die installierten GPU-Engines: XTTS, MOSS,
  Fish-Speech, Qwen3-TTS): BASE/SPEED erneut, mit TTS-Reserve auf der
  gemeinsamen Side-Channel-Karte der zweithöchsten Compute-Klasse
  (`pick_tts_gpu`/`pick_vlm_gpu` → `pick_side_channel_gpu`). Fast Path:
  Re-Projection des BASE-GPU-Sets über `calibrate_tts_variant_from_base`;
  volle Re-Kalibration nur, wenn der keinen Fit findet. Profile, deren
  GPU-Set disjunkt zur TTS-Karte ist, werden ohne Probe kopiert
  (Isolated Mode).
- **VLM-Varianten** (qwen3-vl:4b, qwen3-vl:8b): analog mit VLM-Reserve.
- **Combo-Varianten** (TTS×VLM): beide Reserven auf derselben
  Side-Channel-Karte. Combo wird verworfen wenn TTS+VLM die Karte
  sprengen (Capacity-Check).

Aktueller LLM-Pfad (Stand 2026-09-25): `calibration_mode = "ai"` (Default
`"legacy"`) ruft `calibrate_with_ai()` in
[ai_agent.py](../../../aifred/lib/calibration/ai_agent.py) auf — für
**BASE, SPEED und die TTS-/VLM-/Combo-Zellen**:

- BASE: `_try_ai_calibration()` in
  [flow.py](../../../aifred/lib/calibration/flow.py).
- SPEED: `_find_speed_candidate()` wählt das kleinere GPU-Set
  deterministisch, die KI optimiert ctx/Split auf genau diesem gelockten
  Set (`_ai_variant_from_base(as_speed=True)`).
- TTS/VLM/Combo: `calibrate_tts_variant_from_base` verzweigt im KI-Modus
  auf `_ai_variant_from_base()` statt auf die algorithmische Projektion.
  Die Isolated-Mode-Kopie im Calibration-Mixin bleibt in beiden Modi
  probe-frei.
- Der KI-Modus ist terminal: bei Fehlschlag meldet er einen Fehler, kein
  Rückfall auf den Algorithmus.
- Provider/Modell kommen aus dem System-Agenten `calibration` in
  `data/agents.json` (Default-Provider `qwen`).

## Kern-Hypothese

**Der einzige strukturelle Vorteil einer LLM gegenüber dem aktuellen
Algorithmus liegt in der Binary-Search-Beschleunigung.**

- `llama-fit-params` rechnet nicht genau genug. Math-Projektion sagt
  "passt mit X MB free", echter Probe zeigt OOM oder zu viel Headroom.
- Der Algorithmus repariert das per Binary-Search (Math-gestützt mit
  Bias-Tracking, fällt auf reine Mittelwert-Bisection zurück wenn Math
  uninformativ wird), Präzision 256 Tokens.
- Eine LLM könnte aus dem ersten Probe-Result (predicted vs. measured
  per GPU) direkt interpolieren statt zu halbieren. Wenn das gelingt:
  3-5 Probes Ersparnis pro Variante.

Außerhalb dieses Punktes bringt eine LLM **nichts** — die Greedy-Cascade
(BASE, SPEED) ist deterministisch optimal, Re-Projection bei TTS/VLM/
Combo ist Mathematik ohne Suchraum.

## Was vorab geklärt werden muss

Bevor der Vergleich startet:

1. **Cache leeren** für das Testmodell, sonst gewinnt der Algorithmus
   per Cache-Hit. Datei: `data/model_vram_cache.json`. Eintrag fürs
   Testmodell entfernen (oder ein Modell wählen das noch nie kalibriert
   wurde).
2. **Burn-In-Caches dürfen verwendet werden** — `data/vlm_vram_cache.json`
   und `data/tts_vram_cache.json` sind Lookup-Tabellen mit empirisch
   gemessenen Reserven für VLMs und TTSen. Beide Pfade (Algorithmus und
   LLM) nutzen sie als Input, fair für den Vergleich.
3. **Mindestens 2-3 Modelle unterschiedlicher Größe** testen, damit der
   Vergleich nicht auf einem Spezialfall hängt. Vorschlag:
   - Klein (passt klar auf eine Klasse): z.B. Qwen3-4B oder 8B
   - Mittel/knapp: z.B. Qwen3.6-27B oder 30B
   - Groß/Spillover-pflichtig: z.B. Qwen3-122B oder 70B+

## Analyse vor dem Vergleich (Phase 0)

Bevor der eigentliche Race gefahren wird, eine **Auswertung der
zuletzt durchgelaufenen Calibration** (122B mit allen Varianten):

### Datenquellen

- `journalctl --user -u aifred-intelligence` seit Start der Calibration
  (kompletter Log mit allen Probe-Sequenzen).
- `data/logs/aifred_debug.log` (Live-Debug-Ausgaben des Calibration-Mixin).

### Auswerten

Pro Variante (BASE, SPEED, jede TTS, jede VLM, jede Combo):

1. **Wie viele Probes wurden gefahren** (Phase 1 initial + Layer-Shifts +
   Binary-Search) — Zähler aus den Log-Zeilen.
2. **Bias pro Probe**: `predicted_min_free − measured_min_free` (in MB)
   für jeden Probe-Versuch. Listet auf wie weit `llama-fit-params` daneben
   lag.
3. **Bias-Muster identifizieren**:
   - Systematisch konstant (z.B. immer ~100-150 MB optimistisch) →
     **Bias-Tracking im Algorithmus deckt das ab, LLM bringt keinen
     Vorteil**.
   - Chaotisch/modellabhängig (mal +200, mal −50, mal +500) → **LLM
     könnte Pattern-Matching besser**.
4. **Binary-Search-Kosten**: pro Variante zählen wie viele Probes nach
   dem ersten OOM noch nötig waren bis ctx-Konvergenz. 1-2 ist nicht
   schlagbar. 5+ ist der Hebel den die LLM angreifen müsste.

### Entscheidungs-Punkt

- Wenn die Analyse zeigt, dass der Algorithmus pro Variante bereits mit
  ≤3 Probes konvergiert: **LLM-Pfad rauswerfen**, Diskussion beendet.
- Wenn einzelne Varianten 5+ Probes brauchen mit erkennbarem
  Bias-Pattern: **LLM-Race fahren** (Phase 1 unten).

## Vergleichs-Race (Phase 1)

### Setup

- Modell aus den Vorab-Kandidaten gewählt, Cache geleert.
- Wallclock-Stoppuhr von "Start" bis "alle Profile in
  `~/.config/llama-swap/config.yaml` geschrieben".
- Gleiche Variant-Liste für beide Pfade: BASE + SPEED + dieselben im
  Calibration-Picker angehakten TTS-, VLM- und Combo-Zellen
  (`calibration_matrix`).

### Was die LLM (Claude) darf

- Lese-Zugriff auf `nvidia-smi`, `llama-fit-params`, GGUF-Header,
  llama-swap Config, Burn-In-Caches.
- Echter `llama-server`-Start zur Verifikation (Probe). Ohne Probe ist
  das Resultat nur eine Schätzung, kein Vergleich.
- **Nicht erlaubt**: an `~/.config/llama-swap/config.yaml` schreiben —
  das übernimmt der existierende Code, sonst vermischt sich der
  Vergleich mit Schreib-Latenz.

### Was der Algorithmus tut

Standardlauf via UI bzw. CLI, identisches Modell, gleicher Zeitpunkt
(kein paralleler GPU-Load) — `systemctl restart aifred-intelligence`
zwischen den Läufen für sauberen GPU-State.

### Erfolgskriterien

- **ctx-Werte annähernd identisch** (innerhalb ~5% Toleranz). Wenn die
  LLM signifikant schlechtere ctx-Werte liefert, ist sie nicht
  einsetzbar, egal wie schnell sie ist.
- **Wallclock-Zeit** klar kürzer (≥20% Ersparnis) damit sich der Pfad
  lohnt. Marginale Ersparnis rechtfertigt nicht die API-Kosten + den
  Wartungsaufwand für die zwei Pfade.
- **Determinismus prüfen**: LLM-Race 2x fahren, schauen ob die Ergebnisse
  konsistent sind. Wenn die LLM mal 80k ctx, mal 60k ctx vorschlägt, ist
  sie nicht nutzbar.

## Konsequenzen je Ausgang

| Ergebnis | Konsequenz |
|---|---|
| LLM gewinnt klar (≥20% schneller, gleiche ctx-Werte, deterministisch) | AI-Pfad als optionale Variante behalten, Prompts pro Step ausgliedern (eigene Prompts für BASE + SPEED, Re-Projection bleibt algorithmisch), evtl. später Default |
| LLM kompetitiv aber nicht klar besser | AI-Pfad als Notnagel behalten für Edge-Cases (ungewöhnliche Hardware, fit-params versagt komplett). Keine Investition in Refactor |
| LLM schlechter oder nicht-deterministisch | AI-Pfad rauswerfen, [ai_agent.py](../../../aifred/lib/calibration/ai_agent.py) + die KI-Zweige in [flow.py](../../../aifred/lib/calibration/flow.py) (`_try_ai_calibration`, `_ai_variant_from_base`) + `prompts/{de,en}/calibration/system.txt` + den Agenten `calibration` in `data/agents.json` + `calibration_mode`-Switch löschen |

## Wichtige Verbindlichkeiten für die LLM-Seite

Damit der Vergleich fair ist, muss die LLM **exakt denselben Algorithmus
befolgen** wie in [calibration-strategy.md](calibration-strategy.md)
beschrieben. Nicht "schlauer werden" wo der Algorithmus konservativ ist:

- **Sortierung**: compute_cap DESC → total_mb DESC → Name → UUID
  (`enumerate_gpus()` in `aifred/lib/calibration/gpu.py`).
- **Reihenfolge bei OOM**: zuerst max. 15 Layer-Shifts bei native ctx,
  **erst danach** ctx-shrink. Nicht abkürzen.
- **First-GPU-Handicap** berücksichtigen (Idle-Messung mit 256 MB Floor;
  ein Idle-Delta über 500 MB fällt auf den Floor zurück; sobald
  fit-params gelaufen ist, modellbasiert über `first_gpu_handicap_mb()`) —
  GPU0 hat durch Display + llama.cpp-Main-Device-Puffer (Output-Tensor,
  Compute-Workspace, MTP-Draft) weniger nutzbares VRAM.
- **Native ctx ist hard cap** — niemals höher probieren.
- **Hybrid-Modus** (CPU-Offload) nur wenn explizit erlaubt.

Der **erlaubte Vorteil**: nach jedem Probe (predicted vs. measured per
GPU) direkt interpolieren statt zu halbieren. Das ist die ganze Idee.

## Architektur-Folgefrage (nur bei Sieg der LLM)

Wenn der AI-Pfad ausgebaut wird, neues Prompt-Layout:

- Pro Search-Step ein eigenes Prompt-File: `prompts/{lang}/calibration/base.txt`
  und `prompts/{lang}/calibration/speed.txt` (Tribunal entfällt — ist
  Agenten-Choreografie, kein Calibration-Step).
- Inter-Step-Kontext: nach BASE-Result wird `(ctx, split, measured_free_mb)`
  in den SPEED-Prompt durchgereicht.
- Re-Projection (TTS/VLM/Combo) bleibt rein algorithmisch — kein
  LLM-Reasoning dort (heute übergibt der KI-Modus auch diese Zellen an die
  KI, siehe [Hintergrund](#hintergrund)).
- `agents.json`-Schema: ggf. pro Step eigenes Modell + Reasoning-Toggle.

## Referenzen

- [calibration-strategy.md](calibration-strategy.md) — algorithmische
  Strategie (SSOT)
- [aifred/lib/calibration/flow.py](../../../aifred/lib/calibration/flow.py) —
  `calibrate_llamacpp_model`
- [aifred/lib/calibration/ai_agent.py](../../../aifred/lib/calibration/ai_agent.py) —
  aktueller LLM-Pfad (BASE, SPEED, TTS-/VLM-/Combo-Zellen)
- [aifred/lib/calibration/optimizer.py](../../../aifred/lib/calibration/optimizer.py) —
  `fill_fastest_first`
- [prompts/en/calibration/system.txt](../../../prompts/en/calibration/system.txt) —
  aktueller LLM-System-Prompt (`ai_agent.py` lädt ihn immer mit `lang="en"`)
- [data/vlm_vram_cache.json](../../../data/vlm_vram_cache.json),
  [data/tts_vram_cache.json](../../../data/tts_vram_cache.json) — Burn-In-Lookups
- [data/model_vram_cache.json](../../../data/model_vram_cache.json) — Calibration-Cache
  (vor Vergleich für Testmodell leeren)
