# TTS + VRAM Workflow — FreeEcho.2 & Browser

> **English version:** [tts-vram-workflow.md](../../en/architecture/tts-vram-workflow.md)

## Grundprinzip

**Nichts entladen außer es muss Platz gemacht werden.**
Alles bleibt geladen bis die nächste Anforderung etwas anderes braucht.

## GPU-TTS Engines

Die vier GPU-Engines **XTTS**, **MOSS-TTS**, **Qwen3-TTS** und **Fish-Speech**
belegen VRAM (jeweils Docker-Container mit GPU).
Piper, Edge, eSpeak, DashScope brauchen kein VRAM.

## FreeEcho.2 — Fälle

Der FreeEcho.2 hat eine **eigene TTS-Engine** (konfiguriert im Plugin, unabhängig vom Browser).

Die FreeEcho.2-Pipeline ruft vor der Inferenz `ensure_tts_state(..., check_defer=True)`
auf. Sobald ein LLM geladen ist und eine andere GPU-TTS als die laufende gewünscht
wird, wird der Wechsel **verschoben**: Erst läuft die Inferenz auf dem geladenen LLM,
vor der Audio-Generierung folgt `force_tts_switch()` (Fälle 2 und 4).

Vor dem verschobenen Wechsel prüft `_gpu_tts_combo_fits()`, ob das aktive Modell eine
kalibrierte TTS-Variante für die gewünschte GPU-TTS hat. Wenn nicht, wird die
Sprachausgabe mit einem Fehler-Log übersprungen (ein Wechsel würde das LLM
verdrängen und der TTS trotzdem kein VRAM lassen).

### Fall 1: VRAM leer (nichts geladen)
1. TTS starten (z.B. XTTS)
2. LLM mit TTS-Profil laden (z.B. `GPT-OSS-120B-A5B-UD-Q8_K_XL-tts-xtts`)
3. Inferenz
4. Audio generieren
5. **Alles bleibt geladen**

### Fall 2: LLM geladen, keine TTS (Deferred Path)
1. Inferenz mit bestehendem LLM (schnell, kein Reload)
2. Alles entladen (VRAM freimachen)
3. TTS starten
4. llama-swap mit TTS-Profil neu starten (das LLM selbst lädt bei der nächsten Anfrage wieder)
5. Audio generieren
6. **Alles bleibt geladen**

Ausnahme: Läuft das LLM schon auf der passenden `-tts-<engine>`-Variante (z.B. weil
sich der TTS-Container per Idle-Watchdog selbst beendet hat), startet `_do_switch()`
den Container neben dem warmen LLM — kein Entladen, kein llama-swap-Neustart.

### Fall 3: LLM + richtige TTS schon geladen
1. Inferenz mit TTS-Profil (kein Reload nötig)
2. Audio generieren
3. **Alles bleibt geladen**

### Fall 4: LLM + falsche TTS geladen (z.B. MOSS statt XTTS) — ebenfalls verschoben
1. Inferenz mit bestehendem LLM (kein Reload)
2. Falsche TTS stoppen, alles entladen
3. Richtige TTS starten
4. LLM mit neuem TTS-Profil laden
5. Audio generieren
6. **Alles bleibt geladen**

### Fall 5: Nur TTS geladen, kein LLM
1. LLM mit TTS-Profil dazuladen
2. Inferenz
3. Audio generieren
4. **Alles bleibt geladen**

### Fall 6: Nur falsche TTS geladen, kein LLM
1. Alles entladen
2. Richtige TTS starten
3. LLM mit TTS-Profil laden
4. Inferenz
5. Audio generieren
6. **Alles bleibt geladen**

### Fall 7: LLM + GPU-TTS geladen, Plugin steht auf einer leichten Engine (Piper/Edge/eSpeak/DashScope)
1. Stopp des GPU-TTS-Containers startet im Hintergrund (außer eine aktive Pipeline
   hält ihn noch)
2. Inferenz mit bestehendem LLM
3. `force_tts_switch("")` wartet auf den Stopp, startet llama-swap mit dem Basis-Profil neu
4. Audio mit der leichten Engine generieren

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
- Eigene Engine-Einstellung im Plugin (Credential-Broker)
- Unabhängig vom Browser-Backend
- Änderung → nur Setting, kein sofortiger Wechsel
- Nächste FreeEcho.2-Anfrage nutzt die neuen Settings

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

### FreeEcho.2
SSOT: `_run_tts()` in `tts_reply.py`. Engine aus der Plugin-Einstellung
(`freeecho2`/`tts_engine`, Default `piper`).
1. User-Setting für Agent+Engine (`tts_agent_voices_per_engine[engine][agent]` in `settings.json`)
2. User-Setting für AIfred (nur wenn der Agent keins hat)
3. Engine-Default des Agenten aus `data/agents.json` (`tts_voices.<engine>`, via `get_tts_voice_default()`)
4. Engine-Default von AIfred aus `data/agents.json`, wenn der Default des Agenten keine Voice hat
5. `PUCK_TTS_FALLBACK_VOICE` (config.py), wenn der Default-Eintrag gar kein `voice`-Feld hat

## Debug-Ausgaben

Bei jedem LLM-Profil-Wechsel wird das effektive Modell + Kontext angezeigt
(`get_effective_model_info()`; FreeEcho.2 und das Browser-Dropdown stellen den
Statusmeldungen 🔊 voran):
```
🔊 LLM profile ready: GPT-OSS-120B-A5B-UD-Q8_K_XL-tts-xtts (ctx: 131.072)
```
Lief die TTS schon (`force_tts_switch()`): `🔊 LLM profile switched: …`.

Bei der Intent-Detection (`format_intent_result()` in `intent_detector.py`):
```
🎯 Intent: FAKTISCH, Addressee: –, Lang: DE
```

## Code-Einstiegspunkte

| Funktion | Datei | Beschreibung |
|----------|-------|-------------|
| `ensure_tts_state()` | `tts_engine_manager.py` | SSOT: Prüft/stellt VRAM-State her |
| `force_tts_switch()` | `tts_engine_manager.py` | Nach Deferred-Inferenz: TTS laden + Profil wechseln |
| `_do_switch()` | `tts_engine_manager.py` | Voller Engine-Wechsel (entladen → laden) |
| `set_tts_engine_or_off()` | `_tts_config_mixin.py` | Browser-Dropdown Handler |
| `_run_tts()` | `plugins/channels/freeecho2_channel/tts_reply.py` | FreeEcho.2 Audio-Generierung + Voice-Auflösung |
| `_ensure_tts_state()` / `_force_tts_switch()` | `plugins/channels/freeecho2_channel/tts_reply.py` | FreeEcho.2-Wrapper um die SSOT-Funktionen |
| `_queue_tts_for_agent()` | `_tts_streaming_mixin.py` | Browser TTS-Generierung |
| `_resolve_agent_tts()` | `_tts_streaming_mixin.py` | Browser-Auflösung von Voice/Speed/Pitch |
