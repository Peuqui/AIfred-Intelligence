# TTS-Container — Konventionen für neue Engines

Stand: 2026-05-23. Lebendes Dokument.

Wenn AIfred eine neue TTS-Engine als Docker-Container einbindet, soll
sie sich an die Konventionen unterhalb fügen. So bleibt das Audio-Setup
einheitlich, schnell und einfach erweiterbar.

---

## Goldene Regel

> **Audio-Bytes leben im Container, nicht auf der Wire.**

Der AIfred-Pfad schickt pro TTS-Request nur:
- den Eingabetext,
- den Voice-Namen (Speaker-ID),
- die Sprache.

Die Voice-Bytes liegen als Datei im Container — entweder beim Start als
Speaker-Embedding in den VRAM vorgewärmt, oder pro Request direkt von
der gemounteten Platte gelesen. Was wir auf **keinen** Fall machen:
WAV-Dateien base64-encodiert in den JSON-Body der API-Request stopfen
(>1 MB pro Anfrage, redundant, langsam).

---

## Voice-Verzeichnis auf der Host-Seite

```
docker/<engine>/voices/        # Mount-Quelle, read-only in den Container
```

**Layout-Variante A — flach** (Standard, XTTS / MOSS / Qwen3-TTS):

```
voices/
├── AIfred.wav      # 16 kHz mono, 5–15 s Sprechbeispiel
├── AIfred.txt      # Transkript (verbessert Cloning-Qualität)
├── HAL9000.wav
├── HAL9000.txt
└── ...
```

**Layout-Variante B — Subfolder** (Fish-Speech-Native):

```
voices/
├── AIfred/
│   ├── AIfred.wav
│   └── AIfred.lab   # Fish-Speech nennt das Transkript .lab
└── ...
```

→ Variante A ist Default. Variante B nur, wenn der Container das
`reference_id`-Schema des Upstream-Servers nutzt und der genau diese
Struktur erwartet (S2 Pro). In dem Fall: dem Upstream folgen, nicht
gegen den Strich bürsten.

---

## Volume-Mount im `docker-compose.yml`

```yaml
volumes:
  - ./voices:/app/voices:ro       # XTTS / MOSS / Qwen3 — flacher Standard-Pfad
  # ODER für Engines mit reference_id-API:
  - ./voices:/app/references:ro   # Fish-Speech S2 Pro
```

Read-only — der Container darf nichts in den Mount schreiben (Cache der
vorgewärmten Embeddings kommt in ein separates named volume).

---

## API-Schema

Das, was AIfred pro Request rüberschickt:

```json
// XTTS / MOSS / Qwen3-TTS — Standard
{
    "text": "Hallo Welt.",
    "speaker": "AIfred",
    "language": "de"
}

// Fish-Speech S2 Pro — reference_id statt speaker
{
    "text": "Hallo Welt.",
    "format": "wav",
    "reference_id": "AIfred",
    "normalize": true,
    "streaming": false
}
```

Server antwortet mit `Content-Type: audio/wav` (oder `audio/ogg`),
Body ist die Audio-Datei. Kein JSON-Wrapping um die Bytes — wir sparen
uns das base64-Decodieren auf der AIfred-Seite.

Implementierung pro Engine: [`aifred/lib/audio_processing.py`](../../../aifred/lib/audio_processing.py)
in `generate_speech_<engine>()`.

---

## Voice-Vorwärmung im Container — empfohlen

Voice-Cloning extrahiert beim ersten Request ein Speaker-Embedding aus
der Referenz-WAV. Das Encoding dauert je nach Modell 50–500 ms. Wenn
der Container das pro Request neu macht, zahlt jeder Sprachausgabe-Call
diesen Aufschlag. Daher:

> **Beim Container-Start einmalig alle Voices vorberechnen, im VRAM
> halten, pro Request nur das Embedding aus dem Dict ziehen.**

**Vorbilder:**
- Qwen3-TTS: `_warm_clone_prompts()` in [`docker/tts/qwen3-tts/server.py`](../../../docker/tts/qwen3-tts/server.py)
  baut x-vector + with-transcript Prompts pro Speaker in `_clone_prompts`.
- XTTS: identisches Muster in [`docker/tts/xtts/server.py`](../../../docker/tts/xtts/server.py),
  zusätzlich Disk-Cache als `.pth` damit der zweite Container-Start
  schon mit warmen Embeddings hochkommt.

**Ausnahmen:**
- MOSS-TTS hat keine Vorwärmung — der Upstream-`transformers`-Processor
  lädt die Referenz-WAV pro Request frisch von Disk. Funktioniert,
  bleibt aber langsamer als Qwen3/XTTS. Patchen lohnt nur bei tiefem
  Eingriff in `transformers` und ist deshalb bewusst nicht gemacht.
- Fish-Speech: S2-Pro-Server ist Upstream-Code, internes Caching ist
  Implementations-Detail. `reference_id` reicht.

Wenn die Engine keinen eigenen Server hat sondern auf einer fertigen
ASGI-App reitet (siehe Fish-Speech), reicht das native `reference_id`-
Schema — keine eigene Vorwärm-Logik nötig.

---

## Idle-Watchdog (Auto-Shutdown)

Jeder GPU-TTS-Container belegt mehrere GB VRAM. Damit das LLM auf
derselben GPU nicht permanent klein gerechnet wird, fährt der Container
sich nach `<ENGINE>_KEEP_ALIVE` Minuten Inaktivität selbst runter.

Implementierung:
- Eigener Server (XTTS / MOSS / Qwen3): Idle-Thread direkt im Server-Code,
  Reset bei jedem `/tts`-Request.
- Upstream-Server (Fish-Speech): ASGI-Middleware-Wrapper —
  [`docker/tts/fish-speech/aifred_idle_server.py`](../../../docker/tts/fish-speech/aifred_idle_server.py).

Health-Checks, `/openapi`, `/keep_alive` selbst und das WebUI dürfen
**nicht** als Aktivität zählen — sonst hält die `_detect_running_tts_engine()`-
Probe von AIfred (siehe [`aifred/lib/tts_engine_manager.py`](../../../aifred/lib/tts_engine_manager.py))
den Container für immer am Leben.

Env-Variable folgt dem Schema `<ENGINE>_KEEP_ALIVE=<minuten>` (z.B.
`XTTS_KEEP_ALIVE=15`, `FISH_SPEECH_KEEP_ALIVE=30`), `0` deaktiviert den
Watchdog.

---

## Port-Vergabe

Konvention im `docker-compose.yml`-Block:

```
XTTS         → 5051
Qwen3-TTS    → 5052
Fish-Speech  → 5053
(reserviert) → 5054
MOSS         → 5055
```

Neue Engines bekommen die nächste freie Nummer; den `5054`-Slot lassen
wir bewusst stehen, damit der Block beim Lesen einen klaren Rhythmus
behält.

---

## Calibration-Reserve (VRAM)

Damit die LLM-Kalibrierung neben dem TTS-Container nicht zu großzügig
plant, definiert jede Engine eine Reserve in `aifred/lib/config.py`:

```python
XTTS_VRAM_RESERVE_MB         = ...
MOSS_TTS_VRAM_RESERVE_MB     = ...
QWEN3_TTS_VRAM_RESERVE_MB    = ...
FISH_SPEECH_VRAM_RESERVE_MB  = ...
```

Empirisch bestimmt: idle + peak nach der längsten realistischen
Bubble, plus ~1–2 GB Headroom. Tunable via Env-Var (siehe Kommentare).

---

## Checkliste für eine neue TTS-Engine

1. **Voice-Verzeichnis** unter `docker/<engine>/voices/` anlegen,
   gleiche Speaker-Namen wie die anderen Engines (AIfred, HAL9000,
   Salomo, Sokrates), Layout-Variante A wenn nicht von Upstream
   vorgeschrieben.
2. **`docker-compose.yml`** mit Read-only-Mount auf `/app/voices` (oder
   `/app/references` bei Fish-Speech-ähnlichen Engines).
3. **Idle-Watchdog** mit `<ENGINE>_KEEP_ALIVE` ergänzen.
4. **Vorwärmung im Server-Code**, wenn möglich (Voice-Embeddings in
   `_clone_prompts`/`_custom_voices` o.ä. beim Startup).
5. **`generate_speech_<engine>()`** in `aifred/lib/audio_processing.py` —
   nur Text + Speaker-Name + Sprache senden. Antwort als Audio-Body
   schreiben, niemals base64.
6. **Engine-Registrierung** in `aifred/lib/tts_engines/` als
   `TTSEngine`-Subklasse + Eintrag in `TTS_ENGINES`-Dict.
7. **`<ENGINE>_VRAM_RESERVE_MB`** in `aifred/lib/config.py`.
8. **Port** nach Schema.
9. Calibration-Profil im `~/.config/llama-swap/config.yaml` als
   `<model>-tts-<engine>` Variante (siehe AIfred-Calibration-Doku).
