# Benchmark-Modellübersicht

> **English version:** [models-v2.md](../../en/benchmarks/models-v2.md)

> **Historischer Benchmark.** Gemessen Anfang 2026 auf dem früheren Aufbau mit
> Tesla P40 (Pascal). Der heutige AIfred-Rechner läuft mit 2× Quadro RTX 8000 +
> 3× Tesla V100; die Zahlen beschreiben also nicht die aktuelle Leistung, bleiben
> aber eine brauchbare Referenz für alle, die P40 oder ähnliche Pascal-Karten fahren.

## Hardware-Setup

- **System**: AOOSTAR GEM 10 MiniPC (AMD Ryzen 9 7945HX, 32 GB RAM)
- **GPUs**:
  - 2× Tesla P40 (je 24 GB) über M.2-OCuLink
  - 1× Quadro RTX 8000 (48 GB) über OCuLink (früher USB4)
  - 1× Tesla P40 (24 GB) über USB4
  - Gesamt: **~115 GB VRAM** (4 GPUs)
- **Backend**: llama.cpp über llama-swap, Direct-IO, flash-attn
- **Embedding**: nomic-embed-text-v2-moe über Ollama (CPU-Modus, kein VRAM-Verbrauch)
- **OS**: Ubuntu, Kernel 6.17.0-19-generic

## Getestete Modelle

| Modell | Gesamt | Aktiv | Typ | Quant | Quelle | Experten | GGUF-Größe | Kontext (kalibriert) | KV-Cache | Kontext (nativ) |
|-------|-------|--------|------|-------|--------|---------|-----------|---------------------|----------|------------------|
| Qwen3-4B-Instruct | 4B | 4B | Dense | Q8_0 | Ollama | — | 4 GB | 262K | f16 | 262K |
| Qwen3-14B | 14B | 14B | Dense (Base) | Q4_K | Ollama | — | 9 GB | 41K | f16 | 41K |
| Qwen3-30B-A3B-Instruct | 30B | 3B | MoE | Q8_0 | Ollama | 128/8 | 30 GB | 262K | f16 | 262K |
| Qwen3-Next-80B-A3B-Instruct | 80B | 3B | MoE | UD-Q8_K_XL | Unsloth | 128/8 | 87 GB | Kal. nötig | f16 | 262K |
| GPT-OSS-120B-A5B | 120B | 5,1B | MoE | UD-Q8_K_XL | Unsloth | 128/4 | 60 GB | 131K | f16 | 131K |
| Qwen3.5-122B-A10B | 122B | 10B | MoE | UD-Q5_K_XL | Unsloth | 256/8 | 86 GB | 262K | f16 | 262K |
| MiniMax-M2.5 | 228B | 10,2B | MoE | IQ3_M | imatrix | 256/8 | 93 GB | 101K | f16 | 197K |
| Qwen3-235B-A22B-Instruct | 235B | 22B | MoE | UD-Q3_K_XL | Unsloth | 128/8 | 97 GB | 112K | f16 | 262K |
| Nemotron-3-Super-120B-A12B | 120B | 12B | NAS-MoE | UD-Q5_K_XL | Unsloth | 64/8 | 101 GB | 874K | f16 | 1.049K |

Alle Modelle laufen **nur auf der GPU** (kein CPU-Offload).
MiniMax, Qwen3-235B und Nemotron haben verkleinerte Kontextfenster, weil die großen GGUF-Dateien weniger VRAM für den KV-Cache übrig lassen.
Nemotron schafft 874K Kontext (83 % des nativen 1M), weil es nur 12B aktive Parameter hat = kleiner KV-Cache.

---

## Benchmark 1: RAG-Dokumentensuche

**Aufgabe**: „Liste alle Dokumente, die mit Transfusions- und Labormedizin zu tun haben, auf. Fasse sie ausfuehrlich zusammen."
**RAG-Kontext**: ~29K Token (44–46 Chunks aus 6 Dokumenten automatisch eingespeist)
**Verfügbare Tools**: list_documents, search_documents, web_search, store_memory usw.
**Erwartet**: 8–10 transfusionsbezogene Dokumente von insgesamt 24 in der Datenbank (472 Chunks)

### Ergebnisse

| Modell | TTFT | PP tok/s | TG tok/s | Inference | Wörter | Gefundene Dokumente | Tool-Nutzung | Qualität |
|-------|------|----------|----------|-----------|-------|------------|----------|---------|
| **GPT-OSS-120B Q8** | **40 s** | **623** | **38,6** | **157 s** | 1.200 | 8–9 | list_documents + search_documents | 9/10 |
| **Qwen3-Next-80B Q8** | 81 s | 418 | **30,9** | **180 s** | **1.398** | ~8 | noch zu prüfen | **9,5/10** |
| Qwen3.5-122B Q5_K | 117 s | 260 | 18,9 | 240 s | 1.160 | ~8 | noch zu prüfen | 8/10 |
| Nemotron Q5_K_XL | 141 s | 193 | 16,6 | 541 s | 935 | 8–10 | list_documents + search_documents | 9,5/10 |
| Qwen3-4B Q8 | 78 s | 645 | 29,4 | 120 s | 527 | 5 | keine | 6/10 |
| Qwen3-30B Instruct | 522 s | 93 | 19,9 | 571 s | 424 | 5–6 | keine | 5/10 |
| MiniMax-M2.5 IQ3_M | — | — | — | OOM | — | — | — | — |
| Qwen3-235B-A22B Q3_K | — | — | — | abgebrochen (zu langsam) | — | — | — | — |

### Qualitätsbewertung

**GPT-OSS-120B** (9/10):
- Insgesamt am schnellsten — 38,6 tok/s, fertig nach 157 s
- Nutzt aktiv Tools (list_documents + search_documents), um alle Dokumente zu finden
- Strukturierte Ausgabe mit Markdown-Tabellen, Chunk-Zahlen, Statistiken
- Guter Butler-Stil mit trockenem Humor
- Über mehrere Läufe konsistent
- Fehlt: Autorennamen, einige Randfall-Dokumente

**Qwen3-Next-80B-A3B-Instruct** (9,5/10):
- Bester Allrounder — 30,9 tok/s, fertig nach 180 s (nur 23 s langsamer als GPT-OSS)
- Reichhaltigste Ausgabe: 1.398 Wörter mit außergewöhnlich vielen Details
- Herausragender Butler-Stil — „Die Bibel der transfusionsmedizinischen Sorgfalt", „Silberbesteckkiste inventarisiert"
- Durchgehend trockener Humor, literarische Metaphern, lebendige Beschreibungen
- Autoren genannt (Krakowitzky et al.), alle 11 Abschnitte ausführlich
- Nur 3B aktive Parameter, spielt aber weit über seiner Gewichtsklasse
- **Empfohlen als Standardmodell für die Balance aus Qualität und Tempo**

**Nemotron-3-Super-120B** (9,5/10):
- Größte Detailtiefe — Autoren (Krakowitzky et al.), alle 11 Abschnitte einzeln aufgeführt
- Nutzt aktiv Tools, findet die meisten Dokumente
- 1.787 Wörter im besten Lauf — am umfassendsten
- ABER: 541–754 s Inference-Zeit (4–5× langsamer als GPT-OSS)
- Für den Alltag unpraktisch, hervorragend für tiefe Analysen

**Qwen3.5-122B** (8/10):
- Guter Kompromiss — 240 s Inference, 1.160 Wörter
- Nennt Autoren, „splendid"-Butler-Stil
- 18,9 tok/s TG — mittleres Tempo
- Tool-Nutzung noch zu prüfen

**Qwen3-4B** (6/10):
- Für 4B überraschend brauchbar — findet 5 Dokumente, ordentliche Zusammenfassungen
- Keine Tool-Nutzung — verlässt sich nur auf die automatisch eingespeisten RAG-Chunks
- Beste PP-Geschwindigkeit (645 tok/s), aber durch die Modellintelligenz begrenzt
- Gut für schnelle Überblicke, nicht für gründliche Analysen

**Qwen3-30B Instruct** (5/10):
- Enttäuschend — 521 s TTFT, keine Tool-Nutzung, nur 424 Wörter
- Dünne Zusammenfassungen, keine Struktur
- Die 93 tok/s PP bei ~30K Prompt sind der Flaschenhals
- Für RAG-Aufgaben nicht empfohlen

**MiniMax-M2.5** (nicht testbar):
- OOM-Absturz (Segfault), wenn das Ollama-Embedding-Modell ~900 MB VRAM belegt
- Behoben durch Umstellung des Embeddings auf CPU-Modus (EMBEDDING_USE_GPU=False)
- Muss mit aktivem CPU-Embedding neu kalibriert werden

**Qwen3-235B-A22B** (nicht testbar):
- Wegen extremer Inference-Zeit abgebrochen
- 22B aktive Parameter + Q3-Quantisierung = langsam auf P40-Hardware

### Wichtigste Erkenntnisse

1. **Qwen3-Next-80B ist der beste Allrounder** — fast so schnell wie GPT-OSS (180 s gegen 157 s) bei Nemotron-Qualität (9,5/10)
2. **GPT-OSS-120B ist der Tempo-Champion** — am schnellsten mit 38,6 tok/s, gute Qualität, zuverlässige Tool-Nutzung
3. **Modellgröße != Qualität**: GPT-OSS (5,1B aktiv) schlägt Qwen3-30B (3B aktiv) und Qwen3-4B (4B)
4. **Aktive Parameter != Tempo**: Qwen3-Next-80B (3B aktiv, 87 GB) ist schneller als Qwen3.5-122B (10B aktiv, 86 GB)
5. **Tool-Nutzung ist entscheidend**: Modelle, die list_documents aufrufen, finden 8–10 Dokumente, die ohne nur 5
6. **Qwen3-Instruct-Modelle nutzen bei aktiviertem Thinking keine Tools** — bekannter Bug, Thinking für Instruct + Tools deaktiviert
7. **Embedding auf der GPU verursacht OOM** bei großen Modellen — CPU-Embedding (143 ms gegen 89 ms) ist der sichere Standard
8. **PP-Tempo zählt bei RAG mehr als TG** — große Prompts (30K Token) dominieren die Gesamtzeit

---

## Benchmark 2: Tribunal Hund gegen Katze

**Aufgabe**: „Was ist besser, Hund oder Katze?" / "What is better, dog or cat?"
**Modus**: Tribunal (AIfred -> Sokrates R1 -> AIfred R2 -> Sokrates R2 -> Salomo-Urteil)
**Sprachen**: Deutsch + Englisch

Siehe [analysis-v2.md](analysis-v2.md) für die ausführliche Tribunal-Analyse über die Modelle hinweg.

### Leistungsübersicht (aus den Tribunal-Sessions)

| Modell | TG tok/s | Qualität | Butler-Stil | Debattentiefe | Urteil |
|-------|----------|---------|-------------|--------------|---------|
| Qwen3-Next-80B-A3B (Thinking) | ~31 | 9,5/10 | Hervorragend | Tief philosophisch | Poetisch |
| Qwen3-235B-A22B Q3_K | ~14 | 9,5/10 | Hervorragend | Sehr tief | Nuanciert |
| Qwen3.5-122B-A10B | ~19 | 8,5/10 | Gut | Analytisch | Strukturiert |
| GPT-OSS-120B-A5B | ~50 | 6/10 | Funktional | Ausreichend | Steril |
| MiniMax-M2.5 IQ3_M | ~22 | 8/10 | Gut | Gut | Natürlich |

### Der entscheidende Unterschied: RAG gegen Tribunal

- **Für RAG**: GPT-OSS gewinnt beim Tempo, Qwen3-Next-80B bei der Balance aus Qualität und Tempo
- **Für kreative Debatten**: Qwen3-Next-80B/235B gewinnen (Qualität + Persona-Tiefe dominieren)
- **Bester Allrounder**: **Qwen3-Next-80B-A3B** — glänzt bei RAG und Tribunal, 30 tok/s, herausragende Persona-Konsistenz
