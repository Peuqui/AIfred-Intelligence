# Browser Push Bus

Stand: 2026-05-22. Lebendes Dokument.

Der **Browser Push Bus** ist der reflex-unabhängige Kanal, über den der
Server den Browser aktualisiert, **ohne** den Weg über Reflex-State-Deltas
zu nehmen. Server-Seite: [`browser_push()`](../../../aifred/lib/api/browser_bus.py) in
`aifred/lib/api/browser_bus.py`. Browser-Seite: `browserEventSource` /
`startBrowserStream()` in [`assets/custom.js`](../../../assets/custom.js).

---

## Das Problem

Reflex pusht eine State-Änderung nur dann als Delta in den Browser, wenn
sie **innerhalb eines Event-Handlers** passiert (`@rx.event`, oder ein
`@rx.event(background=True)` mit `async with self:`).

Ein nackter `asyncio.create_task(...)` läuft **außerhalb** dieses
Systems. Er darf den Server-State mutieren — aber Reflex bemerkt es nicht
und schickt nichts an den Browser. Die Änderung wird erst sichtbar, wenn
irgendein *anderer* Event-Handler später `yield`t.

Das betrifft alles, was AIfred bewusst als fire-and-forget-Task aus dem
Event-Handler auslagert, damit der Handler (und der User) nicht warten
muss:

- **Streaming-TTS-Finalize** — kombiniert die Satz-Chunks zur Replay-WAV
  (`_finalize_streaming_tts_in_background`).
- **Session-Titel-Generierung** — ein Reasoning-Modell braucht dafür
  >100 s; inline würde das den Handler **und den State-Lock** so lange
  blockieren (`_generate_session_title`).

Beide patchen Server-State, der den Browser erreichen muss — die Bubble-
Audio-URL, den Sidebar-Titel.

---

## Die Lösung

Statt auf einen Reflex-Delta zu hoffen, **announced** der Background-Task
sein Ergebnis über eine bereits offene SSE-Verbindung. Der Browser
(`custom.js`) konsumiert das Event und schreibt das DOM direkt.

```
Background-Task ──browser_push(kind,…)──▶ _browser_event_storage + SSE-Queue
                                                       │
                                          GET /api/browser/stream/{sid}
                                                       │
                                          browserEventSource.onmessage
                                                       │
                                          switch(kind) ──▶ DOM-Patch
```

Ein einziger SSE-Stream pro Session trägt **alle** Kinds — Audio
(`tts`, `media`, …) und Nicht-Audio (`session_title`). Ein zweiter
EventSource wäre eine zweite langlebige HTTP-Verbindung pro Tab; das
sparen wir uns.

### Server-Seite (`aifred/lib/api/browser_bus.py`)

| Baustein | Zweck |
|---|---|
| `browser_push(session_id, kind, url, **meta)` | Event in Storage + SSE-Queue legen |
| `_browser_event_storage` | Pro Session: `{queue, version, playback_rate}` |
| `_browser_sse_queues` | Pro Session: `asyncio.Queue` für den offenen SSE-Listener |
| `GET /api/browser/stream/{session_id}` | SSE-Endpoint, reconnect-sicher über `Last-Event-ID` |
| `GET /api/browser/queue/{session_id}` | Polling-Fallback, falls SSE nicht verfügbar |
| `browser_queue_clear(session_id)` | Queue leeren (Versions-Zähler bleibt!) |

Die **Version** ist pro Session streng monoton — sie sinkt nie, auch nicht
nach `browser_queue_clear`. Der Client dedupliziert SSE-Events über die
Version; ein Reset würde das nächste `v1` wie ein schon gesehenes Item
aussehen lassen.

### Browser-Seite (`assets/custom.js`)

`startBrowserStream(sessionId)` öffnet den `EventSource`. Wichtig: der
Aufruf passiert **in der User-Geste-Kette** (Login, Send-Button,
Session-Wechsel) — dadurch gilt jedes spätere `audio.play()`, das ein
SSE-Event auslöst, als user-initiiert (kein Autoplay-Block).

`browserEventSource.onmessage` routet per `data.kind` auf den passenden
DOM-Patch.

---

## Wann den Bus nutzen — und wann nicht

```
Läuft der Code in einem Reflex-Event-Handler (@rx.event)?
├─ JA  → normaler State-Zugriff, Reflex pusht den Delta. KEIN Bus nötig.
└─ NEIN (nackter create_task / Background-Worker)
   │
   Patcht der Code ein einzelnes, klar adressierbares DOM-Element?
   ├─ JA  → Browser Push Bus: browser_push(...) + kind-Handler in custom.js.
   └─ NEIN (es geht um eine Liste / vielen State)
      → Reflex-State mutieren und darauf vertrauen, dass ein Event ihn
        pusht. Hängt der State an einer rx.foreach-Liste, die ein
        Background-Task füllt: den 500ms-Timer refresh_debug_console die
        Liste dirty-flaggen lassen (siehe Stolperfallen).
```

**Faustregel:** Der Bus ist gut, wenn custom.js *ein* Element gezielt
patchen kann (eine Audio-URL, ein Titel-Text). Für ganze Listen ist der
Reflex-`rx.foreach`-Pfad richtig — der Bus müsste sonst die React-
Reconciliation umgehen, was fragil wird (siehe Stolperfallen, Debug-
Konsole).

---

## Checkliste: neuen `kind` hinzufügen

1. **Server — `aifred/lib/api/browser_bus.py`:**
   - Den `kind` im Header-Kommentar des Bus-Blocks und im Docstring von
     `browser_push()` dokumentieren.
   - Braucht der `kind` Metadaten über `url` hinaus? Dann in `browser_push()`
     einen `elif kind == "…":`-Zweig ergänzen, der `item[...]` füllt
     (Vorbild: `media`, `seek`, `speed`). Sonst nichts — unbekannte Kinds
     bekommen sauber nur `{kind, url, version, playback_rate}`.

2. **Sender — der Background-Task:**
   - `from ..lib.api import browser_push` und
     `browser_push(session_id, kind="…", url=…)` aufrufen.
   - Den Server-State **trotzdem** mutieren (`chat_history`, Disk-Persistenz,
     …). Der Bus-Push überbrückt nur die Lücke bis zum nächsten regulären
     Reflex-Re-Render — danach muss der State konsistent sein, sonst macht
     der Re-Render den DOM-Patch wieder kaputt.

3. **Browser — `assets/custom.js`:**
   - In `browserEventSource.onmessage` einen `if (kind === '…') { … return; }`
     hinzufügen — **nach** dem Versions-Dedup-Block.
   - `ttsQueueVersion = data.version;` setzen (Dedup über alle Kinds gemeinsam).
   - Den DOM-Patch idempotent halten: das Event kann beim SSE-Reconnect
     erneut ankommen (Replay), und der nächste Reflex-Re-Render überschreibt
     den Patch ohnehin.

4. **DOM-Anker:** Patcht der Handler ein von Reflex gerendertes Element,
   braucht er einen stabilen Selektor — z.B. ein `custom_attrs={"data-…": …}`
   an der Reflex-Komponente (Vorbild: `data-session-id` an der
   Session-Liste, `data-audio-urls` an den Bubble-Buttons).

5. Checks: `node --check assets/custom.js`, plus `py_compile`/`ruff`/`mypy`.

---

## Aktuelle Kinds

| Kind | Payload | Wozu |
|---|---|---|
| `tts` | `{url, playback_rate}` | TTS-Chunk, lückenlose Queue |
| `media` | `{url, state_key, start_pos_sec, is_stream, audio_type}` | Einzeltrack mit Positions-Persistenz |
| `stop` / `pause` / `resume` | — | Audio-Steuerung |
| `seek` | `{position_sec, relative}` | Sprung im Track |
| `speed` | `{factor}` | `audio.playbackRate` setzen |
| `bubble_audio` | `{url}` | Kombinierte Replay-URL auf die fertige Chat-Bubble |
| `session_title` | `{url}` (= Titeltext) | Generierten Titel in die Session-Liste |

---

## Stolperfallen

- **Idempotenz:** Beim SSE-Reconnect replay’t der Server alle Items mit
  `version > Last-Event-ID`. Jeder Handler muss ein zweites Ankommen
  desselben Events vertragen.
- **State-Konsistenz:** Der Bus ersetzt nicht die State-Mutation, er
  ergänzt sie. Wer nur pusht, aber den State nicht mutiert, verliert den
  DOM-Patch beim nächsten Re-Render.
- **User-Geste:** Audio-Kinds funktionieren nur, weil der EventSource in
  einer User-Geste geöffnet wurde. `startBrowserStream` deshalb nie aus
  einem Timer/Callback ohne Geste-Bezug erstmalig aufrufen.
- **Debug-Konsole — NICHT über den Bus:** Die Konsole rendert per
  `rx.foreach(debug_messages)`. Background-Tasks hängen Zeilen an
  `debug_messages` an, lösen aber keinen Reflex-Delta aus. Sie über den Bus
  zu pushen wurde verworfen: custom.js müsste DOM in einen
  `rx.foreach`-Container einfügen → React-Reconciliation-Konflikte
  (doppelte / falsch sortierte Knoten). Stattdessen flaggt der 500ms-Timer
  `refresh_debug_console` die Liste explizit dirty
  (`self.debug_messages = list(self.debug_messages)`) und `yield`t — so
  pusht Reflex die Background-Zeilen mit max. 500 ms Latenz. **Lehre:** Der
  Bus ist für einzelne, gezielt adressierbare DOM-Patches; eine ganze, von
  Reflex gerenderte Liste lässt man über den Reflex-Pfad laufen.
