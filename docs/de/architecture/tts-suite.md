# Sprachausgabe — TTS-Suite, Liste, Einheit, Ansagen

> **English version:** [tts-suite.md](../../en/architecture/tts-suite.md)

Stand: 12.10.2026. AIfred erzeugt Sprache nicht mehr selbst. Engines, Container, Kartenwahl,
das Ausweichen auf die nächste Engine und die Ansage eines Stimmwechsels gehören der
**TTS-Suite**, einem eigenen Dienst (Repository
[Peuqui/TTS-Suite](https://github.com/Peuqui/TTS-Suite), Vertrag in dessen `docs/INTERFACE.md`).
AIfred ist ihr Client: Er schickt Text und seine eigene Liste und bekommt das Audio als Strom von
Teilstücken zurück.

Code: [`tts_client.py`](../../../aifred/lib/tts_client.py) (HTTP + WebSocket),
[`tts_service.py`](../../../aifred/lib/tts_service.py) (Adapter: Katalog des Dienstes ↔ AIfreds Liste),
[`tts_voices.py`](../../../aifred/lib/tts_voices.py),
[`browser_reading.py`](../../../aifred/lib/browser_reading.py),
[`speech_synthesis.py`](../../../aifred/lib/speech_synthesis.py),
[`tts_llm_coordination.py`](../../../aifred/lib/tts_llm_coordination.py),
[`tts_stress_burnin.py`](../../../aifred/lib/tts_stress_burnin.py),
[`api/tts.py`](../../../aifred/lib/api/tts.py), [`api/announce.py`](../../../aifred/lib/api/announce.py).

Dazu: die Cloud-Engine in [dashscope-voice-cloning.md](dashscope-voice-cloning.md), die
VRAM-Planung in den [Kalibrierungs-Dokumenten](calibration-strategy.md).

---

## Der Dienst

- Läuft als Systemeinheit `tts-suite@<Benutzer>.service` auf `127.0.0.1:5090` (`TTS_SUITE_URL`).
  Läuft er nicht, startet `TTSClient.ensure_ready()` die Einheit (Polkit-Regel) und wartet auf
  `/health`.
- Katalog lesen, Sprechen und Freigeben brauchen kein Token; AIfred schickt keins. Die
  Konfiguration des Dienstes (Hosts, GPU-Regeln, Aussprache, Stimmen) wird in dessen eigener
  Weboberfläche bearbeitet.
- AIfred startet oder stoppt **nie** selbst einen Container. Platz auf den Karten macht
  [GPU-Orc](https://github.com/Peuqui/GPU-Orc) über die Freigabe-Schnittstelle des Dienstes
  (`POST /gpu/release`, siehe *VRAM*); alles andere — Start bei Bedarf, Entladen im Leerlauf —
  erledigt der Dienst selbst.
- Weitere Rechner mit Engines (z. B. Aragon) sind Hosts in der Konfiguration des **Dienstes**
  (`~/.config/tts-suite/config.json`), nicht in AIfreds Einstellungen. `tts_hosts` in der
  `settings.json` ist hinfällig.

## AIfreds Liste

Welche Engines in welcher Reihenfolge und in welcher Einheit sprechen, steht in **einer
globalen Liste** in der `settings.json`; Browser, Echo, Ansagen und der Narrator lesen sie:

| Schlüssel | Inhalt |
|-----------|--------|
| `tts_escalation` | geordnete Einträge `{"engine", "host", "enabled"}`; `host: null` = dieser Rechner oder Cloud |
| `tts_toggles_per_engine[engine]["unit"]` | Einheit der Engine (siehe unten); leer = Empfehlung der Engine |
| `tts_agent_voices_per_engine` | je Engine und Agent: `voice`, `speed`, `pitch` |
| `enable_tts`, `tts_autoplay`, `tts_agents` | Sprachausgabe an/aus, Auto-Play, je Agent an/aus |

AIfred **liest und schaltet** die Liste; er entfernt keinen Eintrag und definiert keine Engine.
Was es gibt, liefert der Katalog des Dienstes (`GET /engines`, kurz zwischengespeichert): jede
Engine dieses Rechners und jedes konfigurierten Hosts mit Status, Name, empfohlener Einheit,
`installed` (startbar) und dem belegten VRAM. Eine Katalogzeile, die die Liste noch nicht kennt,
wird ausgeschaltet angehängt. Der Editor (Einstellungen → TTS, Agent-Editor) zeigt Reihenfolge,
An/Aus und Einheit je Zeile mit dem Live-Status („läuft“, „schläft — startet bei Bedarf“, „Cloud
bereit“, „nicht erreichbar“, …).

Die Standardliste einer frischen Installation (`DEFAULT_TTS_ESCALATION`): Qwen3-TTS, XTTS,
DashScope Qwen-Audio 3, Piper, Edge, Browser.

## Was eine Lesung schickt und bekommt

Eine Lesung ist ein `POST /speak` (ganzer Text) oder eine WebSocket `/speak/stream` (Text in
Stücken, während das LLM schreibt). Die Anfrage trägt:

- `engines`: AIfreds aktive Einträge der Reihe nach, je `{engine, unit, host}` — **die Liste
  dieser Anfrage**; der Dienst entscheidet von oben nach unten, welcher Eintrag jetzt spricht.
- `voices`: `{engine: {voice, speed, pitch}}` des sprechenden Agenten je Engine
  (`voices_for()`; eine Engine ohne konfigurierte Stimme bleibt draußen, der Dienst nimmt dann
  seine eigene Vorgabe oder meldet es im Trace).
- `run`: eine Kennung je Antwort (`new_run_id()`), gleich für alle Stücke dieser Antwort. Der
  Dienst merkt sich je Kennung, welche Engines ausgefallen sind, ob ein Codeblock über zwei
  Stücke offen ist und ob ein Stimmwechsel schon angesagt wurde.
- `language`, `paragraph_pause_ms`.

Die Antwort ist ein geordneter Strom von **Segmenten** (Audio oder ein Keep-alive-/Pause-Marker)
und am Ende eine **Zusammenfassung**: der `trace` (je Eintrag `spoke` / `failed` / `skipped` mit
Grund) und `switch` (gesetzt, wenn ein Stimmwechsel angesagt wurde).

**Ausweichen und Stimmwechsel** sind Sache des Dienstes: Ein Eintrag, der vor dem ersten Ton
ausfällt, wird still übersprungen; fällt einer mitten im Text aus, folgt der nächste und sagt
zuerst den Wechsel mit Grund an („Stimmwechsel wegen Serverausfall, weiter mit XTTS“), genau
einmal, und spricht dann weiter. AIfred zeigt das Ergebnis: die Debug-Zeilen `TTS escalation:
<Eintrag> speaks / failed … / voice change announced` (in `aifred_debug.log` und der Konsole)
und den Hinweis an der Antwort-Blase („Qwen3-TTS · Aragon“, bei einem Wechsel mit Grund).

**Textbereinigung:** Markdown, Code, Tabellen, LaTeX, URLs, Emojis und Aussprache (`AIfred` →
„Alfred“) übernimmt der Dienst. AIfred filtert vor dem Senden nur, was zu seiner Oberfläche
gehört: `<think>`-, `<details>`-, `<vlm_output>`-, `<data>`-Blöcke, die Performance-Fußzeile,
Rundenlabels und Zeitangaben in Klammern (`tts_text_filter.py`).

## Einheit der Sprachausgabe

Wie viel Text auf einmal an die Engine geht, ist **eine Einstellung pro TTS-Engine**, systemweit:

| Einheit | Bedeutung |
|---------|-----------|
| `sentence` | satzweise — früheste Sprache; die nächsten Sätze entstehen währenddessen |
| `paragraph` | absatzweise — oft bessere Satzmelodie |
| `whole` | alles am Stück — beste Qualität, die erste Sprache kommt erst nach der kompletten Erzeugung |

- Die Wahl des Nutzers steht in `tts_toggles_per_engine[engine]["unit"]`; ohne Wahl gilt die
  Empfehlung des Dienstes (`unit_default` der Katalogzeile). **SSOT zum Lesen:**
  `speech_unit_for(engine_key)` in `tts_service.py`.
- Der **Dienst** zerlegt den Text nach der Einheit der sprechenden Engine (satzweise mit
  Vorausschau: der erste Satz geht sofort, ab dem zweiten wartet der Dienst auf den nächsten; sehr
  kurze Stücke gehen mit dem nächsten zusammen); AIfred zerlegt nichts.
- **Browser-Streaming** ist die Einheit des obersten aktiven Eintrags; „Streaming“ heißt jede
  Einheit außer `whole`.

## Sprechwege

- **Browser** (`browser_reading.py`): Eine `BrowserReading` je Antwort öffnet die WebSocket,
  füttert den Text, während das LLM schreibt, und schiebt jedes Segment über den Push-Bus (`tts`,
  `tts_pause`) zum Player der Seite. Die Lesung wird dort geöffnet, wo der Text zu fließen beginnt
  (`_stream_agent_to_history` je Agent, `_process_vision_request` für VL Direct); eine noch offene
  Lesung wird beendet, wenn eine neue beginnt. Danach werden die Segmente zum Audio der Sitzung
  zusammengefügt und mit dem Hinweis an die Blase gehängt (Wiedergabe, Export).
- **Browser-Stimme** (`browser`, Web Speech API): ein Eintrag der Liste, der keine Datei erzeugt.
  Der Dienst spricht nie mit ihm; die Einträge **über** ihm gehen an den Dienst, die eigene
  Stimme des Browsers spricht, wenn keiner von ihnen kann (oder er der oberste Eintrag ist). Ein
  Eintrag darunter wird nie erreicht. Dieser Weg braucht keinen Dienst: Ist er aus, spricht der
  Browser.
- **Echo / Ansagen / Wiedergabe** (`speech_synthesis.py`): Text → 48-kHz-PCM (mono, 16 Bit) in
  einem wachsenden Puffer, den das Gerät abspielt, während der Rest noch entsteht; das Audio bleibt
  wie bei einer Browser-Antwort an der Blase.
- **Narrator** (`narrate_file`): liest eine ganze Datei mit **einer** Engine von Anfang bis Ende
  (kein Stimmwechsel mitten im Buch), eine Kennung je Datei; die Dateien werden mit ffmpeg
  zusammengefügt.

## VRAM: Reserve, Freigabe, Burn-in

- **Wo eine Engine läuft, ist für AIfred egal.** Die Kalibrierung reserviert Platz: Das Profil
  `<Modell>-tts-<Engine>` von llama-swap/vLLM lässt für diese Engine Platz frei (gemessene
  Spitze plus Reserve), und der Dienst setzt die Engine selbst auf eine Karte mit Platz.
- **Plan:** Welches `-tts-<Engine>`-Profil lädt, wird allein aus der `settings.json` und der
  llama-swap-Konfiguration gelesen (`planned_tts_engine()`: der oberste aktive lokale Eintrag, für
  den das Modell ein kalibriertes Profil hat). Der Dienst wird nicht gefragt, ein Ausfall des
  Dienstes hält das Sprachmodell also nicht auf. Ein geänderter Plan wirkt beim nächsten Laden
  des Modells: Das Profil steckt im Modellnamen, llama-swap tauscht das laufende Modell selbst aus.
  AIfred stoppt dafür weder llama-swap noch einen TTS-Container.
- **Vor dem Kaltstart des Hauptmodells** (und vor Beschreiber und Kalibrierung) bittet
  AIfred GPU-Orc, die Karten freizuräumen; GPU-Orc ruft dafür `POST /gpu/release` des Dienstes.
  Hält eine Lesung die Engine, wartet GPU-Orc kurz (`busy_wait_s` in seiner Config) und erzwingt
  dann: die Lesung endet mit dem Fehler `reading_aborted` samt Grund, AIfred zeigt das an
  (`tts_reading_aborted`) und weicht auf keinen anderen Eintrag aus, auch nicht auf den Browser.
  Nach dem Laden startet die nächste Lesung eine Engine wieder auf einer Karte mit Platz
  (Qwen3 rund 18 s, XTTS rund 33 s bis zum ersten Ton).
- **Burn-in** (`tts_stress_burnin.py`): Die Kalibrierung lässt den Dienst einen Worst-Case-Text
  auf der Engine lesen und nimmt die Spitze des VRAMs, den der Katalog meldet; das Ergebnis liegt
  in `data/tts_vram_cache.json`. Danach bittet AIfred den Dienst direkt, den Container wieder
  herunterzufahren (`POST /gpu/release` ohne `force`).

## Wenn der Dienst nicht erreichbar ist

TTS-Wege scheitern **laut**: Eine Lesung meldet den Grund (Toast, Debug-Zeile); die Browser-Stimme
übernimmt, wenn ihr Eintrag eingeschaltet ist. Der Rest von AIfred läuft weiter: Die Planung des
LLM-Profils liest nur Einstellungen, der Einstellungs-Editor zeigt den Grund statt der Liste, die
Kalibrierung zeigt keine TTS-Spalte, die Vision-Prüfung meldet „Platzbedarf unbekannt“.
Der Client ist absichtlich streng: Felder, die er nicht kennt, sind ein `IncompatibleService`-
Fehler, der sie nennt (Dienst und Client wurden nicht zusammen aktualisiert).

## Lesende Listen-API und Steuerseite

`GET /tts/entries?lang=de|en` ([`api/tts.py`](../../../aifred/lib/api/tts.py)) liefert AIfreds
Liste in Sprechreihenfolge mit dem Live-Status jedes Eintrags (`engine`, `host`, `label`,
`enabled`, `status`, `status_text`, `detail`). Auth: `Authorization: Bearer
<TTS_CONTROL_API_TOKEN>`. Einen Start- oder Stopp-Endpunkt gibt es nicht: Engines startet der
Dienst bei Bedarf und per Hand in seiner eigenen Weboberfläche.

## Ansage ohne LLM: `POST /api/audio/announce`

[`aifred/lib/api/announce.py`](../../../aifred/lib/api/announce.py) liest einen fertigen Text in
einem FreeEcho.2-Raum vor (Aufrufer z. B. Agent-Orc). Derselbe Zustellweg wie die proaktiven
Alarme: `announce_to_channel` → TTS des Echo-Plugins → Alarm-Warteschlange; Ansagen eines Raums
laufen nacheinander, eine laufende Ansage wird nicht unterbrochen.

- **Auth:** `Authorization: Bearer <ANNOUNCE_API_TOKEN>`, ohne Token abgelehnt.
- **Body:** `{"room", "text"}` **oder** `{"room", "texts": [...], "pause_ms"}` (genau eines von
  `text`/`texts`), dazu `speaker` (Pflicht, Name des Aufrufers, nur Anzeige — er steht in der
  Bubble-Kopfzeile). `room` = Raumname, `@gruppe` oder `*`. `texts` = mehrere Absätze als **eine**
  Ansage; `pause_ms` = Stille zwischen den Absätzen (Standard `ANNOUNCE_PAUSE_MS` = 1.000 ms,
  höchstens `ANNOUNCE_MAX_PAUSE_MS` = 5.000 ms).
- **Grenzen:** 413 über `ANNOUNCE_MAX_CHARS` (1.200) Zeichen je Eintrag oder
  `ANNOUNCE_MAX_TOTAL_CHARS` (4.000) insgesamt — kein stilles Kürzen; 404 bei unbekanntem oder
  nicht verbundenem Raum; 502, wenn die Ansage nicht zugestellt werden konnte.
- **Räume:** `GET /api/audio/announce/rooms` liefert die gerade verbundenen Räume.
- **Töne:** Der Gong ist fest `notification`, nie `alarm`. Ob ein Beginn-Ton davor und ein
  Ende-Ton danach angefordert werden, ist eine Plugin-Einstellung
  (`FREEECHO2_NOTIFICATION_START_TONE` / `_END_TONE`), nicht Sache des Aufrufers. Läuft Musik, ein
  Hörbuch oder Sprache, hält der Server sie vor der Ansage am Puck an.
- Welche Engine spricht, entscheiden die Liste und der Dienst; die Einheit ist
  `speech_unit_for()` der sprechenden Engine.

### `announce_into_rooms`

`announce_into_rooms(channel, rooms, text, speaker, metadata)` in `message_processor.py` ist die
**eine Stelle** für eine laut gemachte Ansage (das Agenten-Werkzeug `freeecho2_announce` und die
Ansage-API nutzen sie). Je Raum: erst die Blase (`record_autonomous_turn` legt einen Assistenten-
Turn mit `speaker` als Absender an), dann die Ansage in diese Sitzung (`announce_to_channel`),
damit das Gesprochene an dieser Blase bleibt. Gibt die erreichten Räume zurück; ein nicht
erreichter Raum behält seine ungesprochene Blase, das wird geloggt, nicht versteckt.
