# Sprachausgabe — Eskalationsliste, Einheit, Ansagen

> **English version:** [tts-escalation.md](../../en/architecture/tts-escalation.md)

Stand: 2026-10-10. Beschreibt, wie AIfred entscheidet, wer spricht, und wie Sprache auf die
Ausgabewege (Browser, FreeEcho.2) kommt. Code: [`aifred/lib/tts_escalation.py`](../../../aifred/lib/tts_escalation.py),
[`aifred/lib/tts_engines/`](../../../aifred/lib/tts_engines/),
[`aifred/lib/speech_synthesis.py`](../../../aifred/lib/speech_synthesis.py),
[`aifred/lib/api/tts.py`](../../../aifred/lib/api/tts.py),
[`aifred/lib/api/announce.py`](../../../aifred/lib/api/announce.py).

Verwandt: VRAM-Planung und Menü in [tts-vram-workflow.md](tts-vram-workflow.md), Aufbau einer
Engine in [tts-container-conventions.md](tts-container-conventions.md), die Cloud-Engine in
[dashscope-voice-cloning.md](dashscope-voice-cloning.md).

---

## Eskalationsliste

Welche Engine auf welchem Rechner spricht, steht in **einer globalen Liste** — keine feste
Engine-Wahl mehr. Browser, Kanäle mit eigenem Lautsprecher (FreeEcho.2) und Narrator lesen
dieselbe Liste. Sie liegt in `settings.json`:

| Schlüssel | Inhalt |
|-----------|--------|
| `tts_escalation` | geordnete Einträge `{"engine", "host", "enabled"}`; `host: null` = dieser Rechner bzw. Cloud |
| `tts_hosts` | andere Rechner mit TTS-Containern: `name`, `address`, `enabled`, `ssh` (`user@host:port`, leer = AIfred steuert nichts), optional `ports` je Engine |

**Reihenfolge:** von oben nach unten, der erste Eintrag, der gerade sprechen kann, spricht
(`SpeechRun.entry()`). **An/aus:** `enabled` je Eintrag; ein ausgeschalteter Eintrag wird
übersprungen. Ein Host lässt sich als Ganzes ausschalten (`tts_hosts[].enabled`): seine Einträge
werden übersprungen und seine Container gestoppt. Reihenfolge, An/Aus, Hosts und Einträge
ändert man im Sprachausgabe-Menü (`_edit_tts_lists()` in `_tts_config_mixin.py`). Eine kaputte
Liste (unbekannte Engine, unbekannter Host) löst `ValueError` aus und wird nicht still
übergangen.

### Standardliste

Die Liste einer frischen Installation leitet sich aus den Engines ab (`in_default_escalation`,
sortiert nach `display_order`; `config.default_tts_escalation()`):

| Reihenfolge | Engine (Key) | `display_order` |
|-------------|--------------|-----------------|
| 1 | Qwen3-TTS (`qwen3local`) | 10 |
| 2 | XTTS (`xtts`) | 20 |
| 3 | DashScope Qwen-Audio 3 (`dashscope_audio3`) | 50 |
| 4 | Piper (`piper`) | 60 |
| 5 | Edge (`edge`) | 80 |
| 6 | Browser (`browser`) | 90 |

Fish-Speech (30), MOSS (40) und eSpeak (70) gibt es als Engines, sie stehen aber nicht in der
Standardliste.

### Wann ein Eintrag spricht

`_skip_reason()` prüft in dieser Reihenfolge; die erste zutreffende Bedingung überspringt den
Eintrag (Debug-Zeile `TTS escalation: skip <eintrag> (<grund>)`):

1. Eintrag ausgeschaltet.
2. Browser-Eintrag, aber die Antwort geht nicht in eine Browser-Sitzung.
3. Lokale Engine, deren Docker-Image nicht gebaut ist.
4. Host des Eintrags ausgeschaltet.
5. Engine läuft (`is_running()`) → spricht.
6. Remote-Container schläft: mit SSH-Ziel am Host startet AIfred ihn
   (`scripts/tts-host-ctl.sh start <service>`) und wartet bis `startup_timeout_s`; ohne SSH-Ziel
   fällt der Eintrag aus.
7. Lokale Engine ohne GPU: „nicht verfügbar“ (z. B. Cloud ohne API-Key).
8. Lokale GPU-Engine: Start auf einer passenden Karte (nächster Abschnitt).

**Das Hauptmodell wird für TTS nie neu geladen.** Passt die lokale TTS nicht, geht es still zum
nächsten Eintrag.

### Platzierung auf den Karten

`place_local_gpu_engine()` (eine Regel für Liste und Start-API): Der gemessene Burn-in-Spitzenbedarf
der Engine (`tts_vram_cache`) plus `LLAMACPP_TTS_BURNIN_HEADROOM_MB` (512 MB) muss in den
**freien** Speicher einer Karte passen. Bevorzugt wird die Sammelkarte (`pick_tts_gpu()`), sonst
die passende Karte mit dem meisten freien Speicher. Ohne gemessenen Wert, ohne GPU-Liste oder
wenn keine Karte passt, lehnt die Platzierung ab (`PlacementRefused`) und der Eintrag fällt
aus. Lokale GPU-Starts laufen nacheinander (ein Sperrobjekt), damit ein zweiter Start den
Speicher sieht, den der erste gerade belegt. Braucht später ein Hauptmodell die Karte, räumt der
GPU-Wächter der Backends den Container ab; ausgenommen ist die Engine, für die ein
`-tts-<engine>`-Profil auf der Sammelkarte Platz hält (`planned_tts_engine()`).

### Ausfall und Stimmwechsel

Jede Engine wirft bei einem Fehler `TTSFailure` mit Grund (`unreachable`, `engine`, `software`).
`SpeechRun` (eine Antwort oder Ansage):

- Ausfall **vor dem ersten Ton**: still zum nächsten Eintrag (nur Debug-Zeile).
- Ausfall **mitten im Text**: der Nachfolger sagt zuerst den Stimmwechsel samt Grund an
  (`tts_switch_*`) und spricht dann den unfertigen Satz.
- Ein ausgefallener Eintrag bleibt für den Rest des Laufs aus.
- Hat keiner mehr eine Stimme für den Agenten (`resolve_voice()`), fällt der Eintrag aus.
- Ist die Liste erschöpft: `NoSpeechAvailable`; ein Kanal mit Lautsprecher bleibt dann stumm
  (laut geloggt).

Gleichzeitige Synthesen je Engine und Rechner sind auf `max_parallel_requests` begrenzt.

### Status der Einträge

`entry_status()` liefert für die Statusspalte des Listen-Editors einen Schlüssel
(`tts_status_<schlüssel>` in `lib/i18n`), ohne etwas zu starten: `disabled`, `host_off`,
`no_image`, `starting`, `running` (Container läuft), `cloud` (Cloud bereit), `ready`
(Prozess ohne Port bereit), `start_failed`, `no_access` (Cloud ohne Zugang), `unreachable`
(Host ohne SSH-Ziel antwortet nicht), `sleeping` (startet bei Bedarf).

---

## Einheit der Sprachausgabe

Wie viel Text auf einmal an die Engine geht, ist **eine Einstellung pro TTS-Engine**, systemweit
für alle Agenten und Kanäle:

| Einheit | Bedeutung |
|---------|-----------|
| `sentence` | satzweise — früheste erste Sprache; weitere Sätze entstehen währenddessen |
| `paragraph` | absatzweise (an Leerzeilen) — oft bessere Betonung |
| `whole` | alles am Stück — beste Qualität, die erste Sprache kommt erst nach der kompletten Erzeugung |

- Gespeichert in `tts_toggles_per_engine[engine]["unit"]` (Sprachausgabe-Menü); Standard je
  Engine ist `default_speech_unit` (`sentence`, bei MOSS, Piper und eSpeak `whole`).
- **SSOT zum Lesen:** `speech_unit_for(engine_key)` in
  [`tts_engines/registry.py`](../../../aifred/lib/tts_engines/registry.py) — Browser und jeder
  Kanal rufen sie auf; kein Kanal kennt eine eigene Einstellung. Ein ungültiger Wert löst
  `ValueError` aus.
- **Zerlegung:** `build_speech_segments()` in `audio_processing.py` macht aus den Absätzen einer
  Ansage Texte und Stille (`pause_ms`) je nach Einheit; die Satzaufteilung selbst ist
  `split_text_for_streaming_tts()`.
- **Browser-Streaming** ist kein eigener Schalter: `tts_speech_unit` ist die Einheit des obersten
  aktiven Eintrags (der tatsächlich sprechende steht erst beim ersten Satz fest); „Streaming“
  heißt jede Einheit außer `whole`.

---

## Browser-Sprachausgabe-Engine

`browser` (Web Speech API, `speechSynthesis`) ist eine Engine der Liste, die **keine Audiodatei**
erzeugt: `SpeechRun.speak_in_browser()` liefert statt einer URL einen `BrowserUtterance`
(Text, Sprache, Stimme, Tempo, Tonhöhe), den die Seite mit einer Stimme des Geräts spricht.

- Sie spricht nur bei Antworten in eine offene Browser-Sitzung (`in_browser=True`); für Echo,
  Narrator und Neu-Synthese wird sie übersprungen.
- Sie steht in der Standardliste zuletzt (`display_order` 90): der Server kann nicht wissen, ob das
  Gerät eine Stimme hat, danach könnte also keine Engine mehr übernehmen.
- Stimme `Auto` = das Gerät wählt eine für die Sprache (`default_voice`); Tempo und Tonhöhe
  nimmt `speechSynthesis` selbst, kein ffmpeg.
- Ortsangabe in der Liste: „Gerät“ (`location_label()`).

---

## TTS-Start-API und Steuerseite

[`aifred/lib/api/tts.py`](../../../aifred/lib/api/tts.py) macht die Liste für Aufrufer ohne LLM
nutzbar — gedacht für die Dienst-Steuerseite, die die Liste zeigt und Container startet oder
stoppt. Die Karten- und Host-Regeln sind dieselben wie bei der Sprache von AIfred selbst (derselbe
Startweg `launch_entry()`).

Auth: `Authorization: Bearer <TTS_CONTROL_API_TOKEN>`; ohne gültiges Token wird abgelehnt
(`require_service_token`). Der Aufrufername (`caller`) ist Pflicht und dient nur der Anzeige.

| Endpunkt | Wirkung |
|----------|---------|
| `GET /tts/entries?lang=de\|en` | Die Liste in Sprechreihenfolge mit Live-Status: `engine`, `host`, `label` („Engine · Ort“), `enabled`, `status`, `status_text`, `controllable`, `container` (lokaler Containername), `detail` (Grund eines fehlgeschlagenen Starts) |
| `POST /tts/start` `{engine, host, caller}` | Startet den Container im Hintergrund (Modell-Laden dauert bis zu einer Minute und mehr); `/tts/entries` zeigt `starting`, dann `running` oder `start_failed`. Lokal auf der von AIfred gewählten Karte, auf einem Host per SSH. 404 = kein solcher Eintrag, 409 = nicht steuerbar oder Start nicht möglich |
| `POST /tts/stop` `{engine, host, caller}` | Stoppt den Container, VRAM frei. 404/409 wie oben |

`host: null` = dieser Rechner. „Steuerbar“ (`is_controllable()`) sind lokale GPU-Container und
Container auf einem Host mit SSH-Ziel. Läuft ein Start des Eintrags schon, meldet `start`
„already starting“.

---

## Ansage ohne LLM: `POST /api/audio/announce`

[`aifred/lib/api/announce.py`](../../../aifred/lib/api/announce.py) liest einen fertigen Text auf
einem FreeEcho.2-Raum vor (Aufrufer z. B. Agent-Orc). Derselbe Zustellweg wie die proaktiven
Alarme: `announce_to_channel` → TTS des Echo-Plugins → Alarm-Warteschlange; Ansagen eines Raums
laufen nacheinander und eine laufende Ansage wird nicht unterbrochen.

- **Auth:** `Authorization: Bearer <ANNOUNCE_API_TOKEN>`, ohne Token wird abgelehnt.
- **Body:** `{"room", "text"}` **oder** `{"room", "texts": [...], "pause_ms"}` (genau eines von
  `text`/`texts`), dazu `speaker` (Pflicht, Name des Aufrufers, nur Anzeige — er steht in der
  Bubble-Kopfzeile). `room` = Raumname, `@gruppe` oder `*`. `texts` = mehrere Absätze als **eine**
  Ansage; `pause_ms` = Stille zwischen den Absätzen (Standard `ANNOUNCE_PAUSE_MS` = 1.000 ms,
  höchstens `ANNOUNCE_MAX_PAUSE_MS` = 5.000 ms).
- **Grenzen:** 413 bei mehr als `ANNOUNCE_MAX_CHARS` (1.200) Zeichen je Eintrag oder
  `ANNOUNCE_MAX_TOTAL_CHARS` (4.000) insgesamt — kein stilles Kürzen; 404 bei unbekanntem oder
  nicht verbundenem Raum; 502, wenn die Ansage nicht zugestellt werden konnte.
- **Räume:** `GET /api/audio/announce/rooms` liefert die gerade verbundenen Räume.
- **Töne:** Der Chime ist fest `notification`, nie `alarm`. Ob vor der Ansage ein Beginn-Ton und
  danach ein Ende-Ton angefordert wird, ist eine Plugin-Einstellung
  (`FREEECHO2_NOTIFICATION_START_TONE` / `_END_TONE`), nicht Sache des Aufrufers; welcher Ton es
  ist, legt der Puck fest. Läuft Musik, ein Hörbuch oder Sprache, hält der Server sie vor der
  Ansage am Puck an.
- Welche Engine spricht, bestimmt die Eskalationsliste; die Einheit ist `speech_unit_for()` der
  sprechenden Engine.

### `announce_into_rooms`

`announce_into_rooms(channel, rooms, text, speaker, metadata)` in `message_processor.py` ist die
**eine Stelle** für eine laut gemachte Ansage (der Agent-Tool `freeecho2_announce` und die
Announce-API nutzen sie). Je Raum:

1. zuerst die Bubble: `record_autonomous_turn` legt in der Sitzung des Raums eine Assistenten-Runde
   mit `speaker` als Absender an,
2. dann die Ansage in diese Sitzung (`announce_to_channel`), damit das Gesprochene an dieser Bubble
   hängen bleibt. In umgekehrter Reihenfolge würde eine kurze Ansage gesprochen, bevor ihre
   Bubble existiert.

Rückgabe: die erreichten Räume. Ein nicht erreichter Raum behält seine ungesprochene Bubble;
das wird geloggt, nicht versteckt.

---

## Sprache des Echos und die Bubble

[`speech_synthesis.py`](../../../aifred/lib/speech_synthesis.py) ist die SSOT der Spracherzeugung
für Kanäle mit eigenem Lautsprecher: Text → 48-kHz-PCM (mono, 16 Bit) in einem wachsenden
`TTSBuffer`. Der Kanal liefert nur noch das PCM an sein Gerät.

- `start_speech_stream()` erzeugt den **ersten Satz** und gibt den Puffer zurück; die übrigen
  Segmente (Texte und Stille in ms) erzeugt ein Hintergrund-Task und hängt sie an, während das
  Gerät schon abspielt. Vorher bereinigt `clean_text_for_tts()` den Text (Markdown, Emojis,
  Code, Links) — dieselbe Funktion wie im Browser-Chat.
- Welche Engine spricht, entscheidet die Eskalationsliste (`SpeechRun`); die **Sprache** gibt der
  Aufrufer (`run.language`); die **Feinheit** der Zerlegung ist die Einheit der Engine
  (`speech_unit_for`). Fällt eine Engine mitten im Strom aus, übernimmt der nächste Eintrag;
  sind alle erschöpft, endet der Strom ohne Ende-Ton (laut geloggt).
- **Bubble:** `keep_spoken_audio()` fügt, sobald alles erzeugt ist (auch bei einem abgebrochenen
  Strom: was bis dahin gesprochen wurde), die Satz-Audiodateien zum Audio der Sitzung zusammen
  und hängt es mit dem Vermerk „wer gesprochen hat“ (`SpeechRun.note(run.language)`, bei einem
  Wechsel mit Grund in der Sprache des Laufs) an die Antwort-Bubble — wie bei einer
  Browser-Antwort. Ohne Sitzung (eine Tool-Ansage ohne eigene Bubble) wird nichts gespeichert.
