# TTS-Container — Konventionen für neue Engines

> **English version:** [tts-container-conventions.md](../../en/architecture/tts-container-conventions.md)

Stand: 2026-09-25. Lebendes Dokument.

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

Alle Engines teilen sich **ein** Voice-Verzeichnis; jede Engine liegt in einem
eigenen Ordner daneben:

```
docker/tts/
├── voices/              # gemeinsame SSOT, read-only in jeden Container gemountet
├── xtts/
├── qwen3-tts/
├── fish-speech/
└── moss-tts/
```

Layout: ein Unterordner pro Sprecher. Er bedient alle Engines gleichzeitig —
XTTS / MOSS / Qwen3-TTS lesen `<Name>/<Name>.wav` (+ `.txt`), Fish-Speech S2 Pro
nutzt den Ordnernamen als `reference_id` und liest das `.lab`-Transkript:

```
voices/
├── AIfred/
│   ├── AIfred.wav   # Mono-Sprechbeispiel (die vorhandenen Voices: 15–30 s)
│   ├── AIfred.txt   # Transkript (XTTS / MOSS / Qwen3-TTS, verbessert Cloning-Qualität)
│   └── AIfred.lab   # gleiches Transkript unter dem Namen, den Fish-Speech erwartet
├── HAL9000/
├── Salomo/
└── Sokrates/
```

Die Server suchen Voices per `glob("*/*.wav")` — der Sprechername ist der Datei-Stamm.

---

## Volume-Mount im `docker-compose.yml`

```yaml
volumes:
  - ../voices:/app/voices:ro       # XTTS / MOSS / Qwen3-TTS
  # ODER für Engines mit reference_id-API:
  - ../voices:/app/references:ro   # Fish-Speech S2 Pro
```

Read-only — der Container darf nichts in den Mount schreiben (Cache der
vorgewärmten Embeddings kommt in ein separates named volume, z.B. XTTS:
`xtts_voices` auf `/app/custom_voices`).

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

Implementierung pro Engine: die `generate_speech()`-Methode der jeweiligen
`TTSEngine`-Subklasse unter
[`aifred/lib/tts_engines/<engine>.py`](../../../aifred/lib/tts_engines/)
(z.B. `xtts.py`). `audio_processing.py` dispatcht nur noch auf
`eng.generate_speech_async(...)`.

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
- XTTS: identisches Muster in [`docker/tts/xtts/server.py`](../../../docker/tts/xtts/server.py)
  (`_custom_voices`), zusätzlich Disk-Cache als `.pth` in `/app/custom_voices`, damit
  der zweite Container-Start schon mit warmen Embeddings hochkommt.

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

Env-Variable folgt dem Schema `<ENGINE>_KEEP_ALIVE=<minuten>`
(`XTTS_KEEP_ALIVE`, `MOSS_KEEP_ALIVE`, `QWEN3_KEEP_ALIVE`, `FISH_SPEECH_KEEP_ALIVE`;
die `docker-compose.yml`-Dateien setzen jeweils 45), `0` deaktiviert den Watchdog.

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
plant, wird die Reserve pro Engine **gemessen**, nicht von Hand gepflegt:

- `resolve_tts_reserve(engine_key)` in
  [`aifred/lib/tts_stress_burnin.py`](../../../aifred/lib/tts_stress_burnin.py)
  liefert den gecachten Peak aus
  [`aifred/lib/tts_vram_cache.py`](../../../aifred/lib/tts_vram_cache.py)
  (`data/tts_vram_cache.json`) plus `LLAMACPP_TTS_BURNIN_HEADROOM_MB`
  (config.py, 512 MB).
- Bei einem Cache-Miss läuft vorher der Stress-Burn-In: Container starten, eine
  absichtlich lange zweisprachige Synthese abfeuern, den GPU-Speicher alle 100 ms
  abfragen, den Peak in den Cache schreiben. Kein TTL — der Wert bleibt, bis der
  Reset-Button im Kalibrier-Picker (`reset_tts_vram_cache`) die Tabelle leert oder
  der Eintrag gelöscht wird.
- Manueller Neulauf: `python -m aifred.lib.tts_stress_burnin <engine_key>`.
- Die Kalibrierung (`_calibration_mixin.py`) plant die TTS-GPU um diese Reserve
  herum; `calibration_setup()` der Engine lässt den Container bewusst kalt, damit
  der Idle-Footprint nicht doppelt zählt.

Handgesetzte Reserven pro Engine gibt es nicht: Die Kalibrierung nutzt
ausschließlich `resolve_tts_reserve()`. Engines, die während der Generierung
wachsen (Qwen3-TTS, Fish-Speech), überschreiben `calibration_setup()`, damit der
Container während der Kalibrierung kalt bleibt — der Burn-in-Peak enthält seinen
Leerlauf-Bedarf bereits.

---

## Checkliste für eine neue TTS-Engine

1. **Gemeinsames Voice-Verzeichnis** `docker/tts/voices/` nutzen (AIfred, HAL9000,
   Salomo, Sokrates); fehlende Transkripte dort nur ergänzen, wenn die Engine
   einen anderen Dateinamen braucht (wie `.lab` bei Fish-Speech).
2. **`docker/tts/<engine>/docker-compose.yml`** mit Read-only-Mount
   `../voices:/app/voices:ro` (oder `/app/references` bei Fish-Speech-ähnlichen Engines).
3. **Idle-Watchdog** mit `<ENGINE>_KEEP_ALIVE` ergänzen.
4. **Vorwärmung im Server-Code**, wenn möglich (Voice-Embeddings in
   `_clone_prompts`/`_custom_voices` o.ä. beim Startup).
5. **`generate_speech()`-Methode** in der neuen `TTSEngine`-Subklasse
   unter `aifred/lib/tts_engines/<engine>.py` — nur Text + Speaker-Name +
   Sprache senden. Antwort als Audio-Body schreiben, niemals base64.
6. **Engine-Registrierung**: nur die `TTSEngine`-Subklasse (mit `key`,
   `label_short`, `needs_gpu`, `image_name`, `compose_subdir` falls der Ordnername
   vom Key abweicht) in `aifred/lib/tts_engines/<engine>.py` —
   [`registry.py`](../../../aifred/lib/tts_engines/registry.py) findet sie
   automatisch und baut `TTS_ENGINES`, kein manueller Eintrag.
7. **VRAM-Reserve**: nichts zu pflegen — der Stress-Burn-In misst sie bei der
   ersten Kalibrierung (siehe oben).
8. **Port** nach Schema.
9. Calibration-Profil im `~/.config/llama-swap/config.yaml` als
   `<model>-tts-<engine>` Variante (siehe AIfred-Calibration-Doku).
