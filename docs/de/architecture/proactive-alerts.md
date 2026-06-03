# Proaktive Alert-Pipeline

Status: **in Umsetzung** (Paket 1 = Kern). Erster vertikaler Schnitt:
unbekanntes Gesicht an scharfer Kamera → Telegram mit Bild.

AIfred soll von sich aus Bescheid geben, wenn etwas Wichtiges passiert —
nicht nur auf Nachfrage. Statt eines vision-spezifischen „Notifiers" bauen
wir eine **generische, plugbare Pipeline**: Vision ist nur der erste
*Producer*, Telegram nur der erste *Sink*. Wenn der Kern agnostisch ist,
kostet jeder weitere Producer/Kanal fast nichts.

## Architektur in einem Satz

Beliebige **Producer** emittieren ein neutrales **`AlertEvent`** an einen
zentralen **Dispatcher**; eine **Regel-Engine** entscheidet (matchen,
drosseln), an welche **Sinks** (Channel-Plugins) es geht; die Sinks stellen
proaktiv zu. Der Kern kennt nichts Konkretes — Producer und Sinks docken an.

```
Producer (vision, system, scheduler, …)
        │  emit(AlertEvent)
        ▼
   Dispatcher  ──►  Regel-Engine  ──►  Drossel (dedup + cooldown + Ruhezeiten)
        │
        ▼
   Sinks (Channel-Plugins: telegram, discord, email, freeecho-voice, …)
```

## Der fixe Kern (`aifred/lib/alert_bus.py`)

Bleibt für immer klein und producer-/kanal-agnostisch.

- **`AlertEvent`** — neutrale Dataclass:
  - `producer` (z.B. `"vision"`), `category` (`"face_unknown"`),
    `source_id` (`"cam/office"`), `severity` (`"info" | "warning" | "critical"`),
  - `title`, `body` (Anzeigetext),
  - `dedup_key` (Drossel-Achse, s.u.),
  - `media` (optionaler Bild-Pfad/URL),
  - `timestamp`, `metadata`.
  - Keine producer-spezifischen Felder im Kern.
- **`AlertRule`** — `producer`, optionale Filter (`category`, `source_id`,
  `min_severity`), `sinks` (Liste Kanalnamen), `min_interval_sec`,
  optionale `quiet_hours`. Referenziert nur *registrierte* Producer/Sinks.
- **`AlertDispatcher`** — `emit(event)`: matchende Regeln finden → drosseln →
  pro Regel an eine `deliver`-Funktion übergeben (für Tests injizierbar,
  Default = `_default_deliver`). Throttle-State: `(rule, dedup_key) → letzter
  Versand`. Der Dispatcher-Kern (Matching + Drossel) bleibt rein und stellt
  selbst nichts zu — das macht die SSoT-Zustellung (s.u.).

Gating wie `armed` (scharf) ist **producer-spezifisch** und bleibt im
Producer (Vision emittiert nur, wenn scharf) — der Kern bleibt agnostisch.

## Plugbare Rollen

1. **Producer** (neu) — meldet sich mit Manifest an (`name`, `categories`,
   Quell-Enumeration) und `emit`-et. Die zentrale Regel-UI baut sich daraus
   selbst auf. Erster Producer: **Vision**.
2. **Sink** — kein neuer Sende-Weg: die Zustellung läuft über die **bestehende
   `send_reply`-Methode** der Channel-Plugins (SSoT), gekapselt in
   `message_processor.announce_to_channel(channel, recipient, text, media)`
   — Empfänger-Auflösung (user_mapping → Allowlist-Fallback) inklusive. Genau
   diese Funktion nutzt auch der **Scheduler** (vorher Duplikat in
   `_deliver_announce`, jetzt delegiert). `send_reply` kann nun ein `media`
   (Foto) mitsenden. Erster Sink: **Telegram** (Text + Foto).
3. **Action** (später) — nicht jede Reaktion ist „Nachricht": Webhook,
   Skript, Kamera scharf/unscharf. Channel-Send ist nur *eine* Action-Art.

**FreeEcho.2 (Puck) als Sink — implementiert.** Der Puck hält eine persistente
WebSocket (`_devices[room]`) und akzeptiert seit der Audio-Bus-Refactor-Phase
auch server-initiierte Push-Sequenzen. Wenn der Dispatcher
`announce_to_channel("freeecho2", recipient, text, media=…)` aufruft, läuft
das durch denselben SSoT-Pfad wie alle anderen Sinks (`send_reply` mit dummy
`InboundMessage(sender="system")`):

- **Recipient-Resolver** (`_resolve_channel_recipient`) löst leere
  `recipient`-Werte auf den ersten verbundenen Geräte-Room auf (FreeEcho.2
  hat keine Allowlist — die Hardware ist im LAN, kein Sender-Filter).
  Wer mehrere Pucks hat und gezielt zustellen will, übergibt
  `recipient="wohnzimmer"` explizit.
- **`send_reply`** erkennt den autonomen Aufruf an `sender == "system"`
  (oder `outbound.metadata.proactive=True`) und routet über den
  `AudioOrchestrator` mit dem zur Severity passenden Chime — entweder
  `play_alarm(with_tts=True, tts_pcm=…)` (auffälliger `alarm_wav`-Sound) oder
  `play_notification(with_tts=True, tts_pcm=…)` (sanfter
  `notification_wav`-Sound). Die Sequenz auf dem Wire:
  `audio_flag(alarm|notification, with_tts=True)` → `audio_flag(tts)` →
  `audio_start` → PCM-Chunks → `audio_end`. Der Puck spielt erst den
  lokalen Sound, puffert parallel den TTS-Stream und wechselt nahtlos auf
  die Sprache — kein „Spricht aus dem Nichts"-Effekt.
- **Sound-Wahl per metadata.audio_type** — `_default_deliver` mappt
  `ev.severity` auf das Tupel: `critical` → `"alarm"`, sonst →
  `"notification"`. Das Tupel reist via `announce_to_channel(..., metadata=
  {"audio_type": ..., "severity": ..., "category": ...})` durch und wird
  vom Channel ausgelesen. Andere Sinks (Telegram, Email, …) ignorieren das
  Feld stillschweigend. Schema-Drift (unbekanntes `audio_type`) fällt auf
  `"notification"` zurück, damit ein Caller-Bug nicht den Push verschluckt.
- **User-Wake-Reply** bleibt unverändert (kein Chime), weil `send_reply`
  dort `original.sender == "<room>"` sieht statt `"system"`.

Tests: `tests/test_freeecho2_proactive_push.py` — Recipient-Resolver,
send_reply-Routing (system-Sender vs. metadata.proactive vs. silent_reply),
audio_type-Mapping (alarm vs. notification vs. unknown-Fallback), und
alert_bus-Severity-Mapping. Live-Smoke-Test mit dem Vision-Producer → Puck
als Sink in der Alert-Regel-Datei (`data/alert_rules.json`).

## Zustellung & Modi (SSoT, ein Weg für Template + LLM)

`_default_deliver(ev, rule)` ist die eine Zustellung — beide Text-Wege teilen
sie:

1. **Text erzeugen** je `compose`-Modus (Regel-Feld `compose`, sonst
   `config.ALERT_COMPOSE_DEFAULT`):
   - `"template"` — fester Formatstring aus `AlertEvent` (deterministisch,
     kein LLM).
   - `"llm"` — `_compose_via_llm` baut eine synthetische `InboundMessage` und
     ruft **`process_inbound`** (AIfred formuliert; legt dabei selbst die
     Session an — wie der Scheduler).
2. **Browser-Session** (Kontroll-Trail): `record_autonomous_turn` schreibt den
   Turn über dieselben Primitive wie `process_inbound` (`routing_table` +
   `create_empty_session` + `update_chat_data` + `write_hub_notification`) →
   erscheint als **normale Session** im Browser. (Im LLM-Modus hat das schon
   `process_inbound` erledigt.)
3. **Kanäle**: `announce_to_channel` pro Sink der Regel (SSoT, s.o.).

Eine Browser-Session zählt selbst als Zustellung — Alerts sind also auch dann
sichtbar (im Browser), wenn ein Kanal mal nicht konfiguriert ist.

## Live-Clustering (Fundament, ersetzt Batch-only)

Live-Alerts feuern im Moment der Erkennung — **bevor** der Describe-Lauf
clustert. Damit der Alert-`dedup_key` „ein Alert pro Vorkommnis" leisten
kann (statt blinder Zeit-Cooldown), wird das Clustering **nach vorne
gezogen: live im Watcher**.

- Der Matching-Kern von `vision_cluster` wird zu einem **inkrementellen
  Clusterer** (zustandsbehaftet pro Quelle: `(timestamp, pHash) → cluster_id`,
  dieselbe deterministische ID-Logik).
- Der **Watcher** berechnet den pHash aus dem In-Memory-Frame (billiger als
  Disk-Reread im Batch) und schreibt den `cluster_id` direkt beim
  Event-Speichern.
- `vision_cluster` (Batch) bleibt als **Backfill** für Events ohne
  `cluster_id` (Altbestand / Watcher war aus) — selber Kern, SSoT.
- Der Describe-Lauf clustert nicht mehr neu, er beschreibt nur Repräsentanten.

Nebeneffekte: die „Frische-Lücke" in `vision_query_events` (frische Events
ohne `cluster_id`) verschwindet, und der Describe wird schlanker.

Restrisiko: Watcher-Neustart setzt offene Cluster zurück → ein Vorkommnis
über den Neustart wird gesplittet (selten, vertretbar).

## Throttling / dedup

- `dedup_key` für Vision = `cluster_id` (ein Alert pro Vorkommnis; zwei
  verschiedene Personen kurz nacheinander = zwei Cluster = zwei Alerts).
- Generisch: andere Producer liefern eigene Keys (Scheduler → Task-ID,
  System → Metrik-Name).
- Pro Regel zusätzlich `min_interval_sec` + optionale Ruhezeiten.

## Regel-Config

Zentral in `data/alert_rules.json` — eine JSON-Liste von Regel-Objekten.
**Fehlt die Datei → keine Regeln → keine Alerts** (sicherer Default; das
Feature aktiviert sich erst, wenn man die Datei anlegt). Unbekannte Keys
werden ignoriert (Schema darf wachsen). Beispiel:

```json
[
  {
    "rule_id": "vision-stranger",
    "producer": "vision",
    "category": "face_unknown",
    "source_id": null,
    "min_severity": "info",
    "sinks": ["telegram"],
    "min_interval_sec": 300,
    "quiet_hours": [22, 7]
  }
]
```

`category`/`source_id` = `null` heißt „alle". `sinks` sind Kanalnamen aus
dem `plugin_registry`. `min_interval_sec` ist der Cooldown pro
`(Regel, dedup_key)`; `quiet_hours` `[start, end]` (lokal, wraps über
Mitternacht). Geladen einmalig von `get_default_dispatcher()`.

## Künftige Producer (kein Vorbau, nur Andocken)

System-Health (GPU-Temp, Disk, Dienst-Crash), Scheduler/Erinnerungen,
Fertig-Meldungen langer Tasks (Deep-Research, Kalibration), Schwellwert-
Watchdogs, Kalender/EPIM. Jeder = „Manifest registrieren + `emit`en".

## Arbeitspakete

1. **Kern** — `AlertEvent`, `AlertRule`, `AlertDispatcher` (Matching +
   Drossel + Sink-Registry). Reine lib, unit-testbar ohne I/O.
2. **Live-Clustering** — inkrementeller Clusterer (SSoT), Watcher schreibt
   `cluster_id` bei Erkennung, Batch wird Backfill, Describe entschlackt.
3. **Telegram-Proaktiv-Send** — Sink-Fähigkeit „send an Ziel" (Ziel aus
   Plugin-Config), Text + Foto.
4. **Vision-Producer** — `emit` aus dem Watcher bei `face_unknown` (scharf),
   `dedup_key = cluster_id`.
5. **Zentrale Regel-Config** + Verdrahtung.
6. **Tests + Checks**; später Regel-UI, weitere Producer/Sinks.

## Status & offene Punkte

Umgesetzt: Kern (Paket 1), Live-Clustering (2), Telegram-Proaktiv-Send (3),
Vision-Producer + Regel-Config + Verdrahtung (4/5). Erster vertikaler
Schnitt steht: unbekanntes Gesicht an scharfer Kamera → Telegram mit Bild
(aktiviert sich, sobald `data/alert_rules.json` angelegt ist).

Offen / später:
- Producer-Registrierung formalisieren (Manifest-Plugin), sobald eine
  Regel-UI kommt — bis dahin emittiert der Vision-Producer direkt.
- Weitere Producer (System-Health, Scheduler) und Sinks (FreeEcho-Voice).
- Nachricht: aktuell Template (deterministisch); AIfred-formuliert optional.
- Live-Verifikation nötig: Watcher-Restart (Live-Clustering greift) und ein
  echter Telegram-Versand mit gesetzter `data/alert_rules.json`.
