# MOSS-TTS 8B on Turing GPUs (RTX 8000 / RTX 6000)

> Stand: 2026-02-20 | Getestet auf: NVIDIA RTX 8000 (48 GB, Turing, CC 7.5)

## Zusammenfassung

Das MOSS-TTS Delay 8B Modell (`OpenMOSS-Team/MOSS-TTS`, MossTTSDelayModel)
läuft auf Turing-GPUs (Compute Capability < 8.0) nur in **float32**.
Float16 und bfloat16 führen zu NaN-Werten und Abstürzen.

---

## Probleme und Lösungen

### 1. Fehlende generation_config.json

**Fehler:**
```
OSError: OpenMOSS-Team/MOSS-TTS does not appear to have a file named generation_config.json
```

**Ursache:** Das 8B HuggingFace-Repo hat keine `generation_config.json` (im Gegensatz
zu Standard-HF-Modellen).

**Lösung:** `GenerationConfig` nicht via `from_pretrained()` laden, sondern direkt
instanziieren oder komplett weglassen — das 8B Modell braucht sie nicht.

### 2. Inkompatible generate() API

**Fehler:**
```
TypeError: MossTTSDelayModel.generate() got an unexpected keyword argument 'generation_config'
```

**Ursache:** Das 8B Modell (MossTTSDelay) hat eine eigene `generate()`-Signatur,
die NICHT kompatibel mit der Standard-HuggingFace-API ist.

**8B generate() Signatur:**
```python
model.generate(
    input_ids=...,
    attention_mask=...,
    max_new_tokens=4096,
    text_temperature=1.5,      # Temperature für Text-Tokens
    text_top_p=0.8,
    text_top_k=25,
    audio_temperature=1.7,     # Temperature für Audio-Tokens
    audio_top_p=0.8,
    audio_top_k=25,
    audio_repetition_penalty=1.0,
)
```

**Lösung:** Parameter direkt übergeben statt über `GenerationConfig`.

### 3. Float16 NaN auf Turing GPUs

**Fehler:**
```
RuntimeError: probability tensor contains either inf, nan or element < 0
```
(via `torch.multinomial` in der generate()-Methode)

**Ursache:** Die generate()-Methode dividiert Logits durch Temperature in-place
(`logit / text_temperature`). In float16 auf Turing-GPUs (CC 7.5, kein nativer
bfloat16-Support) laufen die Werte über und erzeugen NaN/Inf.

**Fehlgeschlagene Ansätze:**
- Monkey-Patching von `sample_token` für float32-Cast: NaN entsteht VOR sample_token
- Patching in `inference_utils` und `modeling_moss_tts`: gleicher Grund

**Lösung:** Gesamtes Modell in **float32** laden:
```python
def resolve_dtype():
    if device != "cuda":
        return torch.float32
    major, _ = torch.cuda.get_device_capability()
    if major >= 8:  # Ampere+: bfloat16 nativ
        return torch.bfloat16
    else:           # Turing/älter: float32
        return torch.float32
```

---

## VRAM-Verbrauch

| Precision | VRAM (gemessen) | Modell-Größe | Status |
|-----------|----------------|----------------|--------|
| bfloat16  | ~17 GB         | ~17 GB         | Nur Ampere+ (CC >= 8.0) |
| float16   | ~17 GB         | ~17 GB         | **NaN auf Turing** — nicht verwenden! |
| float32   | ~41.6 GB       | ~34 GB         | Funktioniert auf allen GPUs |

**Mindest-VRAM für float32:** ~34 GB (Modell) + Overhead = ca. 38-42 GB
→ RTX 8000 (48 GB) passt, RTX 3090 Ti (24 GB) reicht NICHT.

---

## Performance

Gemessen auf RTX 8000 (float32):
- **Generierungszeit:** ~13.5 Sekunden für 2 Sätze (~30 Wörter)
- **Token-Geschwindigkeit:** ~9.3 Tokens/Sekunde
- **Qualität:** Gut, aber weniger Intonation/Emotion als XTTS v2

Vergleich mit 1.7B Modell (MossTTSLocal):
- 1.7B auf RTX 8000 (bfloat16): ~18-22 Sekunden pro Satz
- 8B auf RTX 8000 (float32): ~13.5 Sekunden für 2 Sätze
- Das 8B Modell ist trotz float32 schneller als das 1.7B (andere Architektur: Delay vs Local)

---

## Vergleich 1.7B (Local) vs 8B (Delay)

| Eigenschaft | MOSS-TTS Local 1.7B | MOSS-TTS Delay 8B |
|-------------|---------------------|-------------------|
| Architektur | MossTTSLocal (Global Latent + Local Transformer) | MossTTSDelay (Delay Pattern) |
| generate() API | Standard HuggingFace (`generation_config`) | Eigene API (direkte Parameter) |
| VRAM (BF16) | ~11.5 GB | ~17 GB |
| VRAM (FP32) | ~22 GB | ~34 GB |
| Min. GPU (BF16) | RTX 3090 Ti (24 GB) | RTX A5000 (24 GB) — knapp |
| Min. GPU (FP32) | RTX 8000 (48 GB) — überdimensioniert | RTX 8000 (48 GB) |
| Sample Rate | 22.05 kHz | 22.05 kHz |
| Seed-TTS EN SIM | 73.42% (besser) | 71.46% |
| Seed-TTS ZH SIM | 78.82% (besser) | 77.05% |
| Parameter-Steuerung | Standard (temperature, top_p, top_k) | Getrennt: audio_* und text_* |

---

## Docker-Konfiguration

**docker-compose.8b.yml Besonderheiten:**
- `MOSS_VRAM_THRESHOLD=30.0` (float32 braucht ~34 GB)
- `MOSS_MODEL=OpenMOSS-Team/MOSS-TTS` (8B Hauptrepo, nicht Local/Realtime)
- Separates Volume `moss_models_8b` (~17 GB Download)
- `start_period: 600s` im Healthcheck (10 Min. für Erstdownload)

**server_8b.py Features:**
- Web-UI mit Parameter-Slidern (Audio/Text Temperature, Top-P, Top-K, Repetition Penalty)
- Per-Request Parameter-Overrides via `/tts` Endpoint
- Reset-to-Defaults Button
- Playback-Speed Controls

---

## Bekannte Einschränkungen

1. **Kein float16 auf Turing**: NaN in der generate()-Methode, nicht patchbar
2. **Hoher VRAM-Verbrauch**: 41.6 GB von 48 GB — wenig Headroom für lange Texte
3. **Weniger expressiv als XTTS v2**: Gute Qualität, aber monotoner in der Intonation
4. **Cross-Lingual Voice Cloning**: Behält Akzent der Referenzsprache (wie 1.7B)
5. **Kein Streaming**: Chunk-basiert, generiert kompletten Audio-Block auf einmal
