# TTS Model Comparison for AIfred Intelligence

> As of: 2026-03-28 | Sources: community reviews, GitHub, HuggingFace

## AIfred Requirements

- **Multilingual** (at least German + English)
- **Voice Cloning** (custom voices, no presets)
- **Expressive** (intonation, emotion, personality)
- **Streaming-capable** (sentence-by-sentence TTS while the LLM streams)
- **VRAM budget**: RTX 3090 Ti (24 GB), LLM runs in parallel

## Current: XTTS v2

Good intonation, speech pauses, multilingual (17+ languages incl. DE), voice
cloning. Weaknesses: hallucination on short text (< 3 words), not very
expressive, relatively slow. Existing cloned voices must be compatible with a
new model or be re-cloned.

---

## Streaming Modes Explained

There are three levels of TTS "streaming":

### 1. Chunk-based (XTTS v2, F5-TTS, Higgs-Audio)
```
LLM streams → detect sentence → whole sentence to TTS → wait for audio → play
```
- Latency = full generation time per sentence (1-3 seconds)
- **Advantage:** model sees the whole sentence → better intonation planning
  (a question is stressed differently from the start than a statement)
- **Disadvantage:** highest latency, sentence detection needed
- AIfred currently uses this mode with a carry mechanism for short sentences

### 2. Autoregressive Streaming (Qwen3-TTS, VoxCPM 1.5, Spark-TTS)
```
Sentence to model → first audio frames immediately → play WHILE rest generates
```
- Latency = first-audible ~200-300ms
- **Advantage:** low latency, sentence start audible before sentence end is done
- **Disadvantage:** model must "guess" intonation before the sentence ends

### 3. True Text Streaming (VibeVoice Realtime, MOSS-TTS-Realtime)
```
LLM streams word by word → straight to TTS → audio comes immediately
```
- Latency = lowest (~80-300ms first packet)
- **Advantage:** no sentence detection needed, direct LLM→TTS piping
- **Disadvantage:** model has the least context for intonation
- **MOSS-TTS-Realtime** uses a `push_text(delta)` API for incremental chunks
  with KV-cache reuse across multiple turns (32K context, ~40 min.)

**Conclusion:** chunk-based is not worse - just slower. Quality can even be
better because the model has the full sentence context. For AIfred the
latency is acceptable, since the LLM streams sentence by sentence anyway.

---

## Comparison Table

| Model | Parameters | Languages | DE | Voice Cloning | Expressive | Streaming | Speed (RTFX) | Sample Rate | VRAM | Architecture | License |
|-------|-----------|-----------|-----|---------------|-----------|-----------|-------------|-------------|------|--------------|---------|
| **XTTS v2** (current) | ~1.5B | 17+ | Yes | Yes (6-15s audio) | Medium | No (chunk-based) | ~0.5-1x | 24 kHz | ~2-4 GB | Autoregressive + DVAE | CPML |
| **F5-TTS** | ~335M | EN+CN (DE via fine-tune) | Yes (3 fine-tunes) | Yes (zero-shot, 10-15s) | Very high | No (flow-based) | RTF 0.15 (~7x) | 24 kHz | ~2 GB | Flow Matching DiT | CC-BY-NC 4.0 (weights) / MIT (code) |
| **Qwen3-TTS** | 1.7B | 10 (CN,EN,DE,FR,JA,KO,RU,PT,ES,IT) | Yes (native) | Yes (3s audio) | High | Yes (via fork) | ~1.8x | 24 kHz | ~4-6 GB | LLM-based | Apache 2.0 |
| **Higgs-Audio V2** | 3B | 50+ | Yes | Yes (3-10s audio) | Very high (best) | Unclear | ~1.8x | 24 kHz | ~8-12 GB | Llama-3.2-3B + DualFFN | Apache 2.0 |
| **EchoTTS** | Unclear | Unclear | Unclear | Yes (best similarity) | Low (monotone) | No | ~10x | 44.1 kHz | Unclear | Diffusion | Unclear |
| **Spark-TTS** | 0.5B | CN + EN | No | Yes (zero-shot) | High | Yes | ~50x (fastest!) | 16 kHz | ~2-4 GB | LLM single-stream | Apache 2.0 |
| **VoxCPM 1.5** | Unclear | CN + EN | No | Yes (zero-shot) | Unclear | Yes (RTF 0.17) | ~6x | 44.1 kHz | Unclear | Diffusion Autoregressive | Unclear |
| **VibeVoice TTS** | 1.5B | EN, CN, others | Unclear | Yes | High | No | Unclear | Unclear | ~6-8 GB | Next-Token Diffusion @ 7.5Hz | MIT |
| **VibeVoice Realtime** | 0.5B | EN, CN, others | Unclear | Yes | High | Yes (300ms latency) | Unclear | Unclear | ~2-4 GB | Next-Token Diffusion | MIT |
| **VibeVoice 7B** | 7B | EN, CN, others | Unclear | Yes (multi-speaker) | High | No (long-form) | Unclear | Unclear | ~19 GB | Next-Token Diffusion | MIT |
| **PocketTTS** | Small | Unclear | Unclear | Yes (safetensors) | Unclear | Yes (OpenAI API) | Unclear | Unclear | CPU possible! | Unclear | Unclear |
| **Dia (Nari Labs)** | 1.6B | EN only | No | Yes (a few sec.) | Very high | Unclear | Unclear | Unclear | ~6-8 GB | Unclear | Apache 2.0 |
| **CosyVoice-3** | Unclear | Multilingual | Unclear | Yes | Medium | Unclear | Unclear | Unclear | Unclear | Unclear | Unclear |
| **IndexTTS 2** | Unclear | Unclear | Unclear | Yes | Unclear | Unclear | Unclear | Unclear | Unclear | Unclear | Unclear |
| **MOSS-TTS Local** | 1.7B | 20+ (CN,EN,DE,FR,ES,JA,KO,...) | Yes (native) | Yes (zero-shot) | High | No (chunk-based) | Unclear | 22.05 kHz | ~11.5 GB (BF16) | Global Latent + Local Transformer (MossTTSLocal) | Apache 2.0 |
| **MOSS-TTS Delay** | 8B | 20+ | Yes (native) | Yes (zero-shot) | High | No | ~9.3 tok/s | 22.05 kHz | ~17 GB (BF16) / ~34 GB (FP32, Turing) | Delay Pattern (MossTTSDelay) | Apache 2.0 |
| **MOSS-TTS-Realtime** | 1.7B | 10+ (CN,EN,DE,FR,JA,KO,...) | Yes | Yes (zero-shot) | High | Yes (text streaming, `push_text`) | Unclear | 24 kHz | ~11.5 GB (BF16) + codec | MossTTSRealtime + MOSS-Audio-Tokenizer | Apache 2.0 |
| **MOSS-TTSD** | 8B | Multilingual | Yes | Yes (1-5 speakers) | Very high | No | Unclear | 22.05 kHz | ~16 GB (BF16) | MossTTSDelay (dialogue) | Apache 2.0 |
| **MOSS-VoiceGenerator** | 8B | CN + EN | No | No (voice from text description!) | Very high | No | Unclear | 22.05 kHz | ~16 GB (BF16) | MossTTSDelay | Apache 2.0 |
| **MOSS-SoundEffect** | ? | Multilingual | - | - (sound effects) | - | No | Unclear | 22.05 kHz | ? | MossTTSDelay | Apache 2.0 |
| **Chatterbox** | Unclear | EN | No | Yes | High | Yes (sub-200ms) | Unclear | Unclear | Unclear | Unclear | Unclear |
| **Voxtral TTS** | 4B | 9 (EN,FR,DE,ES,NL,PT,IT,HI,AR) | Yes (native) | Yes (2-3s audio) | Very high | Yes | ~10x (H200) | 24 kHz | ~16 GB | Transformer + Flow-Matching + Neural Codec | CC BY-NC 4.0 |

## Practical Test of the Integrated Engines (2026-05-22)

Head-to-head test of the four engines currently integrated into AIfred, on the
MiniPC. Conditions: all fp16, GPU V100 (HBM2), HAL9000 reference voice (mono,
after a stereo→mono level boost). Test sentence:

> "Sehr wohl, Sir, wie kann ich Ihnen heute behilflich sein? Es ist ein
> rather wunderschöner Tag am Teich im Herrengarten, indeed."

**Important for the quality assessment:** AIfred's original voice comes from an
English speaker — the character is a British butler. If an English accent
shows through when speaking German, that is **desired** and part of the
butler's charm, not a flaw. An engine that carries this English butler accent
cleanly is rated positively here; one that loses it, or mangles the
pronunciation of English words, negatively.

| Engine | Time | Quality |
|--------|------|---------|
| **XTTS v2** | 2.8 s | Fastest — but weakest: English words in the German text are pronounced poorly. |
| **Qwen3-TTS** | 8.52 s | Very pleasant accent for AIfred (original voice is English), sounds really good. A bit brighter than the original — could be darker/rougher, but isn't. Acceptably fast. |
| **Fish-Speech S2 Pro** | 11.01 s | Good quality. |
| **MOSS-TTS** | 14.8 s | Slowest — but best quality: speech idiosyncrasies very well captured. |

**Conclusion:** speed and quality are inverse. XTTS is by far the fastest, but
weakest on mixed-language text (English words within German). MOSS delivers the
best quality but is the slowest. Qwen3-TTS is the best compromise: good
quality, a fitting accent for AIfred's English original voice, and acceptably
fast at 8.5 s.

Note: pure speed benchmarks (8-sentence text, V100 vs. RTX 8000) and the
rationale behind the GPU choice are in the docstring of
`aifred/lib/process_utils.py::_detect_tts_gpu_uuid`.

## Community Ratings (Audiobook Use Case)

### Prompt Audio Similarity (how well the voice is cloned)
EchoTTS > Qwen3-TTS > Higgs-Audio > Spark-TTS

### Expressiveness (intonation, emotion, dynamics)
Higgs-Audio > Spark-TTS ~ Qwen3-TTS > EchoTTS

### Stability (missing words, artifacts)
EchoTTS > Higgs-Audio > Spark-TTS ~ Qwen3-TTS

### Voice Variation (voice variation depending on text content)
Higgs-Audio > Spark-TTS > Qwen3-TTS > EchoTTS

### Natural Sounding
Spark-TTS ~ Qwen3-TTS > Higgs-Audio > EchoTTS

### Clarity (audio quality, depends on sample rate)
EchoTTS (44 kHz) > Qwen3-TTS (24 kHz) > Higgs-Audio (24 kHz) > Spark-TTS (16 kHz)

### Cross-Lingual Voice Cloning (clone a voice in language X, speak language Y)
XTTS v2 > Qwen3-TTS > MOSS-TTS Local

**Own test:** MOSS-TTS keeps the accent of the reference language. An English
speaker sounds "foreign" in German. XTTS v2 separates voice identity from
accent better and sounds noticeably more natural cross-lingually.

## Discarded Models (Community Feedback)

| Model | Reason |
|-------|--------|
| VoxCPM 1.5 | "Overly sibilant" - BUT: another reviewer says "best streaming model" |
| VibeVoice | "Insufficient stability" |
| CosyVoice-3 | Audio not clean, clicks, noise, artifacts |
| IndexTTS 2 | Audio not clean, clicks, noise, artifacts |
| MOSS-TTS (old version) | Audio not clean, clicks and noise artifacts (rated before the MOSS-TTS Family release 2026-02-10). **Update:** the new MOSS-TTS Family (Local 1.7B, Delay 8B, Realtime 1.7B) is much better - state-of-the-art benchmarks, integrated into AIfred. |
| Chatterbox | Many artifacts |

**Note:** these ratings come from the audiobook use case (long-form). For
AIfred (short, conversational sentences) the results may differ.

## Top Candidates for AIfred

### 1. F5-TTS
- **Pro:** highest quality per community, 3 German fine-tunes available, fast
  (RTF 0.15 on L20, ~25x real-time with TensorRT), very expressive, relatively
  small (~335M, ~1.35 GB on disk), only ~2 GB VRAM, zero-shot voice cloning
  (10-15s reference), `pip install f5-tts` (simple install), P40-compatible (FP32)
- **Contra:** no native streaming (flow-based, generates the whole sentence at
  once), CC-BY-NC license for the weights (code is MIT), base model only EN+CN
  (DE needs a fine-tune), ~5% artifacts at chunk boundaries, reference audio
  leakage possible, cross-lingual voice cloning = reference accent stays (like
  MOSS-TTS), needs ref_text transcription (otherwise Whisper ASR +2 GB VRAM)
- **German fine-tunes:**
  - [aihpi/F5-TTS-German](https://huggingface.co/aihpi/F5-TTS-German) (HPI, BMBF-funded, 8x H100 training)
  - [hvoss-techfak/F5-TTS-German](https://huggingface.co/hvoss-techfak/F5-TTS-German) (officially listed in the F5-TTS repo)
  - [tabularisai/f5-tts-german-voice-clone](https://huggingface.co/tabularisai/f5-tts-german-voice-clone) (WIP, cloning-optimized)
- **Links:** [GitHub](https://github.com/SWivid/F5-TTS) | [PyPI](https://pypi.org/project/f5-tts/)
- **Docker container:** `docker/f5-tts/` (port 5052)

### 2. Qwen3-TTS
- **Pro:** German natively supported, streaming via fork, good all-rounder,
  3s voice cloning, cross-lingual cloning, Apache 2.0
- **Contra:** only ~1.8x RTFX, not the most expressive
- **Links:** [GitHub](https://github.com/QwenLM/Qwen3-TTS) |
  [Streaming Fork](https://github.com/dffdeeq/Qwen3-TTS-streaming)

### 3. Higgs-Audio V2
- **Pro:** most expressive of all models, 50+ languages, voice cloning,
  75.7% win rate over GPT-4o-mini-tts on emotions, Apache 2.0
- **Contra:** quality fluctuations under strong variation, 3B needs more VRAM,
  ~1.8x RTFX
- **Links:** [GitHub](https://github.com/boson-ai/higgs-audio) |
  [HuggingFace](https://huggingface.co/bosonai/higgs-audio-v2-generation-3B-base)

### 4. MOSS-TTS Local (1.7B)
- **Pro:** state-of-the-art benchmarks (EN SIM 73.42%, ZH SIM 78.82% - best
  open-source), 20 languages incl. German natively, zero-shot voice cloning,
  Pinyin/IPA phoneme control, up to 1 hour of audio in one run, Apache 2.0
- **Contra:** brand new (2026-02-10), needs Python 3.12 + PyTorch 2.9 +
  Transformers 5.0 (bleeding-edge dependencies), community rating of the
  predecessor version was negative (artifacts), ~11.5 GB VRAM (measured on
  RTX 3090 Ti, BF16), cross-lingual voice cloning weak (keeps the accent of the
  reference language), ~18-22s per sentence (not streaming-suited)
- **Links:** [GitHub](https://github.com/OpenMOSS/MOSS-TTS) |
  [HuggingFace Local](https://huggingface.co/OpenMOSS-Team/MOSS-TTS-Local-Transformer) |
  [HuggingFace 8B](https://huggingface.co/OpenMOSS-Team/MOSS-TTS) |
  [HuggingFace Realtime](https://huggingface.co/OpenMOSS-Team/MOSS-TTS-Realtime)
- **Docker container:** `docker/tts/moss-tts/` (port 5055)
- **Benchmarks (Seed-TTS-eval):**
  | Model | EN WER | EN SIM | ZH CER | ZH SIM |
  |-------|--------|--------|--------|--------|
  | MOSS-TTS Local 1.7B | 1.85 | 73.42 | 1.2 | 78.82 |
  | MOSS-TTS Delay 8B | 1.79 | 71.46 | 1.32 | 77.05 |
  | Qwen3-TTS 1.7B | 1.50 | 71.45 | 1.33 | 76.72 |
  | CosyVoice3 1.5B | 2.22 | 72.0 | 1.12 | 78.1 |
  | F5-TTS 0.3B | 2.00 | 67.0 | 1.53 | 76.0 |
  | VoxCPM 0.5B | 1.85 | 72.9 | 0.93 | 77.2 |

### 5. MOSS-TTS-Realtime (1.7B)
- **Pro:** true text streaming via `push_text(delta)` - LLM chunks straight to
  audio, multi-turn KV-cache reuse (voice consistency across turns), 32K context
  (~40 min.), 10+ languages incl. German, 1.7B parameters, Apache 2.0,
  trained on 2.5M+ hours single-speaker + 1M+ hours multi-speaker data
- **Contra:** separate architecture (MossTTSRealtime, not compatible with
  MossTTSLocal), needs an additional MOSS-Audio-Tokenizer codec (~24 kHz output),
  SIM score slightly lower than Local (68.9% vs 73.42% EN),
  brand new, not yet tested
- **MOSS-TTS family (complete):**
  | Model | Params | Focus | For AIfred? |
  |-------|--------|-------|-------------|
  | MossTTSLocal | 1.7B | Best benchmarks, research | Yes (currently integrated) |
  | MossTTSDelay | 8B | Production, long-form stability | Yes (RTX 8000, float32 ~34 GB) |
  | MossTTSRealtime | 1.7B | Streaming, voice agents | Yes (next candidate!) |
  | MOSS-TTSD | 8B | Multi-speaker dialogue (1-5 speakers) | No (too much VRAM) |
  | MOSS-VoiceGenerator | 8B | Voices from text description | No (too much VRAM) |
  | MOSS-SoundEffect | ? | Sound effects from text | No (not TTS) |
- **Links:** [HuggingFace](https://huggingface.co/OpenMOSS-Team/MOSS-TTS-Realtime) |
  [Audio-Tokenizer](https://huggingface.co/OpenMOSS-Team/MOSS-Audio-Tokenizer)

## Voice Cloning Compatibility

XTTS v2 voice clones (WAV/OGG reference audio) **cannot** be transferred
directly into other models. Each model has its own cloning format:

- **XTTS v2**: 6-15 seconds reference audio (WAV)
- **F5-TTS**: a few seconds reference audio (zero-shot)
- **Qwen3-TTS**: 3 seconds reference audio
- **Higgs-Audio V2**: 3-10 seconds reference audio
- **MOSS-TTS**: reference audio (no transcription needed)
- **Voxtral TTS**: 2-3 seconds reference audio (zero-shot, cross-lingual)

The **original audio recordings** of the voices can however be used as a
reference for every model. As long as the original recordings exist, switching
is no problem.

### 6. Voxtral TTS (4B) — Mistral AI
- **Pro:** frontier quality (beats ElevenLabs Flash v2.5), 9 languages incl.
  German natively, voice cloning from 2-3s reference, cross-lingual cloning
  (DE voice speaks EN with an accent), streaming, 20 preset voices, vLLM as
  runtime (already present), OpenAI-compatible API (`/v1/audio/speech`), 70ms
  latency on H200, ~10x real-time
- **Contra:** ~16 GB VRAM minimum (occupies a whole GPU next to the LLM),
  CC BY-NC 4.0 license (non-commercial), BF16 weights (P40/RTX 8000 no native
  BF16, would have to be tested in FP16), 4B parameters is a lot for TTS
- **Status:** deferred due to VRAM demand. Only relevant once a dedicated TTS
  GPU is available.
- **Links:** [HuggingFace](https://huggingface.co/mistralai/Voxtral-4B-TTS-2603) |
  [Mistral Blog](https://mistral.ai/news/voxtral-tts) |
  [Paper](https://mistral.ai/static/research/voxtral-tts.pdf)

## Recommended Evaluation Order

1. ~~Test **MOSS-TTS Local**~~ ✅ Integrated! (Docker container, VRAM reservation, good quality)
2. ~~Test **MOSS-TTS Delay 8B**~~ ✅ Integrated! (RTX 8000 float32, own generate() API, web UI with parameter sliders. See [moss-tts-8b-notes.md](../../de/models/moss-tts-8b-notes.md) (German))
3. Test **MOSS-TTS-Realtime** (streaming via `push_text`, same VRAM class as Local)
4. Test **F5-TTS** with a German fine-tune (smallest model, fastest)
5. Test **Qwen3-TTS** (native DE support, streaming via fork)
6. Test **Higgs-Audio V2** (if more expressiveness is wanted)
7. Test **Voxtral TTS** (once a dedicated TTS GPU is available, ~16 GB VRAM, frontier quality)
8. If none convinces: keep optimizing XTTS v2 with the carry mechanism

## Tips from the Community

- **Sentence chunking** is needed for ALL models (not just XTTS)
- **Buffer short sentences** (like our carry mechanism) - a universal problem
- **Test multiple seeds** - affects speaking style and stability
- **Silero VAD** for consistent silence between sentences
- **STT-based validation** (Whisper) to detect missing words
- **FlowHigh** can upsample 16kHz to 48kHz (relevant for Spark-TTS)
