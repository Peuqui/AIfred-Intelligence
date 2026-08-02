# Empfohlene Parameter pro Modell (llama-server)

Stand: 2026-02-21 — Offizielle Unsloth-Docs + Eigene Tests

---

## GPT-OSS-120B

| Parameter | Wert | Wichtig |
|-----------|------|---------|
| --jinja | ja, Pflicht | Harmony-Template |
| --reasoning-format | none | NICHT deepseek! |
| --chat-template-kwargs | '{"reasoning_effort": "medium"}' | low/medium/high möglich |
| --temp | 1.0 | |
| --top-p | 1.0 | |
| --top-k | 0 (offiziell) oder 100 (Speed-Trick) | |
| --min-p | 0.0 | |
| --repeat-penalty | NICHT setzen! | Explizit verboten |
| -ctk / -ctv | NICHT setzen! | KV-Quant killt Performance (84% langsamer PP, 58% langsamer TG) |
| -fa | on | |
| -b / -ub | 2048 / 2048 | |

## GLM-4.7-REAP-218B

| Parameter | Wert | Wichtig |
|-----------|------|---------|
| --jinja | ja, Pflicht | |
| --reasoning-format | deepseek | |
| --chat-template-kwargs | '{"enable_thinking": false}' | Zum Abschalten |
| --temp | 0.7 (Coding) / 1.0 (allgemein) | |
| --top-p | 1.0 (Coding) / 0.95 (allgemein) | |
| --min-p | 0.01 | llama.cpp Default 0.1 ist zu hoch |
| --repeat-penalty | 1.0 (= disabled, KRITISCH!) | Jeder andere Wert zerstört Output |
| -ctk / -ctv | q8_0 / q8_0 | OK, spart VRAM |
| -fa | on | |
| -b / -ub | 2048 / 512 | |

## MiniMax-M2.5

| Parameter | Wert | Wichtig |
|-----------|------|---------|
| --jinja | ja, Pflicht | Sonst Endlos-Loops |
| --reasoning-format | none | NICHT deepseek! MiniMax-Format ist anders |
| --temp | 1.0 | |
| --top-p | 0.95 | |
| --top-k | 40 | |
| --min-p | 0.01 | |
| --repeat-penalty | 1.0 | |
| -ctk / -ctv | q4_0 / q4_0 | OK bei Q2_K_XL Modell |
| -fa | on | |
| -b / -ub | 4096 / 4096 | MoE profitiert von großen Batches |

## Qwen3 — Instruct-Varianten (4B, 14B, 30B-A3B, 235B-A22B)

| Parameter | Wert | Wichtig |
|-----------|------|---------|
| --jinja | ja | |
| --reasoning-format | nicht nötig | Instruct = kein Thinking |
| --temp | 0.7 | |
| --top-p | 0.8 | |
| --top-k | 20 | |
| --min-p | 0 | |
| --presence-penalty | 1.5 (bei Wiederholungen) | |
| -ctk / -ctv | q8_0 / q8_0 | OK |
| -fa | on | |
| --no-context-shift | ja | Pflicht bei KV-Quant |

## Qwen3-Next-80B (Thinking + Instruct)

| Parameter | Wert | Wichtig |
|-----------|------|---------|
| --jinja | ja | |
| --reasoning-format | deepseek (Thinking) / nicht nötig (Instruct) | |
| --temp | 0.6 (Thinking) / 0.7 (Instruct) | |
| --top-p | 0.95 (Thinking) / 0.8 (Instruct) | |
| --top-k | 20 | |
| -ub | max 512! | Höher = Crash |
| -ctk / -ctv | q4_1 / q4_1 | |
| KV-Cache-Reuse | kaputt | Hybrid-Architektur |

---

## Wichtigste Erkenntnisse (Original)

- **GPT-OSS**: Kein KV-Cache-Quant! (-ctk/-ctv killt Performance massiv)
- **GPT-OSS**: --reasoning-format none, nicht deepseek (Harmony-Format ist inkompatibel)
- **GLM-REAP**: --repeat-penalty muss 1.0 sein (alles andere = Endlos-Wiederholungen)
- **MiniMax**: --reasoning-format none (kein deepseek)
- **Qwen3-Next**: -ub max 512 (höher crasht wegen Hybrid-Architektur)
- **--jinja ist bei ALLEN Modellen Pflicht**
- **--no-context-shift** ist Pflicht bei Qwen3 + KV-Quant

---

# 📌 Unsere Hardware-spezifische Anpassungen (Dual-GPU: RTX 8000 48GB + RTX P40 24GB)

**Stand:** 2026-02-21 — Stress-Tests mit 200 Tokens bestanden

## 🚀 Direct-IO Performance

| Parameter | Wert | Effekt |
|-----------|------|--------|
| --direct-io | **ja, bei ALLEN Modellen** | **~45x schnelleres Laden!** (60-90s → 2s) |

**Vorteile:**
- Umgeht CPU-RAM Page-Cache
- Füllt VRAM direkt (kein Umweg)
- Weniger CPU-RAM Verbrauch

**Funktioniert mit:** ext4, xfs, btrfs Dateisystemen

---

## 200B+ Modelle: KV-Quantisierung & Batch-Größen

### Qwen3-235B-A22B Instruct

| Parameter | Original | Unsere Anpassung | Grund |
|-----------|----------|------------------|-------|
| -ctk / -ctv | q8_0 | **q4_0** | q8_0 = OOM! |
| -b / -ub | Default | **1024 / 512** | Optimal für VRAM |
| --direct-io | — | **ja** | 2s Laden |
| -ngl | — | 73 | Dual-GPU |
| --tensor-split | — | 2,1 | RTX 8000 + P40 |

**Unsere Tests:**
- ✅ Stress-Test: 160 Tokens stabil
- ✅ VRAM: 43,5 GB + 21,4 GB
- ✅ CPU-RAM: ~24,9 GB (6 GB frei)
- ❌ q8_0: OOM (crasht)

### GLM-4.7-REAP-218B

| Parameter | Original | Unsere Anpassung | Grund |
|-----------|----------|------------------|-------|
| -ctk / -ctv | q8_0 | **q4_0** | q8_0 = OOM! |
| -b / -ub | 2048 / 512 | **2048 / 512** | Stabil |
| --direct-io | — | **ja** | 2s Laden |
| -ngl | — | 66 | Dual-GPU |
| --tensor-split | — | 2,1 | RTX 8000 + P40 |

**Unsere Tests:**
- ✅ Stress-Test: 130 Tokens stabil
- ✅ VRAM: 42 GB + 21 GB
- ✅ CPU-RAM: ~25 GB
- ❌ q8_0: OOM (crasht)

### MiniMax-M2.5

| Parameter | Original | Unsere Anpassung | Grund |
|-----------|----------|------------------|-------|
| -ctk / -ctv | q4_0 | **q4_0** | q8_0 = OOM! |
| -b / -ub | 4096 / 4096 | **1024 / 512** | 4096 = OOM! |
| --direct-io | — | **ja** | 2s Laden |
| -ngl | — | 48 | Dual-GPU |
| --tensor-split | — | 2,1 | RTX 8000 + P40 |

**Unsere Tests:**
- ✅ Stress-Test: 200 Tokens stabil
- ✅ VRAM: 42 GB + 20,4 GB
- ✅ CPU-RAM: ~24,6 GB
- ❌ q8_0: OOM (crasht)
- ❌ -b 4096: OOM (zu große Batches)

---

## Kleinere Modelle (<100B): KV-Cache f16

**Erkenntnis:** Bei kleineren Modellen ist **f16 schneller als q8_0**!

| Modell | -ctk / -ctv | Grund |
|--------|-------------|-------|
| Qwen3-4B | **f16** | Schneller als q8_0 |
| Qwen3-14B | **f16** | Schneller als q8_0 |
| Qwen3-30B-A3B | **f16** | Schneller als q8_0 |
| Qwen3-Next-80B | **f16** | Hybrid-Architektur (KV-Reuse kaputt) |
| GPT-OSS-120B | **f16** | Offiziell empfohlen |

---

## 📋 Finale Config für unsere Hardware

**Alle Modelle laufen stabil mit:**
- ✅ --direct-io (2s Laden)
- ✅ KV-Quant q4_0 für 200B+ Modelle
- ✅ Optimierte Batch-Größen

**Hinweis:** Bei Hardware-Upgrade (mehr VRAM) können die Original-Parameter aus der Tabelle oben getestet werden!
