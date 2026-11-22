# Browser Push Bus

> **English version:** [browser-push-bus.md](../../en/architecture/browser-push-bus.md)

Stand: 2026-11-18. Lebendes Dokument.

Der **Browser Push Bus** ist der Kanal, über den der Server die offenen Tabs einer Sitzung
aktualisiert, ohne dass sie fragen: Sprachausgabe, Musik, Statusleiste und die Meldung „diese
Liste hat sich geändert“. Er ist kein eigener Strom, sondern ein Aufruf auf dem **Ereignisstrom je
Sitzung**: Server-Seite [`browser_push()`](../../../aifred/lib/browser_push.py), Hub
[`session_events.py`](../../../aifred/lib/session_events.py), Browser-Seite
`followSession()` in [`frontend/src/api/events.ts`](../../../frontend/src/api/events.ts).

---

## Der Weg eines Ereignisses

```
irgendein Code ──browser_push(sid, kind, **felder)──▶ session_events.publish
(Zug, Hintergrund-Task,                                      │
 Planer, Kanal-Thread)                      Hub je Sitzung: ID-Zähler, Puffer, Warteschlange je Tab
                                                             │
                                           GET /api/sessions/{id}/events  (SSE)
                                                             │
                                           EventSource (followSession) ──▶ stores/chat.ts handle(kind, data)
                                                             │
                                           Store, lib/speech.ts, lib/media.ts, Statusleiste
```

Der Aufrufer muss in keinem Handler sitzen und keinen Zustand „dirty“ melden: `browser_push()` ruft
`session_events.publish` und fertig. Aus einem Thread mit eigener Schleife (Kanäle und Planer laufen
im Thread des Message Hubs) reicht der Aufruf trotzdem: der Hub gehört der Schleife des Servers
(`bind_home_loop` beim Start), `on_home_loop` reicht die Arbeit in der Reihenfolge der Aufrufe
dorthin, deshalb braucht er keine Sperre.

### Hub (`aifred/lib/session_events.py`)

| Baustein | Zweck |
|---|---|
| `publish(session_id, kind, data, replaces=…)` | Ereignis an jeden Abonnenten der Sitzung und in den Puffer; `replaces` nennt Arten, deren frühere Ereignisse dabei den Puffer verlassen |
| `subscribe(session_id, after_id)` | liefert die verpassten Ereignisse und die Warteschlange des Tabs; Eintragen und Puffer lesen geschehen ohne `await` dazwischen, jedes Ereignis landet in genau einem von beiden |
| `clear_replay(session_id, keep=…)` | Puffer leeren (die IDs steigen weiter); ein neuer Zug ruft es auf (`turn_service.py:297`) und behält nur die `LATEST_WINS_KINDS` |
| `missed_beyond_replay` | ob ein Client Ereignisse verpasst haben kann, die der Puffer nicht mehr hält |
| `sessions_with_subscribers` | die Sitzungen, die gerade ein Tab hört |

Jeder Tab hat seine eigene Warteschlange, zwei Tabs einer Sitzung sehen also beide jedes Ereignis.
Die ID steigt je Sitzung streng und fällt nie; der Browser merkt sich die letzte und sendet sie beim
Wiederverbinden (`Last-Event-ID`), der erste Aufruf übergibt sie als `?after=` (der `EventSource`
kann keine Kopfzeilen setzen). Beginnt die ID-Zählung nach einem Neustart des Servers neu oder wurde
der Puffer nach der letzten ID des Clients geleert, beginnt der Strom mit `resync`
(`aifred/lib/api/sessions.py`, `sessions_events`), und die Seite lädt die Sitzung neu
(`stores/chat.ts`, `resync()`). Alle 15 Sekunden ohne Ereignis kommt ein Keep-alive-Kommentar
(`SSE_KEEPALIVE_S`).

### Arten, die den Zustand tragen

Ein Ereignis, das einen Zustand statt eines Vorfalls meldet, ersetzt im Puffer seine Vorgänger: wer
sich neu verbindet, braucht den letzten Stand, nicht die Geschichte. Das sind `LATEST_WINS_KINDS`
(`browser_push.py`): `service_status`, `local_hold` und je Listenart `<name>_changed`. Auch `turn_phase`,
`tool_status` und `speech_regenerating` ersetzen sich über `replaces`.

---

## Was `browser_push()` verschickt

| Art | Felder | Wozu |
|---|---|---|
| `tts` | `url` | Audiodatei der Vorlesung |
| `tts_speak` | `utterance {text, language, voice, rate, pitch}` | Stück, das die eigene Stimme des Browsers spricht |
| `tts_pause` | `ms` | Stille zwischen zwei Stücken |
| `media` | `url, state_key, start_pos_sec, is_stream, audio_type, queue, pre_roll_sec, position_save_sec` | ein Titel mit gespeicherter Position; `queue` = die weiteren Titel des Ordners `{audio_url, state_key}`, `pre_roll_sec` = Rücksprung nach der Sprachausgabe, `position_save_sec` = Takt der Positionsspeicherung (beide `config.py`) |
| `stop` / `pause` / `resume` | – | Audio steuern |
| `seek` | `position_sec, relative` | Sprung im Titel |
| `speed` | `factor` | `playbackRate` (0,25 bis 4,0) |
| `service_status` | Schnappschuss (`ServiceSnapshot.as_json`) | Statusleiste (Modell, Bildbeschreiber, STT, TTS), siehe [service-status.md](service-status.md) |
| `local_hold` | `LocalHoldStatus` (Felder) | Halter der Karten dieses Rechners in der Statuszeile, vom Server bei jeder Änderung geschoben, siehe [service-status.md](service-status.md) |
| `<name>_changed` | – | die Liste `<name>` hat sich geändert (`notify_changed`) |

Die Seite liest sie in `stores/chat.ts` (`handle`), `lib/speech.ts` (`tts*`) und `lib/media.ts`
(`media`, `pause`, `resume`, `seek`, `speed`). Der Hub trägt außerdem die Ereignisse des Zuges
(`turn_started`, `token`, `message_added`, `turn_finished` …, aus `turn_service.py`), des Diktats,
der Vorlesung und des Message Hubs; die vollständige Liste, die der Browser hört, ist
`SESSION_EVENT_KINDS` in `events.ts`.

### Listen ändern sich: `notify_changed`

`notify_changed(resource)` (`browser_push.py`) sagt jeder verbundenen Seite, dass eine Liste neu zu
laden ist; `RESOURCES` sind `jobs`, `sessions`, `agents`, `memory`, `plugins` (die Gegenliste im
Browser: `RESOURCES` in `events.ts`). Aufrufer sind das Speichern einer Sitzung, der Papierkorb,
Agenten-Konfiguration, Agenten-Gedächtnis, der Planer und der Plugin-Manager. Die Seite
(`lib/resourceChanges.ts`) fasst eine Folge von Meldungen (eine Sitzung wird in einem Zug mehrfach
gespeichert) auf 300 ms zu einem Laden zusammen; der Store der Liste hängt sich mit
`onResourceChanged` ein. `notify_changed` darf aus jedem Thread kommen: die Liste der verbundenen
Sitzungen wird auf der Schleife des Servers gelesen.

### Debug-Konsole

Die Zeilen der Konsole gehen über denselben Strom als `debug`-Ereignis, sofort beim Aufruf von
`debug()` (`aifred/lib/debug_bus.py`). In die Sitzungsdatei kommen sie nur einmal, am Ende des
`session_scope` (die Datei wird als Ganzes umgeschrieben, manche sind hunderte KB groß). Eine Seite,
die mitten im Zug aufgeht, bekommt die noch wartenden Zeilen über `GET /api/sessions/{id}`
(`debug_bus.pending_lines`), den Rest aus der Datei.

---

## Wann den Bus nutzen

Alles, was der Server von sich aus an offene Tabs melden will und was nicht Teil der Antwort auf
eine Anfrage ist: Audio, Statusänderungen, „Liste hat sich geändert“. Die Antwort auf einen Aufruf
bleibt die HTTP-Antwort. Nachrichten für Listen tragen keine Daten, nur den Anlass; die Seite lädt
über die API neu, so bleibt die API die einzige Quelle.

---

## Checkliste: neue Art hinzufügen

1. **Server:** `browser_push(session_id, "<art>", feld=…)` aufrufen und die Art samt Feldern im
   Docstring von `browser_push()` nennen. Meldet die Art einen Zustand, in `LATEST_WINS_KINDS`
   aufnehmen; eine neue Listenart gehört in `RESOURCES` (dann entsteht `<name>_changed` von selbst).
2. **Browser:** die Art in `SESSION_EVENT_KINDS` (`events.ts`) eintragen, sonst hört der Browser sie
   nicht, und in `stores/chat.ts` (`handle`) einen `case` ergänzen. Eine neue Listenart auch in
   `RESOURCES` von `events.ts` und im Store der Liste `onResourceChanged` aufrufen.
3. Den Handler **idempotent** halten: beim Wiederverbinden kommen Ereignisse aus dem Puffer noch
   einmal.
4. Prüfen: `npm run build` und `npx vitest run` in `frontend/`, `ruff`, `mypy`.

## Stolperfallen

- **Idempotenz:** Der Server spielt beim Wiederverbinden alle Ereignisse mit `id > Last-Event-ID`
  erneut ab.
- **Nicht im Puffer:** Ein Client, der hinter der letzten Leerung des Puffers liegt, bekommt `resync`
  und lädt neu; ein Handler darf sich nicht darauf verlassen, dass jedes Ereignis ankommt.
- **Autoplay:** Der Browser erlaubt `audio.play()` erst nach einer Geste auf der Seite. `lib/speech.ts`
  spielt beim ersten Klick oder Tastendruck einmal einen stillen Ton (`unlock`); danach dürfen die
  Vorlesungen, die später über den Strom kommen, klingen.
