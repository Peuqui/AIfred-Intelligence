# TTS + VRAM Workflow — FreeEcho.2 & Browser

## Grundprinzip

**Nichts entladen außer es muss Platz gemacht werden.**
Alles bleibt geladen bis die nächste Anforderung etwas anderes braucht.

## GPU-TTS Engines

Nur **XTTS** und **MOSS-TTS** belegen VRAM (Docker-Container mit GPU).
Piper, Edge, eSpeak, DashScope brauchen kein VRAM.

## FreeEcho.2 — Fälle

Der FreeEcho.2 hat eine **eigene TTS-Engine** (konfiguriert im Plugin, unabhängig vom Browser).

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
4. Audio generieren
5. LLM mit TTS-Profil dazuladen
6. **Alles bleibt geladen**

### Fall 3: LLM + richtige TTS schon geladen
1. Inferenz mit TTS-Profil (kein Reload nötig)
2. Audio generieren
3. **Alles bleibt geladen**

### Fall 4: LLM + falsche TTS geladen (z.B. MOSS statt XTTS)
1. Alles entladen
2. Richtige TTS starten
3. LLM mit neuem TTS-Profil laden
4. Inferenz
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

## Browser — TTS Umschaltung

### Engine-Dropdown (Haupt-Einstellungen)
- Umschaltung erfolgt **sofort** (nicht nur Setting ändern)
- `set_tts_engine_or_off()` steuert: VRAM freimachen, neuen Container starten, LLM mit Profil neu laden
- "Aus" → `enable_tts=False`, GPU-Container stoppen

### Agent-Editor (pro Agent)
- Backend-Dropdown pro Agent: Wählt welches Backend für diesen Agenten gilt
- "Aus" → Agent bekommt kein TTS (`enabled=False`)
- Voice leer → Fallback auf AIfred's Voice des aktuellen Backends
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
1. Agent hat Voice konfiguriert → benutze die
2. Agent hat keine Voice → AIfred's Voice des aktuellen Backends
3. AIfred hat keine Voice → `self.tts_voice` (State-Default)

### FreeEcho.2
1. User-Setting für Agent+Engine (aus `settings.json`)
2. User-Setting für AIfred (Fallback)
3. `TTS_AGENT_VOICE_DEFAULTS[engine][agent]`
4. `TTS_AGENT_VOICE_DEFAULTS[engine]["aifred"]`
5. `PUCK_TTS_FALLBACK_VOICE` (config.py)

## Debug-Ausgaben

Bei jedem LLM-Profil-Wechsel wird das effektive Modell + Kontext angezeigt:
```
🔊 LLM restarted: GPT-OSS-120B-A5B-UD-Q8_K_XL-tts-xtts (ctx: 131.072)
```

Bei der Intent-Detection:
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
| `_run_tts()` | `freeecho2/__init__.py` | FreeEcho.2 Audio-Generierung |
| `_queue_tts_for_agent()` | `_tts_streaming_mixin.py` | Browser TTS-Generierung |
