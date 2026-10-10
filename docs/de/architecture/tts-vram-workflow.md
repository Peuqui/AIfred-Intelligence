# TTS + VRAM Workflow — FreeEcho.2 & Browser

> **English version:** [tts-vram-workflow.md](../../en/architecture/tts-vram-workflow.md)

## Grundprinzip

**Nichts entladen außer es muss Platz gemacht werden.**
Alles bleibt geladen bis die nächste Anforderung etwas anderes braucht.

## GPU-TTS Engines

Die vier GPU-Engines **XTTS**, **MOSS-TTS**, **Qwen3-TTS** und **Fish-Speech**
belegen VRAM (jeweils Docker-Container mit GPU, Port je Engine: `default_port`).
Piper, Edge, eSpeak, DashScope brauchen kein VRAM. Dieselben Container können auf
einem anderen Rechner laufen (Eintrag in `tts_hosts`).

Die Engine, für die das LLM-Profil (`<modell>-tts-<engine>`) Platz reserviert, schaltet
der Browser (`ensure_tts_state`). Kanäle schalten sie nie um.

## Eskalationsliste (FreeEcho.2, Narrator)

Kanäle mit eigenem Lautsprecher (FreeEcho.2) und der Narrator wählen ihre Engine
nicht selbst, sondern über die **globale Eskalationsliste** (`lib/tts_escalation.py`).
Zwei Listen in `settings.json`:

- `tts_hosts`: andere Rechner, die TTS-Container mit derselben API betreiben
  (`name`, `address`, optional `ports` je Engine). Dieser Rechner ist implizit.
- `tts_escalation`: geordnete Einträge `{"engine", "host", "enabled"}`; `host: null`
  = dieser Rechner (bzw. Cloud). Der erste passende Eintrag von oben spricht.

Ein Eintrag **passt**, wenn er aktiv ist und:

| Art | Bedingung |
|-----|-----------|
| Remote (Container auf `host`) | Health-Abfrage antwortet. Der Mini startet oder stoppt dort nie etwas. |
| Lokal, GPU (XTTS, Qwen3, MOSS, Fish) | Container läuft — oder sein gemessener Burn-in-Peak (`tts_vram_cache`) + `LLAMACPP_TTS_BURNIN_HEADROOM_MB` passt in den **freien** Speicher der Sammelkarte (`pick_tts_gpu`); dann wird er gestartet. Ohne Messwert passt er nicht. |
| Lokal ohne GPU / Cloud | Engine ist verfügbar (`is_running`, bei DashScope: API-Key gesetzt). |

**Das Hauptmodell wird für TTS nie neu geladen.** Passt die lokale TTS nicht (z. B.
DeepSeek auf allen Karten), geht es still zum nächsten Eintrag — Aragon, Cloud, Piper.

**Ausfall:** Jede Engine wirft `TTSFailure` mit Grund (`unreachable`, `engine`,
`software`) statt `None` zu liefern. `SpeechRun` (eine Antwort/Ansage):

- Ausfall **vor dem ersten Ton** → still zum nächsten Eintrag (nur Debug-Zeile).
- Ausfall **mitten im Text** → der Nachfolger sagt zuerst den Stimmwechsel samt Grund
  an (`tts_switch_*` in `lib/i18n/{de,en}.json`) und spricht dann den unfertigen Satz.
- Ein ausgefallener Eintrag bleibt für den Rest der Antwort aus.

Der Narrator nimmt den obersten passenden Eintrag (oder die vom Agenten genannte
Engine) und bleibt für die ganze Datei dabei — kein Stimmwechsel mitten im Hörbuch.

**Rangfolge auf den Karten:** Hauptmodell > Describer > TTS > STT. Der Describer zählt
den VRAM von TTS-Container und Whisper-GPU-Worker als frei und gibt beide vor dem
Laden frei (`release_side_channels_for_describer`); eine laufende Ansage wechselt
dann zum nächsten Eintrag.

Debug-Konsole und `debug.log` (englisch):
```
🔊 [FreeEcho.2 buero] TTS escalation: skip xtts@Aragon (not serving on 10.0.0.2)
🔊 [FreeEcho.2 buero] TTS escalation: qwen3local@local speaks
🔊 [FreeEcho.2 buero] TTS escalation: qwen3local@local failed (unreachable): …
🔊 [FreeEcho.2 buero] TTS escalation: voice change announced, dashscope@local continues
```

## Browser — TTS Umschaltung

### Engine-Dropdown (Haupt-Einstellungen)
- Umschaltung erfolgt **sofort** (nicht nur Setting ändern)
- `set_tts_engine_or_off()` steuert: VRAM freimachen, neuen Container starten, LLM mit Profil neu laden
- "Aus" → `enable_tts=False`, GPU-Container stoppen

### Agent-Editor (pro Agent)
- Backend-Dropdown pro Agent: Wählt welches Backend für diesen Agenten gilt
- "Aus" → Agent bekommt kein TTS (`enabled=False`)
- Voice leer → Fallback auf den Engine-Default des Agenten aus `agents.json`, dann auf die globale `tts_voice` (siehe Voice-Auflösung)
- Änderungen werden nur als **Settings** gespeichert, kein sofortiger VRAM-Wechsel

### FreeEcho.2-Plugin
- Keine eigene Engine-Einstellung — es spricht über die Eskalationsliste
- Die Einheit der Sprachausgabe (Satz/Absatz/am Stück) ist die der sprechenden Engine

## Autoplay + Streaming

| Autoplay | Streaming | Verhalten |
|----------|-----------|-----------|
| ON | ON | Realtime: Sätze werden während Inferenz generiert und abgespielt |
| ON | OFF | Queue: Gesamtes Audio nach Inferenz, dann abspielen |
| OFF | * | Audio wird generiert (Play-Button), aber nicht automatisch abgespielt. Streaming-Wert wird ignoriert. |

## Voice-Auflösung

### Browser
SSOT: `_resolve_agent_tts()` in `_tts_streaming_mixin.py`. Ein Agent leiht sich nie
die Voice eines anderen Agenten.
1. Per-Agent-Voice des Users für die aktive Engine (`tts_agent_voices[agent]["voice"]`)
2. Engine-Default des Agenten aus `data/agents.json` (`tts_voices.<engine>`)
3. `self.tts_voice` (globaler State-Default) — nur für Agenten ohne Engine-Default

### Eskalationsliste (FreeEcho.2)
SSOT: `resolve_voice()` in `lib/tts_escalation.py`, je Engine des sprechenden Eintrags.
1. User-Setting für Agent+Engine (`tts_agent_voices_per_engine[engine][agent]` in `settings.json`)
2. User-Setting für AIfred (nur wenn der Agent keins hat)
3. Engine-Default des Agenten aus `data/agents.json` (`tts_voices.<engine>`, via `get_tts_voice_default()`)
4. Engine-Default von AIfred aus `data/agents.json`, wenn der Default des Agenten keine Voice hat
5. Keine Voice → der Eintrag fällt aus (`TTSFailure`), die Liste geht weiter

## Debug-Ausgaben

Bei jedem LLM-Profil-Wechsel wird das effektive Modell + Kontext angezeigt
(`get_effective_model_info()`; FreeEcho.2 und das Browser-Dropdown stellen den
Statusmeldungen 🔊 voran):
```
🔊 LLM profile ready: GPT-OSS-120B-A5B-UD-Q8_K_XL-tts-xtts (ctx: 131.072)
```

Bei der Intent-Detection (`format_intent_result()` in `intent_detector.py`):
```
🎯 Intent: FAKTISCH, Addressee: –, Lang: DE
```

## Code-Einstiegspunkte

| Funktion | Datei | Beschreibung |
|----------|-------|-------------|
| `ensure_tts_state()` | `tts_engine_manager.py` | SSOT: Prüft/stellt VRAM-State her |
| `_do_switch()` | `tts_engine_manager.py` | Voller Engine-Wechsel (entladen → laden) |
| `set_tts_engine_or_off()` | `_tts_config_mixin.py` | Browser-Dropdown Handler |
| `SpeechRun` / `choose_speaker()` | `lib/tts_escalation.py` | Eskalationsliste: Auswahl, Ausfall, Ansage |
| `resolve_voice()` | `lib/tts_escalation.py` | Voice/Speed/Pitch je Engine (Kanäle, Narrator) |
| `start_speech_stream()` | `lib/speech_synthesis.py` | Satzweiser PCM-Strom für Kanäle mit Lautsprecher |
| `release_side_channels_for_describer()` | `lib/vision_routing.py` | Describer verdrängt TTS und STT |
| `_queue_tts_for_agent()` | `_tts_streaming_mixin.py` | Browser TTS-Generierung |
| `_resolve_agent_tts()` | `_tts_streaming_mixin.py` | Browser-Auflösung von Voice/Speed/Pitch |
