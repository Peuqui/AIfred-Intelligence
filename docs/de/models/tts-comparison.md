# TTS Model Comparison for AIfred Intelligence

> Stand: 2026-03-28 | Quellen: Community-Reviews, GitHub, HuggingFace

## AIfred-Anforderungen

- **Multilingual** (mindestens Deutsch + Englisch)
- **Voice Cloning** (eigene Stimmen, keine Presets)
- **Expressiv** (Betonung, Emotion, Persoenlichkeit)
- **Streaming-faehig** (Satz-fuer-Satz TTS waehrend LLM streamt)
- **VRAM-Budget**: RTX 3090 Ti (24 GB), LLM laeuft parallel

## Aktuell: XTTS v2

Gute Betonung, Sprachpausen, multilingual (17+ Sprachen inkl. DE), Voice Cloning.
Schwaechen: Halluzination bei kurzem Text (< 3 Woerter), nicht sehr expressiv,
relativ langsam. Bestehende geklonte Stimmen muessen mit neuem Modell kompatibel sein
oder neu geklont werden.

---

## Streaming-Modi erklaert

Es gibt drei Stufen von TTS-"Streaming":

### 1. Chunk-basiert (XTTS v2, F5-TTS, Higgs-Audio)
```
LLM streamt → Satz erkennen → ganzer Satz an TTS → warten bis Audio fertig → abspielen
```
- Latenz = komplette Generierungszeit pro Satz (1-3 Sekunden)
- **Vorteil:** Modell sieht ganzen Satz → bessere Intonationsplanung
  (Fragesatz wird von Anfang an anders betont als Aussage)
- **Nachteil:** Hoechste Latenz, Satzerkennung noetig
- AIfred nutzt diesen Modus aktuell mit Carry-Mechanismus fuer kurze Saetze

### 2. Autoregressive Streaming (Qwen3-TTS, VoxCPM 1.5, Spark-TTS)
```
Satz an Modell → erste Audio-Frames sofort → abspielen WAEHREND Rest generiert wird
```
- Latenz = First-Audible ~200-300ms
- **Vorteil:** Niedrige Latenz, Satzanfang hoerbar bevor Satzende fertig
- **Nachteil:** Modell muss Intonation "raten" bevor Satzende bekannt

### 3. Echtes Text-Streaming (VibeVoice Realtime, MOSS-TTS-Realtime)
```
LLM streamt Wort fuer Wort → direkt an TTS → Audio kommt sofort
```
- Latenz = niedrigste (~80-300ms First-Packet)
- **Vorteil:** Keine Satzerkennung noetig, direktes LLM→TTS Piping
- **Nachteil:** Modell hat am wenigsten Kontext fuer Intonation
- **MOSS-TTS-Realtime** nutzt `push_text(delta)` API fuer inkrementelle Chunks
  mit KV-Cache Reuse ueber mehrere Turns (32K Kontext, ~40 Min.)

**Fazit:** Chunk-basiert ist nicht schlechter - nur langsamer. Die Qualitaet kann
sogar besser sein, weil das Modell den vollen Satzkontext hat. Fuer AIfred ist die
Latenz akzeptabel, da der LLM ohnehin satzweise streamt.

---

## Vergleichstabelle

| Modell | Parameter | Sprachen | DE | Voice Cloning | Expressiv | Streaming | Speed (RTFX) | Sample Rate | VRAM | Architektur | Lizenz |
|--------|-----------|----------|-----|---------------|-----------|-----------|-------------|-------------|------|-------------|--------|
| **XTTS v2** (aktuell) | ~1.5B | 17+ | Ja | Ja (6-15s Audio) | Mittel | Nein (Chunk-basiert) | ~0.5-1x | 24 kHz | ~2-4 GB | Autoregressive + DVAE | CPML |
| **F5-TTS** | ~335M | EN+CN (DE via Fine-Tune) | Ja (3 Fine-Tunes) | Ja (Zero-Shot, 10-15s) | Sehr hoch | Nein (Flow-basiert) | RTF 0.15 (~7x) | 24 kHz | ~2 GB | Flow Matching DiT | CC-BY-NC 4.0 (Weights) / MIT (Code) |
| **Qwen3-TTS** | 1.7B | 10 (CN,EN,DE,FR,JA,KO,RU,PT,ES,IT) | Ja (nativ) | Ja (3s Audio) | Hoch | Ja (via Fork) | ~1.8x | 24 kHz | ~4-6 GB | LLM-basiert | Apache 2.0 |
| **Higgs-Audio V2** | 3B | 50+ | Ja | Ja (3-10s Audio) | Sehr hoch (beste) | Unklar | ~1.8x | 24 kHz | ~8-12 GB | Llama-3.2-3B + DualFFN | Apache 2.0 |
| **EchoTTS** | Unklar | Unklar | Unklar | Ja (beste Aehnlichkeit) | Niedrig (monoton) | Nein | ~10x | 44.1 kHz | Unklar | Diffusion | Unklar |
| **Spark-TTS** | 0.5B | CN + EN | Nein | Ja (Zero-Shot) | Hoch | Ja | ~50x (schnellste!) | 16 kHz | ~2-4 GB | LLM Single-Stream | Apache 2.0 |
| **VoxCPM 1.5** | Unklar | CN + EN | Nein | Ja (Zero-Shot) | Unklar | Ja (RTF 0.17) | ~6x | 44.1 kHz | Unklar | Diffusion Autoregressive | Unklar |
| **VibeVoice TTS** | 1.5B | EN, CN, weitere | Unklar | Ja | Hoch | Nein | Unklar | Unklar | ~6-8 GB | Next-Token Diffusion @ 7.5Hz | MIT |
| **VibeVoice Realtime** | 0.5B | EN, CN, weitere | Unklar | Ja | Hoch | Ja (300ms Latenz) | Unklar | Unklar | ~2-4 GB | Next-Token Diffusion | MIT |
| **VibeVoice 7B** | 7B | EN, CN, weitere | Unklar | Ja (Multi-Speaker) | Hoch | Nein (Langform) | Unklar | Unklar | ~19 GB | Next-Token Diffusion | MIT |
| **PocketTTS** | Klein | Unklar | Unklar | Ja (Safetensors) | Unklar | Ja (OpenAI-API) | Unklar | Unklar | CPU moeglich! | Unklar | Unklar |
| **Dia (Nari Labs)** | 1.6B | Nur EN | Nein | Ja (wenige Sek.) | Sehr hoch | Unklar | Unklar | Unklar | ~6-8 GB | Unklar | Apache 2.0 |
| **CosyVoice-3** | Unklar | Multilingual | Unklar | Ja | Mittel | Unklar | Unklar | Unklar | Unklar | Unklar | Unklar |
| **IndexTTS 2** | Unklar | Unklar | Unklar | Ja | Unklar | Unklar | Unklar | Unklar | Unklar | Unklar | Unklar |
| **MOSS-TTS Local** | 1.7B | 20+ (CN,EN,DE,FR,ES,JA,KO,...) | Ja (nativ) | Ja (Zero-Shot) | Hoch | Nein (Chunk-basiert) | Unklar | 22.05 kHz | ~11.5 GB (BF16) | Global Latent + Local Transformer (MossTTSLocal) | Apache 2.0 |
| **MOSS-TTS Delay** | 8B | 20+ | Ja (nativ) | Ja (Zero-Shot) | Hoch | Nein | ~9.3 tok/s | 22.05 kHz | ~17 GB (BF16) / ~34 GB (FP32, Turing) | Delay Pattern (MossTTSDelay) | Apache 2.0 |
| **MOSS-TTS-Realtime** | 1.7B | 10+ (CN,EN,DE,FR,JA,KO,...) | Ja | Ja (Zero-Shot) | Hoch | Ja (Text-Streaming, `push_text`) | Unklar | 24 kHz | ~11.5 GB (BF16) + Codec | MossTTSRealtime + MOSS-Audio-Tokenizer | Apache 2.0 |
| **MOSS-TTSD** | 8B | Multilingual | Ja | Ja (1-5 Sprecher) | Sehr hoch | Nein | Unklar | 22.05 kHz | ~16 GB (BF16) | MossTTSDelay (Dialog) | Apache 2.0 |
| **MOSS-VoiceGenerator** | 8B | CN + EN | Nein | Nein (Stimme aus Textbeschreibung!) | Sehr hoch | Nein | Unklar | 22.05 kHz | ~16 GB (BF16) | MossTTSDelay | Apache 2.0 |
| **MOSS-SoundEffect** | ? | Multilingual | - | - (Soundeffekte) | - | Nein | Unklar | 22.05 kHz | ? | MossTTSDelay | Apache 2.0 |
| **Chatterbox** | Unklar | EN | Nein | Ja | Hoch | Ja (Sub-200ms) | Unklar | Unklar | Unklar | Unklar | Unklar |
| **Voxtral TTS** | 4B | 9 (EN,FR,DE,ES,NL,PT,IT,HI,AR) | Ja (nativ) | Ja (2-3s Audio) | Sehr hoch | Ja | ~10x (H200) | 24 kHz | ~16 GB | Transformer + Flow-Matching + Neural Codec | CC BY-NC 4.0 |

## Praxis-Test der integrierten Engines (2026-05-22)

Head-to-Head-Test der vier aktuell in AIfred integrierten Engines auf dem
MiniPC. Bedingungen: alle fp16, GPU V100 (HBM2), HAL9000-Referenzstimme
(mono, nach Stereo→Mono-Anhebung). Testsatz:

> „Sehr wohl, Sir, wie kann ich Ihnen heute behilflich sein? Es ist ein
> rather wunderschöner Tag am Teich im Herrengarten, indeed."

| Engine | Zeit | Qualität |
|--------|------|----------|
| **XTTS v2** | 2,8 s | Schnellste — aber schwächste: englische Wörter im deutschen Text werden schlecht ausgesprochen. |
| **Qwen3-TTS** | 8,52 s | Sehr angenehmer Akzent für AIfred (Originalstimme ist englisch), klingt richtig gut. Etwas heller als das Original — dürfte dunkler/rauer sein, ist es aber nicht. Akzeptabel schnell. |
| **Fish-Speech S2 Pro** | 11,01 s | Gute Qualität. |
| **MOSS-TTS** | 14,8 s | Langsamste — aber beste Qualität: Sprach-Eigenheiten sehr gut übernommen. |

**Fazit:** Geschwindigkeit und Qualität sind invers. XTTS ist mit Abstand
am schnellsten, aber bei gemischtsprachigem Text (englische Wörter im
Deutschen) am schwächsten. MOSS liefert die beste Qualität, ist aber am
langsamsten. Qwen3-TTS ist der beste Kompromiss: gute Qualität, passender
Akzent für die englische AIfred-Originalstimme, mit 8,5 s akzeptabel
schnell.

Hinweis: Reine Geschwindigkeits-Benchmarks (8-Satz-Text, V100 vs.
RTX 8000) und die Begründung der GPU-Wahl stehen im Docstring von
`aifred/lib/process_utils.py::_detect_tts_gpu_uuid`.

## Community-Bewertungen (Audiobook Use-Case)

### Prompt Audio Similarity (wie gut wird die Stimme geklont)
EchoTTS > Qwen3-TTS > Higgs-Audio > Spark-TTS

### Expressiveness (Betonung, Emotion, Dynamik)
Higgs-Audio > Spark-TTS ~ Qwen3-TTS > EchoTTS

### Stability (fehlende Woerter, Artefakte)
EchoTTS > Higgs-Audio > Spark-TTS ~ Qwen3-TTS

### Voice Variation (Stimmvariation je nach Textinhalt)
Higgs-Audio > Spark-TTS > Qwen3-TTS > EchoTTS

### Natural Sounding (Natuerlichkeit)
Spark-TTS ~ Qwen3-TTS > Higgs-Audio > EchoTTS

### Clarity (Audioqualitaet, abhaengig von Sample Rate)
EchoTTS (44 kHz) > Qwen3-TTS (24 kHz) > Higgs-Audio (24 kHz) > Spark-TTS (16 kHz)

### Cross-Lingual Voice Cloning (Stimme in Sprache X klonen, Sprache Y sprechen)
XTTS v2 > Qwen3-TTS > MOSS-TTS Local

**Eigener Test:** MOSS-TTS behaelt den Akzent der Referenz-Sprache. Englischer Sprecher
klingt auf Deutsch "auslaendisch". XTTS v2 trennt Stimm-Identitaet von Akzent besser
und klingt bei Cross-Lingual deutlich natuerlicher.

## Ausgeschiedene Modelle (Community-Feedback)

| Modell | Grund |
|--------|-------|
| VoxCPM 1.5 | "Overly sibilant" (zu viele Zischlaute) - ABER: Anderer Reviewer sagt "best streaming model" |
| VibeVoice | "Insufficient stability" |
| CosyVoice-3 | Audio nicht sauber, Klicks, Rauschen, Artefakte |
| IndexTTS 2 | Audio nicht sauber, Klicks, Rauschen, Artefakte |
| MOSS-TTS (alte Version) | Audio nicht sauber, Klicks und Rauschartefakte (Bewertung vor MOSS-TTS Family Release 10.02.2026). **Update:** Neue MOSS-TTS Family (Local 1.7B, Delay 8B, Realtime 1.7B) ist deutlich besser - State-of-the-Art Benchmarks, in AIfred integriert. |
| Chatterbox | Viele Artefakte |

**Anmerkung:** Diese Bewertungen stammen aus dem Audiobook-Use-Case (Langform).
Fuer AIfred (kurze Saetze, konversationell) koennen die Ergebnisse abweichen.

## Top-Kandidaten fuer AIfred

### 1. F5-TTS
- **Pro:** Hoechste Qualitaet laut Community, 3 deutsche Fine-Tunes verfuegbar, schnell
  (RTF 0.15 auf L20, mit TensorRT ~25x Echtzeit), sehr expressiv, relativ klein (~335M,
  ~1.35 GB auf Disk), nur ~2 GB VRAM, Zero-Shot Voice Cloning (10-15s Referenz),
  `pip install f5-tts` (einfache Installation), P40-kompatibel (FP32)
- **Contra:** Kein natives Streaming (Flow-basiert, generiert ganzen Satz auf einmal),
  CC-BY-NC Lizenz fuer Weights (Code ist MIT), Base-Modell nur EN+CN (DE braucht Fine-Tune),
  ~5% Artefakte an Chunk-Grenzen, Reference Audio Leakage moeglich,
  Cross-Lingual Voice Cloning = Akzent der Referenz bleibt (wie MOSS-TTS),
  braucht ref_text Transkription (sonst Whisper ASR +2 GB VRAM)
- **Deutsche Fine-Tunes:**
  - [aihpi/F5-TTS-German](https://huggingface.co/aihpi/F5-TTS-German) (HPI, BMBF-gefuerdert, 8x H100 Training)
  - [hvoss-techfak/F5-TTS-German](https://huggingface.co/hvoss-techfak/F5-TTS-German) (offiziell im F5-TTS Repo gelistet)
  - [tabularisai/f5-tts-german-voice-clone](https://huggingface.co/tabularisai/f5-tts-german-voice-clone) (WIP, Cloning-optimiert)
- **Links:** [GitHub](https://github.com/SWivid/F5-TTS) | [PyPI](https://pypi.org/project/f5-tts/)
- **Docker-Container:** `docker/f5-tts/` (Port 5052)

### 2. Qwen3-TTS
- **Pro:** Deutsch nativ unterstuetzt, Streaming via Fork, guter Allrounder,
  3s Voice Cloning, Cross-lingual Cloning, Apache 2.0
- **Contra:** Nur ~1.8x RTFX, nicht die expressivste
- **Links:** [GitHub](https://github.com/QwenLM/Qwen3-TTS) |
  [Streaming Fork](https://github.com/dffdeeq/Qwen3-TTS-streaming)

### 3. Higgs-Audio V2
- **Pro:** Expressivste aller Modelle, 50+ Sprachen, Voice Cloning,
  75.7% Win-Rate ueber GPT-4o-mini-tts bei Emotionen, Apache 2.0
- **Contra:** Qualitaetsschwankungen bei starker Variation, 3B braucht mehr VRAM,
  ~1.8x RTFX
- **Links:** [GitHub](https://github.com/boson-ai/higgs-audio) |
  [HuggingFace](https://huggingface.co/bosonai/higgs-audio-v2-generation-3B-base)

### 4. MOSS-TTS Local (1.7B)
- **Pro:** State-of-the-Art Benchmarks (EN SIM 73.42%, ZH SIM 78.82% - beste Open-Source),
  20 Sprachen inkl. Deutsch nativ, Zero-Shot Voice Cloning, Pinyin/IPA Phonem-Kontrolle,
  bis zu 1 Stunde Audio in einem Run, Apache 2.0
- **Contra:** Brandneu (10.02.2026), braucht Python 3.12 + PyTorch 2.9 + Transformers 5.0
  (bleeding edge Dependencies), Community-Bewertung der Vorgaengerversion war negativ
  (Artefakte), ~11.5 GB VRAM (gemessen auf RTX 3090 Ti, BF16), Cross-Lingual Voice
  Cloning schwach (behaelt Akzent der Referenzsprache), ~18-22s pro Satz (nicht
  streaming-geeignet)
- **Links:** [GitHub](https://github.com/OpenMOSS/MOSS-TTS) |
  [HuggingFace Local](https://huggingface.co/OpenMOSS-Team/MOSS-TTS-Local-Transformer) |
  [HuggingFace 8B](https://huggingface.co/OpenMOSS-Team/MOSS-TTS) |
  [HuggingFace Realtime](https://huggingface.co/OpenMOSS-Team/MOSS-TTS-Realtime)
- **Docker-Container:** `docker/moss-tts/` (Port 5055)
- **Benchmarks (Seed-TTS-eval):**
  | Modell | EN WER | EN SIM | ZH CER | ZH SIM |
  |--------|--------|--------|--------|--------|
  | MOSS-TTS Local 1.7B | 1.85 | 73.42 | 1.2 | 78.82 |
  | MOSS-TTS Delay 8B | 1.79 | 71.46 | 1.32 | 77.05 |
  | Qwen3-TTS 1.7B | 1.50 | 71.45 | 1.33 | 76.72 |
  | CosyVoice3 1.5B | 2.22 | 72.0 | 1.12 | 78.1 |
  | F5-TTS 0.3B | 2.00 | 67.0 | 1.53 | 76.0 |
  | VoxCPM 0.5B | 1.85 | 72.9 | 0.93 | 77.2 |

### 5. MOSS-TTS-Realtime (1.7B)
- **Pro:** Echtes Text-Streaming via `push_text(delta)` - LLM-Chunks direkt als Audio,
  Multi-Turn KV-Cache Reuse (Stimmkonsistenz ueber Turns), 32K Kontext (~40 Min.),
  10+ Sprachen inkl. Deutsch, 1.7B Parameter, Apache 2.0,
  trainiert auf 2.5M+ Stunden Single-Speaker + 1M+ Stunden Multi-Speaker Daten
- **Contra:** Separate Architektur (MossTTSRealtime, nicht kompatibel mit MossTTSLocal),
  braucht zusaetzlichen MOSS-Audio-Tokenizer Codec (~24 kHz Output),
  SIM-Score etwas niedriger als Local (68.9% vs 73.42% EN),
  brandneu, noch nicht getestet
- **MOSS-TTS Familie (komplett):**
  | Modell | Params | Fokus | Fuer AIfred? |
  |--------|--------|-------|-------------|
  | MossTTSLocal | 1.7B | Beste Benchmarks, Forschung | Ja (aktuell integriert) |
  | MossTTSDelay | 8B | Produktion, Langform-Stabilitaet | Ja (RTX 8000, float32 ~34 GB) |
  | MossTTSRealtime | 1.7B | Streaming, Voice Agents | Ja (naechster Kandidat!) |
  | MOSS-TTSD | 8B | Multi-Speaker Dialog (1-5 Sprecher) | Nein (zu viel VRAM) |
  | MOSS-VoiceGenerator | 8B | Stimmen aus Textbeschreibung | Nein (zu viel VRAM) |
  | MOSS-SoundEffect | ? | Soundeffekte aus Text | Nein (nicht TTS) |
- **Links:** [HuggingFace](https://huggingface.co/OpenMOSS-Team/MOSS-TTS-Realtime) |
  [Audio-Tokenizer](https://huggingface.co/OpenMOSS-Team/MOSS-Audio-Tokenizer)

## Voice Cloning Kompatibilitaet

XTTS v2 Voice Clones (WAV/OGG Referenz-Audio) koennen **nicht** direkt in andere
Modelle uebernommen werden. Jedes Modell hat sein eigenes Cloning-Format:

- **XTTS v2**: 6-15 Sekunden Referenz-Audio (WAV)
- **F5-TTS**: Wenige Sekunden Referenz-Audio (Zero-Shot)
- **Qwen3-TTS**: 3 Sekunden Referenz-Audio
- **Higgs-Audio V2**: 3-10 Sekunden Referenz-Audio
- **MOSS-TTS**: Referenz-Audio (keine Transkription noetig)
- **Voxtral TTS**: 2-3 Sekunden Referenz-Audio (Zero-Shot, Cross-Lingual)

Die **originalen Audioaufnahmen** der Stimmen koennen aber fuer jedes Modell
als Referenz verwendet werden. Solange die Originalaufnahmen vorhanden sind,
ist ein Wechsel problemlos.

### 6. Voxtral TTS (4B) — Mistral AI
- **Pro:** Frontier-Qualitaet (schlaegt ElevenLabs Flash v2.5), 9 Sprachen inkl. Deutsch nativ,
  Voice Cloning ab 2-3s Referenz, Cross-Lingual Cloning (DE-Stimme spricht EN mit Akzent),
  Streaming, 20 Preset-Stimmen, vLLM als Runtime (bereits vorhanden), OpenAI-kompatible API
  (`/v1/audio/speech`), 70ms Latenz auf H200, ~10x Echtzeit
- **Contra:** ~16 GB VRAM Minimum (belegt eine komplette GPU neben dem LLM),
  CC BY-NC 4.0 Lizenz (nicht-kommerziell), BF16-Weights (P40/RTX 8000 kein natives BF16,
  muesste FP16 getestet werden), 4B Parameter sind viel fuer TTS
- **Status:** Zurueckgestellt wegen VRAM-Bedarf. Erst relevant wenn dedizierte TTS-GPU verfuegbar.
- **Links:** [HuggingFace](https://huggingface.co/mistralai/Voxtral-4B-TTS-2603) |
  [Mistral Blog](https://mistral.ai/news/voxtral-tts) |
  [Paper](https://mistral.ai/static/research/voxtral-tts.pdf)

## Empfohlene Evaluierungsreihenfolge

1. ~~**MOSS-TTS Local** testen~~ ✅ Integriert! (Docker-Container, VRAM-Reservation, gute Qualitaet)
2. ~~**MOSS-TTS Delay 8B** testen~~ ✅ Integriert! (RTX 8000 float32, eigene generate()-API, Web-UI mit Param-Slidern. Siehe [moss-tts-8b-notes.md](moss-tts-8b-notes.md))
3. **MOSS-TTS-Realtime** testen (Streaming via `push_text`, gleiche VRAM-Klasse wie Local)
4. **F5-TTS** mit deutschem Fine-Tune testen (kleinstes Modell, schnellstes)
5. **Qwen3-TTS** testen (nativer DE-Support, Streaming via Fork)
6. **Higgs-Audio V2** testen (wenn mehr Expressivitaet gewuenscht)
7. **Voxtral TTS** testen (wenn dedizierte TTS-GPU verfuegbar, ~16 GB VRAM, Frontier-Qualitaet)
8. Falls keines ueberzeugt: XTTS v2 mit Carry-Mechanismus weiter optimieren

## Tipps aus der Community

- **Sentence Chunking** ist bei ALLEN Modellen noetig (nicht nur XTTS)
- **Kurze Saetze buffern** (wie unser Carry-Mechanismus) - universelles Problem
- **Mehrere Seeds testen** - beeinflusst Sprechweise und Stabilitaet
- **Silero VAD** fuer konsistente Stille zwischen Saetzen
- **STT-basierte Validierung** (Whisper) um fehlende Woerter zu erkennen
- **FlowHigh** kann 16kHz auf 48kHz upsamplen (relevant fuer Spark-TTS)
