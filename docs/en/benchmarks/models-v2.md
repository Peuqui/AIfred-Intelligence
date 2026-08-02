# Benchmark Model Overview

## Hardware Setup

- **System**: AOOSTAR GEM 10 MiniPC (AMD Ryzen 9 7945HX, 32GB RAM)
- **GPUs**:
  - 2x Tesla P40 (24GB each) via M.2 OCuLink
  - 1x Quadro RTX 8000 (48GB) via OCuLink (formerly USB4)
  - 1x Tesla P40 (24GB) via USB4
  - Total: **~115 GB VRAM** (4 GPUs)
- **Backend**: llama.cpp via llama-swap, Direct-IO, flash-attn
- **Embedding**: nomic-embed-text-v2-moe via Ollama (CPU mode, no VRAM usage)
- **OS**: Ubuntu, Kernel 6.17.0-19-generic

## Models Under Test

| Model | Total | Active | Type | Quant | Source | Experts | GGUF Size | Context (calibrated) | KV Cache | Context (native) |
|-------|-------|--------|------|-------|--------|---------|-----------|---------------------|----------|------------------|
| Qwen3-4B-Instruct | 4B | 4B | Dense | Q8_0 | Ollama | — | 4 GB | 262K | f16 | 262K |
| Qwen3-14B | 14B | 14B | Dense (Base) | Q4_K | Ollama | — | 9 GB | 41K | f16 | 41K |
| Qwen3-30B-A3B-Instruct | 30B | 3B | MoE | Q8_0 | Ollama | 128/8 | 30 GB | 262K | f16 | 262K |
| Qwen3-Next-80B-A3B-Instruct | 80B | 3B | MoE | UD-Q8_K_XL | Unsloth | 128/8 | 87 GB | needs cal. | f16 | 262K |
| GPT-OSS-120B-A5B | 120B | 5.1B | MoE | UD-Q8_K_XL | Unsloth | 128/4 | 60 GB | 131K | f16 | 131K |
| Qwen3.5-122B-A10B | 122B | 10B | MoE | UD-Q5_K_XL | Unsloth | 256/8 | 86 GB | 262K | f16 | 262K |
| MiniMax-M2.5 | 228B | 10.2B | MoE | IQ3_M | imatrix | 256/8 | 93 GB | 101K | f16 | 197K |
| Qwen3-235B-A22B-Instruct | 235B | 22B | MoE | UD-Q3_K_XL | Unsloth | 128/8 | 97 GB | 112K | f16 | 262K |
| Nemotron-3-Super-120B-A12B | 120B | 12B | NAS-MoE | UD-Q5_K_XL | Unsloth | 64/8 | 101 GB | 874K | f16 | 1.049K |

All models run **GPU-only** (no CPU offload).
MiniMax, Qwen3-235B and Nemotron have reduced context windows because the large GGUF files leave less VRAM for KV cache.
Nemotron achieves 874K context (83% of native 1M) thanks to only 12B active parameters = small KV cache.

---

## Benchmark 1: RAG Document Retrieval

**Task**: "Liste alle Dokumente, die mit Transfusions- und Labormedizin zu tun haben, auf. Fasse sie ausfuehrlich zusammen."
**RAG Context**: ~29K tokens (44-46 chunks from 6 documents auto-injected)
**Tools available**: list_documents, search_documents, web_search, store_memory, etc.
**Expected**: 8-10 transfusion-related documents from a total of 24 in the database (472 chunks)

### Results

| Model | TTFT | PP tok/s | TG tok/s | Inference | Words | Docs found | Tool Use | Quality |
|-------|------|----------|----------|-----------|-------|------------|----------|---------|
| **GPT-OSS-120B Q8** | **40s** | **623** | **38,6** | **157s** | 1.200 | 8-9 | list_documents + search_documents | 9/10 |
| **Qwen3-Next-80B Q8** | 81s | 418 | **30,9** | **180s** | **1.398** | ~8 | needs verification | **9.5/10** |
| Qwen3.5-122B Q5_K | 117s | 260 | 18,9 | 240s | 1.160 | ~8 | needs verification | 8/10 |
| Nemotron Q5_K_XL | 141s | 193 | 16,6 | 541s | 935 | 8-10 | list_documents + search_documents | 9.5/10 |
| Qwen3-4B Q8 | 78s | 645 | 29,4 | 120s | 527 | 5 | none | 6/10 |
| Qwen3-30B Instruct | 522s | 93 | 19,9 | 571s | 424 | 5-6 | none | 5/10 |
| MiniMax-M2.5 IQ3_M | — | — | — | OOM | — | — | — | — |
| Qwen3-235B-A22B Q3_K | — | — | — | aborted (too slow) | — | — | — | — |

### Quality Assessment

**GPT-OSS-120B** (9/10):
- Fastest overall — 38,6 tok/s, done in 157s
- Actively uses tools (list_documents + search_documents) to find all documents
- Structured output with Markdown tables, chunk counts, statistics
- Good Butler style with dry humor
- Consistent across multiple runs
- Missing: Author names, some edge-case documents

**Qwen3-Next-80B-A3B-Instruct** (9.5/10):
- Best all-rounder — 30,9 tok/s, done in 180s (only 23s slower than GPT-OSS)
- Richest output: 1.398 words with exceptional detail
- Outstanding Butler style — "Die Bibel der transfusionsmedizinischen Sorgfalt", "Silberbesteckkiste inventarisiert"
- Dry humor throughout, literary metaphors, vivid descriptions
- Authors mentioned (Krakowitzky et al.), all 11 sections detailed
- Only 3B active parameters but punches far above its weight
- **Recommended as default model for quality + speed balance**

**Nemotron-3-Super-120B** (9.5/10):
- Highest detail depth — authors (Krakowitzky et al.), all 11 sections listed individually
- Actively uses tools, finds most documents
- 1.787 words in best run — most comprehensive
- BUT: 541-754s inference time (4-5x slower than GPT-OSS)
- Impractical for everyday use, excellent for deep analysis

**Qwen3.5-122B** (8/10):
- Good compromise — 240s inference, 1.160 words
- Mentions authors, "splendid" Butler style
- 18,9 tok/s TG — moderate speed
- Needs tool use verification

**Qwen3-4B** (6/10):
- Surprisingly usable for 4B — finds 5 docs, orderly summaries
- No tool use — relies only on auto-injected RAG chunks
- Best PP speed (645 tok/s) but limited by model intelligence
- Good for quick overviews, not for thorough analysis

**Qwen3-30B Instruct** (5/10):
- Disappointing — 521s TTFT, no tool use, only 424 words
- Thin summaries, no structure
- The 93 tok/s PP on ~30K prompt is the bottleneck
- Not recommended for RAG tasks

**MiniMax-M2.5** (not testable):
- OOM crash (segfault) when Ollama embedding model occupies ~900MB VRAM
- Fixed by switching embedding to CPU mode (EMBEDDING_USE_GPU=False)
- Needs recalibration with CPU embedding active

**Qwen3-235B-A22B** (not testable):
- Aborted due to extreme inference time
- 22B active parameters + Q3 quantization = slow on P40 hardware

### Key Findings

1. **Qwen3-Next-80B is the best all-rounder** — near GPT-OSS speed (180s vs 157s) with Nemotron-level quality (9.5/10)
2. **GPT-OSS-120B is the speed champion** — fastest at 38,6 tok/s, good quality, reliable tool use
3. **Model size != quality**: GPT-OSS (5.1B active) beats Qwen3-30B (3B active) and Qwen3-4B (4B)
4. **Active parameters != speed**: Qwen3-Next-80B (3B active, 87GB) is faster than Qwen3.5-122B (10B active, 86GB)
5. **Tool Use is critical**: Models that call list_documents find 8-10 docs, those without find only 5
6. **Qwen3 Instruct models don't use tools with thinking enabled** — known bug, thinking disabled for Instruct + tools
7. **Embedding on GPU causes OOM** for large models — CPU embedding (143ms vs 89ms) is the safe default
8. **PP speed matters more than TG** for RAG — large prompts (30K tokens) dominate total time

---

## Benchmark 2: Dog vs Cat Tribunal

**Task**: "Was ist besser, Hund oder Katze?" / "What is better, dog or cat?"
**Mode**: Tribunal (AIfred -> Sokrates R1 -> AIfred R2 -> Sokrates R2 -> Salomo Verdict)
**Languages**: German + English

See [analysis-v2.md](analysis-v2.md) for detailed tribunal analysis across models.

### Performance Summary (from tribunal sessions)

| Model | TG tok/s | Quality | Butler Style | Debate Depth | Verdict |
|-------|----------|---------|-------------|--------------|---------|
| Qwen3-Next-80B-A3B (Thinking) | ~31 | 9.5/10 | Excellent | Deep philosophical | Poetic |
| Qwen3-235B-A22B Q3_K | ~14 | 9.5/10 | Excellent | Very deep | Nuanced |
| Qwen3.5-122B-A10B | ~19 | 8.5/10 | Good | Analytical | Structured |
| GPT-OSS-120B-A5B | ~50 | 6/10 | Functional | Adequate | Sterile |
| MiniMax-M2.5 IQ3_M | ~22 | 8/10 | Good | Good | Natural |

### Key Difference: RAG vs Tribunal

- **For RAG**: GPT-OSS wins on speed, Qwen3-Next-80B wins on quality/speed balance
- **For creative debate**: Qwen3-Next-80B/235B win (quality + persona depth dominate)
- **Best all-rounder**: **Qwen3-Next-80B-A3B** — excels at both RAG and Tribunal, 30 tok/s, outstanding persona consistency
