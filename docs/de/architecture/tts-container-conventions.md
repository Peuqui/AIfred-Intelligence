# TTS-Container — Konventionen für neue Engines

> **English version:** [tts-container-conventions.md](../../en/architecture/tts-container-conventions.md)

Stand: 2026-10-10. Lebendes Dokument.

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
`TTSEngine`-Subklasse in
[`aifred/lib/tts_engines/<key>/engine.py`](../../../aifred/lib/tts_engines/)
(z.B. `xtts/engine.py`). Container-Engines rufen dafür `_synthesize_via_http()` der
Basisklasse auf. `audio_processing.py` dispatcht nur auf `eng.generate_speech_async(...)`.

---

## Engine-Struktur im Code

Jede Engine liegt in **einem eigenen Ordner** `aifred/lib/tts_engines/<key>/`:

```
aifred/lib/tts_engines/
├── base.py              # TTSEngine: Basisklasse samt Container-Gerüst
├── registry.py          # findet die Ordner, baut TTS_ENGINES
├── xtts/
│   ├── engine.py        # die TTSEngine-Subklasse
│   └── i18n.json        # {"de": "...", "en": "..."} — Beschriftung für Dropdowns
├── qwen3local/ · moss/ · fishspeech/ · piper/ · edge/ · espeak/ · browser/ · dashscope_audio3/
```

- Der **Ordnername ist der Engine-Key**. `registry.py` importiert `*/engine.py` jedes Ordners;
  die `TTSEngine`-Subklasse meldet sich damit an, sortiert nach `display_order`. Es gibt kein
  zentrales Dict und keine Liste, die mitzupflegen wäre.
- `i18n.json` enthält die lange Beschriftung in Deutsch und Englisch (`engine.label(lang)`).
- Die **Standard-Eskalationsliste** einer frischen Installation leitet sich aus den Engines ab:
  alle mit `in_default_escalation = True`, in `display_order`
  (`registry.default_escalation()`, über `config.default_tts_escalation()` in
  `DEFAULT_SETTINGS["tts_escalation"]`). Der Browser hat die höchste `display_order` und
  steht damit zuletzt. Siehe [tts-escalation.md](tts-escalation.md).

### Container-Gerüst in `base.py`

Start, Stopp und Bereitschaft eines Containers stehen **einmal** in
[`base.py`](../../../aifred/lib/tts_engines/base.py) (`start()`, `stop()`, `ensure_ready()`,
`is_running()`). Eine Container-Engine setzt nur Klassenattribute und überschreibt Haken, wo
ihr Container abweicht:

| Attribut / Haken | Bedeutung | Standard |
|------------------|-----------|----------|
| `runs_in_container`, `needs_gpu` | Docker-Container; belegt VRAM | `False` |
| `image_name` | lokales Docker-Image — dessen Existenz entscheidet über „installiert“ (`is_installed()`) | `None` |
| `compose_subdir` | Ordner unter `docker/tts/`, wenn er vom Key abweicht (`moss` → `moss-tts`) | Key |
| `default_port` | Port der REST-API; `None` = kein REST (Cloud/CLI), die Engine kann nicht auf einem anderen Rechner laufen | `None` |
| `health_path` | Health-Endpunkt relativ zur Service-URL | `/health` |
| `_model_ready(health)` | wann die Health-Antwort „Modell geladen“ heißt | `health["model_loaded"]` |
| `_device(health)` | Rechenziel aus der Health-Antwort (`cuda:0`, `cpu`) | `health["device"]` |
| `startup_timeout_s` | Sekunden, bis Container und Modell bereit sein müssen (> 0 bei Container-Engines) | 0 |
| `restart_when_on_cpu` | Container einmal neu starten, wenn er auf der CPU landet, obwohl eine GPU frei ist (MOSS) | `False` |
| `max_parallel_requests` | gleichzeitige Synthesen je Engine und Rechner | 2 |
| `in_default_escalation` | Teil der Standard-Eskalationsliste | `False` |
| `cloud` | Text verlässt das Haus (Etikett in der Liste) | `False` |
| `default_speech_unit` | Standard-Einheit der Sprachausgabe (`sentence`/`paragraph`/`whole`) | `sentence` |
| `default_voice` | Stimme, wenn weder User noch `agents.json` eine nennen | `None` |

Zeiten der vorhandenen Container-Engines: XTTS 60 s, Qwen3-TTS 240 s, MOSS 180 s, Fish-Speech
600 s (`startup_timeout_s`); Qwen3-TTS hat `max_parallel_requests = 1`.

Dasselbe Gerüst gilt für Container auf einem anderen Rechner (`at_host()`): dort wird nichts
von hier gestartet, nur die Health-Abfrage genutzt, der Host startet seine Container selbst
(siehe [tts-vram-workflow.md](tts-vram-workflow.md)).

### Vollständigkeitstest

[`tests/test_tts_engine_completeness.py`](../../../tests/test_tts_engine_completeness.py)
prüft für alle Engines:

- Jeder Ordner mit `engine.py` enthält genau eine registrierte Engine; der Ordnername ist der Key.
- Jede Engine hat ihren Ordner (`package_dir`) und nicht-leere Beschriftungen in `de` und `en`,
  die sich über `tts_key_to_label` / `tts_label_to_key` verlustfrei umkehren lassen.
- Jede Container-Engine stimmt mit ihrer `docker-compose.yml` überein (`image:`-Name,
  Host-Port `default_port`) und hat `startup_timeout_s > 0`.
- Engines ohne Container deklarieren weder `image_name` noch eine compose-Datei.
- Die Standard-Eskalationsliste kommt aus den Engines, der Browser steht zuletzt, alle Einträge
  sind aktiv und `host: null`.

---

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
wachsen (XTTS, Qwen3-TTS, Fish-Speech), überschreiben `calibration_setup()`, damit der
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
5. **Engine-Ordner** `aifred/lib/tts_engines/<key>/` mit `engine.py` (`TTSEngine`-Subklasse:
   `key`, `label_short`, `display_order`, bei Container-Engines `runs_in_container`, `needs_gpu`,
   `image_name`, `default_port`, `startup_timeout_s`, ggf. `compose_subdir`, `health_path`,
   `_model_ready`, `_device`, `restart_when_on_cpu`) und `i18n.json` (`de`/`en`). Der Ordnername
   ist der Key und muss dem Suffix der llama-swap-Profile `<modell>-tts-<key>` entsprechen.
   Start/Stopp/Bereitschaft kommen aus der Basisklasse — nicht nachbauen.
6. **`generate_speech()`**: nur Text + Speaker-Name + Sprache senden (bei Container-Engines
   über `_synthesize_via_http()`), Antwort als Audio-Body, niemals base64; bei Ausfall
   `TTSFailure` werfen statt `None` zurückzugeben.
   **Registrierung:** nichts weiter — [`registry.py`](../../../aifred/lib/tts_engines/registry.py)
   findet den Ordner automatisch. Soll die Engine in der Standard-Eskalationsliste stehen:
   `in_default_escalation = True`.
7. **VRAM-Reserve**: nichts zu pflegen — der Stress-Burn-In misst sie bei der
   ersten Kalibrierung (siehe oben).
8. **Port** nach Schema.
   Zum Schluss `python -m pytest tests/test_tts_engine_completeness.py`.
9. Calibration-Profil im `~/.config/llama-swap/config.yaml` als
   `<model>-tts-<engine>` Variante (siehe AIfred-Calibration-Doku).
