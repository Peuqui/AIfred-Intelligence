# Infrastruktur-Entschlackung — Plan & Evaluation (Stand 2026-08-15)

Arbeitsdokument zur Konsolidierung des Inferenz-Stacks. Kontext: Kobold ist
bereits entfernt; Qwen3.8-27B ist als neues Arbeitsmodell deployed (llama.cpp
via llama-swap, MTP + Vision + Effort-Stufen). Nächster Kandidat: Ollama.

---

## 1. Ollama ablösen

### Ist-Zustand (verifiziert 2026-08-15)

Ollama läuft als zwei System-Services (`ollama.service` :11434,
`ollama-vlm.service` :11436 V100-gepinnt) und hat noch genau **zwei** Nutzer:

| Nutzer | Code-Stelle | Modelle |
|---|---|---|
| Vision-Side-Channel (Vigilantia + `-vlm-`Varianten) | `lib/vision_analyzer.py` → `VISION_VLM_HOST_PINNED` (:11436) | qwen3-vl:4b, qwen3-vl:8b |
| Embeddings (RAG-Index **und** Research-Cache) | `lib/vector_cache.py` + `lib/document_store.py` → `OllamaEmbeddingFunction` | bge-m3 (+ nomic-embed-text-v2-moe) |

Das Chat-Backend `backends/ollama.py` wird am Mini nicht genutzt (kein
Tool-Support — Single-Shot only). Entscheidung offen, ob es für Fremd-User
als niedrigschwelliger Einstieg im Produkt bleibt.

### Zielbild

Alles über llama-swap. Kernvoraussetzung: eine **persistente, nicht-exklusive
Gruppe** neben `main`, damit Side-Channel- und Embedding-Modelle Modell-Swaps
des Haupt-LLM überleben (24/7-Anforderung von Vigilantia). llama-swap bringt
das Gruppen-Feature mit; die VRAM-Seite ist durch das Reserve-Konzept der
Kalibrierung bereits vorbereitet (z. B. „Vigilantia 4B reserve: 7722 MB on
GPU3").

### Teilpakete

1. **GGUFs + Gruppe:** Offizielle HF-GGUFs für qwen3-vl 4B/8B (+ mmproj)
   laden — die Ollama-Blobs sind unbrauchbar (Autoscan: „missing GGUF
   metadata key qwen3vl.rope.dimension_sections"). llama.cpp-Seite kann die
   Architektur nachweislich (Qwen3.8-mmproj nutzt denselben
   `qwen3vl_merger`-Projektor). Autoscan um Gruppen-Zuordnung erweitern
   (persistent-Gruppe für Side-Channel + Embeddings statt `main`).
2. **`vision_analyzer.py` umstellen:** Ollama-API → OpenAI-kompatible
   `/v1/chat/completions` mit `image_url`. Vorlage existiert im eigenen
   Haus: die Kalibrier-Vision-Probe (`lib/calibration/verifier.py`) macht
   exakt solche Requests.
3. **Embedding-Funktion tauschen:** `OllamaEmbeddingFunction` →
   `/v1/embeddings` (llama-server `--embedding`). bge-m3-GGUF (f16)
   verfügbar. **Risiko:** Vektor-Kompatibilität — bleibt es dasselbe
   bge-m3 in f16, bleiben die bestehenden ChromaDB-Vektoren gültig;
   andere Quantisierung ⇒ Re-Indexierung (machbar, ~1 Abend Rechenzeit).
   Vorher mit Stichproben-Cosine-Vergleich verifizieren.
4. **Rückbau:** beide Ollama-Services stilllegen, Ollama-Sonderfälle
   entfernen (greedy GPU-Wahl-Doku, Zwei-Service-Konstrukt,
   Blob-Symlink-Logik im Autoscan). `backends/ollama.py`: Entscheidung
   User (behalten für Fremd-User vs. Kobold-Präzedenz).

### Gewinne

- Ein Inferenz-Stack (SSOT) — eine Kalibrierung, ein Monitoring, ein Update-Pfad
- Side-Channel-Modelle upgradebar: Qwen3.5/3.6-Vision war **nur in Ollama**
  blockiert (Upstream-Bugs #16282/#14575), llama.cpp-Seite (`--mmproj`)
  funktioniert → modernere VL-Modelle möglich
- Ollama-Eigenheiten entfallen (keep_alive-Semantik, eigenes VRAM-Management)

### Aufwand

Kein Nachmittag, kein Großprojekt: **2–3 konzentrierte Sessions** inkl. Tests.
Reihenfolge: erst Paket 1+2 (Vision), dann 3 (Embeddings, mit
Kompatibilitäts-Check), dann 4.

---

## 2. vLLM-Evaluation — AUF EIS GELEGT (2026-08-15)

**Entscheidung:** AIfred ist Single-User (`-np 1`, kein paralleler Betrieb
vorgesehen). Der einzige plausible vLLM-Gewinn auf dieser Hardware wäre
paralleler Durchsatz (continuous batching) — der Single-Stream-Fall ist
laut Analyse unten eher ein Rückschritt (Turing ohne schnelle Quant-Kernels,
AWQ/GPTQ nötig statt Q8). Ohne Parallel-Use-Case kein Grund für den Aufwand
(frisches venv, Benchmark-Runde). Test-Plan bleibt unten dokumentiert,
falls der Betriebsmodus sich mal ändert (z. B. Message-Hub-Parallellast).

### Status (Bestandsaufnahme, vor der Eis-Entscheidung)

- Rückkehr-Trigger aus der Planung („sobald P40s weg") ist formal erfüllt.
- Installation im AIfred-venv ist eine **Leiche**: `venv/bin/vllm` existiert,
  aber Torch fehlt komplett — nicht lauffähig. Echte Versuche brauchen ein
  frisches, separates venv (`uv venv` + `vllm` + Torch, ~10–15 GB), gemäß
  Paket-Regel nur nach expliziter Freigabe.

### Hardware-Realität (dämpft die Erwartung)

| Karte | Compute | vLLM-Tauglichkeit 2026 |
|---|---|---|
| 3× V100 (Volta, sm_70) | 7.0 | **Praktisch raus** — moderne Torch-/vLLM-Stacks haben Volta gestrichen (vgl. Diarisierungs-Thema: cu130 ohne Volta) |
| 2× RTX 8000 (Turing, sm_75) | 7.5 | Legacy-Tier: Marlin-Kernels (AWQ/GPTQ schnell) brauchen sm_80+, FP8 Hopper+, NVFP4 Blackwell — auf Turing bleiben langsame Fallback-Kernels |

### Einschätzung zur These „Gewinn bei Ein-Karten-Modellen"

Der vLLM-Kernvorteil ist **Parallelität** (continuous batching, PagedAttention)
— nicht Single-Stream-Tempo. AIfred fährt heute `-np 1`, Single-User; und
llama.cpp liefert mit MTP-Draft auf der RTX 8000 bereits ~26–35 tok/s bei
Q8-Qualität. Auf Turing ohne schnelle Quant-Kernels ist bei Einzelanfragen
eher **kein** Gewinn zu erwarten, möglicherweise ein Rückschritt (AWQ-4bit
nötig statt Q8 → Qualitätsverlust, dazu Fallback-Kernel-Tempo).

Realistischer Gewinn-Fall: **parallele Last** — Message-Hub-Worker,
Symposion-Agenten gleichzeitig, mehrere Browser-Sessions. Wenn dieser
Betriebsmodus kommen soll, lohnt der Benchmark.

### Testplan (wenn freigegeben)

1. Separates venv, vLLM aktuell + Torch-Backend passend zu sm_75
2. Modell: Qwen3.8-27B **AWQ/GPTQ-4bit** (FP8/NVFP4 scheiden aus) auf 1× RTX 8000
3. Benchmark A: Single-Stream tok/s + TTFT vs. llama.cpp Q8-Speed-Variante (Referenz: PP 127–246 tok/s, Gen 26–35 tok/s)
4. Benchmark B: 4× und 8× parallele Anfragen, Gesamt- und Per-User-Durchsatz
5. Qualitäts-Stichprobe AWQ vs. Q8 (Übersetzungs-/Coding-Aufgabe)
6. MTP-Speculative in vLLM testen (`--speculative-config mtp`) — falls auf Turing lauffähig

Abbruchkriterium: Ist Single-Stream deutlich langsamer und Parallel-Betrieb
kein reales Einsatzszenario, bleibt llama.cpp gesetzt und `docs/vllm/` wird
als Historical Notes markiert.

---

## 3. Erledigt / Verworfen

- **Kobold**: entfernt (Präzedenz für Backend-Rückbau)
- **Qwen3.6-27B**: gelöscht, ersetzt durch Qwen3.8-27B (2026-08-14)
- **preserve_thinking turn-übergreifend**: bewusst verworfen (Kontextkosten,
  Anker-Effekt; frisches Nachprüfen hat sich als Qualitätsvorteil erwiesen)
- **preserve_thinking turn-intern**: wird umgesetzt (base.py/llamacpp.py,
  Backend-Gate; Server-Seite + Template-Rendering am 2026-08-15 per
  /apply-template verifiziert)
