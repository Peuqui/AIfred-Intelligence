# Proaktive Alert-Pipeline

> **English version:** [proactive-alerts.md](../../en/architecture/proactive-alerts.md)

Stand (2026-09-25): **umgesetzt** (Pakete 1–5). Vision ist der erste
Producer; Telegram (Text + Foto) und FreeEcho.2 (Chime + Sprache) sind
umgesetzte Sinks, und jedes weitere Channel-Plugin lässt sich als Sink wählen.
Die Regeln pflegst du in den Vigilantia-Einstellungen (Regel-UI).

AIfred soll von sich aus Bescheid geben, wenn etwas Wichtiges passiert —
nicht nur auf Nachfrage. Statt eines vision-spezifischen „Notifiers" bauen
wir eine **generische, plugbare Pipeline**: Vision ist nur der erste
*Producer*, Telegram nur der erste *Sink*. Wenn der Kern agnostisch ist,
kostet jeder weitere Producer/Kanal fast nichts.

## Architektur in einem Satz

Beliebige **Producer** emittieren ein neutrales **`AlertEvent`** an einen
zentralen **Dispatcher**; eine **Regel-Engine** entscheidet (matchen,
deduplizieren, Ruhezeiten), an welche **Sinks** (Channel-Plugins) es geht; die Sinks stellen
proaktiv zu. Der Kern kennt nichts Konkretes — Producer und Sinks docken an.

```
Producer (vision, system, scheduler, …)
        │  emit(AlertEvent)
        ▼
   Dispatcher  ──►  Regel-Engine  ──►  Drossel (Dedup pro Vorkommnis + Ruhezeiten)
        │
        ▼
   Sinks (Channel-Plugins: telegram, discord, email, freeecho2, …)
```

## Der fixe Kern (`aifred/lib/alert_bus.py`)

Bleibt für immer klein und producer-/kanal-agnostisch.

- **`AlertEvent`** — neutrale Dataclass:
  - `producer` (z.B. `"vision"`), `category` (`"face_unknown"`),
    `source_id` (`"cam/office"`), `severity` (`"info" | "warning" | "critical"`),
  - `title`, `body` (Anzeigetext),
  - `dedup_key` (Drossel-Achse, s.u.),
  - `media` (Haupt-Bildpfad; Subjekt-Ansicht für Einzelbild-Kanäle),
    `media_context` (optionaler zweiter Bildpfad: die Weitwinkel-Kontext-Ansicht
    desselben Moments), `media_gallery` (Bild-URLs für die Browser-Session),
  - `session_key` (optional: mehrere Quellen eines Geräts teilen sich eine
    Browser-Session), `session_title` (fester Titel der gerouteten Session),
  - `timestamp`, `metadata`.
  - Keine producer-spezifischen Felder im Kern.
- **`AlertRule`** — `producer`, `sinks` (Liste Kanalnamen, optional
  `"kanal:ziel"`), optionale Filter (`category`, `source_id`,
  `min_severity`), optionale `quiet_hours`, `rule_id`, `compose` (Text-Modus,
  s.u.). Filter sind UND-verknüpft, `None` = beliebig.
- **`AlertDispatcher`** — `emit(event)`: matchende Regeln finden → in Ruhezeiten
  überspringen → deduplizieren → pro Regel an eine `deliver`-Funktion übergeben
  (für Tests injizierbar, Default = `_default_deliver`). Throttle-State:
  `(rule, dedup_key) → letzter Versand`, eingetragen erst nach erfolgreicher
  Zustellung. Der Dispatcher-Kern (Matching + Drossel) bleibt rein und stellt
  selbst nichts zu — das macht die SSoT-Zustellung (s.u.).

Gating wie `armed` (scharf) ist **producer-spezifisch** und bleibt im
Producer (Vision emittiert nur, wenn scharf) — der Kern bleibt agnostisch.

## Plugbare Rollen

1. **Producer** — ruft `get_default_dispatcher().emit(...)`. Eine formale
   Producer-Registrierung (Manifest) gibt es noch nicht; der Vision-Producer
   (`aifred/lib/vision_alerts.py`) emittiert direkt.
2. **Sink** — kein neuer Sende-Weg: die Zustellung läuft über die **bestehende
   `send_reply`-Methode** der Channel-Plugins (SSoT), gekapselt in
   `message_processor.announce_to_channel(channel, recipient, text, *,
   session_id, media, metadata)` — Empfänger-Auflösung (user_mapping →
   erster gemappter User → Allowlist-Fallback) inklusive. Genau
   diese Funktion nutzt auch der **Scheduler** (`_deliver_announce` delegiert
   dorthin). `send_reply` sendet `media` (Foto) mit; Telegram schickt ein Album,
   wenn `metadata.media_context` gesetzt ist (Dual-Lens: Zoom + Weitwinkel).
   Ein Sink-Eintrag `"kanal:ziel"` wird von `resolve_announce_targets` in
   konkrete Empfänger expandiert.
3. **Action** (später) — nicht jede Reaktion ist „Nachricht": Webhook,
   Skript, Kamera scharf/unscharf. Channel-Send ist nur *eine* Action-Art.

**FreeEcho.2 (Puck) als Sink — implementiert.** Der Puck hält eine persistente
WebSocket (`_devices[room]`) und akzeptiert seit der Audio-Bus-Refactor-Phase
auch server-initiierte Push-Sequenzen. Wenn der Dispatcher
`announce_to_channel("freeecho2", recipient, text, …)` aufruft, läuft
das durch denselben SSoT-Pfad wie alle anderen Sinks (`send_reply` mit dummy
`InboundMessage(sender="system")`):

- **Ziel-Expansion** (`resolve_announce_targets`): Sink `"freeecho2"` oder
  `"freeecho2:*"` → alle gerade verbundenen Pucks, `"freeecho2:@gruppe"` → die
  verbundenen Rooms dieser Gruppe aus `data/freeecho2_groups.json`,
  `"freeecho2:<room>"` → dieser Room, aber nur wenn er verbunden ist (sonst kein
  Empfänger, kein Versand). FreeEcho.2 hat keine Allowlist — die Hardware ist
  im LAN, kein Sender-Filter. (`_resolve_channel_recipient` löst einen leeren
  Empfänger auf den ersten verbundenen Room auf — der Weg für direkte Aufrufe
  ohne Ziel-Expansion, etwa den Scheduler.)
- **`send_reply`** erkennt den autonomen Aufruf an `sender == "system"`
  (oder `outbound.metadata.proactive=True`) und legt Chime + TTS in die
  **Alert-Queue** des Rooms (`enqueue_alert`, `alert_queue.py`): ein Worker pro
  Room spielt die Einträge nacheinander über den `AudioOrchestrator` ab —
  entweder `play_alarm(with_tts=True, tts_pcm=…)` (auffälliger `alarm_wav`-Sound)
  oder `play_notification(with_tts=True, tts_pcm=…)` (sanfter
  `notification_wav`-Sound) — und wartet vor dem nächsten Eintrag auf das `_done`
  des Pucks (mit einem aus der Wiedergabedauer abgeleiteten Timeout). Der
  Emit-Pfad blockiert nicht. Die Sequenz auf dem Wire:
  `audio_flag(alarm|notification, with_tts=True)` → `audio_flag(tts)` →
  `audio_start` → PCM-Chunks → `audio_end`, danach `done`. Der Puck spielt erst den
  lokalen Sound, puffert parallel den TTS-Stream und wechselt nahtlos auf
  die Sprache — kein „Spricht aus dem Nichts"-Effekt.
- **Sound-Wahl per metadata.audio_type** — `_default_deliver` mappt
  `ev.severity`: `critical` und `warning` → `"alarm"`, sonst (`info`) →
  `"notification"`. Der Wert reist via `announce_to_channel(..., metadata=
  {"audio_type": ..., "severity": ..., "category": ...})` durch und wird
  vom Channel ausgelesen. Andere Sinks (Telegram, Email, …) ignorieren das
  Feld stillschweigend. Schema-Drift (unbekanntes `audio_type`) fällt auf
  `"notification"` zurück, damit ein Caller-Bug nicht den Push verschluckt.
- **User-Wake-Reply** bleibt unverändert (kein Chime), weil `send_reply`
  dort `original.sender == "<room>"` sieht statt `"system"`.

Tests: `tests/test_freeecho2_proactive_push.py` — Recipient-Resolver,
send_reply-Routing (system-Sender vs. metadata.proactive vs. silent_reply),
audio_type-Mapping (alarm vs. notification vs. unknown-Fallback) und
alert_bus-Severity-Mapping (critical/warning → alarm, info → notification);
`tests/test_freeecho2_alert_queue.py` — die Alert-Queue pro Room.

## Zustellung & Modi (SSoT, ein Weg für alle Text-Modi)

`_default_deliver(ev, rule)` ist die eine Zustellung — alle Text-Modi teilen
sie:

1. **Text erzeugen** je `compose`-Modus (Regel-Feld `compose`, sonst
   `config.ALERT_COMPOSE_DEFAULT`, derzeit `"template"`):
   - `"template"` — fester Formatstring aus `AlertEvent` (deterministisch,
     kein LLM).
   - `"llm"` — `_compose_via_llm` baut eine synthetische `InboundMessage` und
     ruft **`process_inbound`** (AIfred formuliert; legt dabei selbst die
     Session an — wie der Scheduler). Sieht nur Titel und Body, nicht das Bild.
   - `"vlm"` — `_describe_media_via_vlm` lässt das aktive VLM das Vorkommnis
     beschreiben (bei laufendem Burst die neuen Bilder als Kapitel, sonst die
     Bildserie des Clusters, sonst den Einzelmoment Zoom + Weitwinkel); die
     Beschreibung kommt in den Body.
   - `"vlm+llm"` — VLM-Beschreibung, dann formuliert AIfred damit als Kontext
     den finalen Text.

   Scheitert LLM oder VLM, bleibt der Template-Text. Eine VLM-Beschreibung
   wird in die Vision-Events zurückgeschrieben (`apply_description_to_events` /
   `apply_cluster_description`), der nächtliche bulk-describe überspringt sie.
2. **Browser-Session** (Kontroll-Trail): `record_autonomous_turn` schreibt den
   Turn über dieselben Primitive wie `process_inbound` (`routing_table` +
   `create_empty_session` + `update_chat_data` + `write_hub_notification`) →
   erscheint als **normale Session** im Browser, mit `media_gallery` als
   Bildern. (Im LLM-Modus hat das schon `process_inbound` erledigt.)
   `session_title` wird gesetzt, solange die Session keinen Titel hat.
3. **Kanäle**: `announce_to_channel` pro Sink (und pro expandiertem Empfänger)
   der Regel (SSoT, s.o.), mit der `session_id` des Alerts — eine Antwort auf
   eine Alarm-Mail landet so in dieser Session.

Eine Browser-Session zählt selbst als Zustellung — Alerts sind also auch dann
sichtbar (im Browser), wenn ein Kanal mal nicht konfiguriert ist.

## Live-Clustering (Fundament, ersetzt Batch-only)

Live-Alerts feuern im Moment der Erkennung — **bevor** der Describe-Lauf
clustert. Damit der Alert-`dedup_key` „ein Alert pro Vorkommnis" leisten
kann (statt blinder Zeit-Cooldown), läuft das Clustering **live im
Watcher**.

- Der Matching-Kern ist der **`IncrementalClusterer`** in `vision_cluster`
  (zustandsbehaftet pro Quelle, ein offener Cluster): Ein Event schließt sich
  an, solange die Lücke zum letzten Event ≤ `VISION_CLUSTER_GAP_SECONDS` ist und
  die Gesamtdauer ≤ `VISION_CLUSTER_MAX_SECONDS`, sonst beginnt ein neues
  Vorkommnis. Der pHash dient nur als ID-Suffix und Gültigkeitscheck
  (0 = unbrauchbar), nicht zum Splitten; die ID
  `{source-slug}-{start-ts}-{hash-prefix}` ist deterministisch.
- Der **Watcher** berechnet den pHash aus dem In-Memory-Frame
  (`_cluster_id_for`, billiger als Disk-Reread im Batch) und schreibt den
  `cluster_id` direkt beim Event-Speichern.
- `cluster_events` (Batch) bleibt als **Backfill** für Events ohne
  `cluster_id` (Altbestand / Watcher war aus) — selber Kern, SSoT.
- Der bulk-describe-Lauf clustert nur Events ohne `cluster_id` und
  beschreibt jedes Vorkommnis einmal als Keyframe-Sequenz.

Nebeneffekte: die „Frische-Lücke" in `vision_query_events` (frische Events
ohne `cluster_id`) verschwindet, und der Describe wird schlanker.

Restrisiko: Watcher-Neustart setzt offene Cluster zurück → ein Vorkommnis
über den Neustart wird gesplittet (selten, vertretbar).

## Throttling / dedup

- Ein nicht-leerer `dedup_key` kennzeichnet ein Vorkommnis: Jede Wiederholung
  desselben Keys wird unterdrückt, solange der Dispatcher ihn sich merkt
  (`ALERT_DEDUP_RETENTION_SEC`, 1800 s); ein neuer Key alarmiert sofort.
  Events ohne Key werden nie gedrosselt. Einen Zeit-Cooldown pro Regel gibt es
  nicht (mehr).
- Vision: `dedup_key` = `cluster_id` (ein Alert pro Vorkommnis; zwei
  verschiedene Personen kurz nacheinander = zwei Cluster = zwei Alerts),
  Fallback `source_id:category`. Die Burst-Bilanz (`BurstReport`, bei Quellen
  mit Face-Hunt-Burst) hängt einen Laufindex an (`…:burst-report-<n>`): Dort
  sind mehrere Meldungen pro Vorkommnis gewollt — eine Bilanz mit bestem Bild
  und allen Namen, danach Follow-ups nur bei echten Neuigkeiten (neuer Name,
  Band-Upgrade, mehr Personen).
- Andere Producer würden eigene Keys liefern (Scheduler → Task-ID,
  System → Metrik-Name).
- Pro Regel optionale `quiet_hours`. Zusätzlich filtert der Vision-Producer
  pro Kamera (Quellen-Settings): `alerts_enabled`, `alert_types`,
  `quiet_enabled` + `quiet_start`/`quiet_end`.

## Regel-Config

Zentral in `data/alert_rules.json` — eine JSON-Liste von Regel-Objekten.
**Fehlt die Datei → keine Regeln → keine Alerts** (sicherer Default; das
Feature aktiviert sich erst, wenn es Regeln gibt). Unbekannte Keys
werden ignoriert (Schema darf wachsen). Beispiel:

```json
[
  {
    "rule_id": "vision-face_unknown",
    "producer": "vision",
    "category": "face_unknown",
    "source_id": null,
    "min_severity": "info",
    "sinks": ["telegram", "freeecho2"],
    "compose": "vlm",
    "quiet_hours": [22, 7]
  }
]
```

`category`/`source_id` = `null` heißt „alle". `sinks` sind Kanalnamen aus
dem `plugin_registry`, optional mit Ziel (`"freeecho2:@unten"`).
`quiet_hours` `[start, end]` (lokal, wraps über Mitternacht). Geladen von
`get_default_dispatcher()`; `reload_rules()` baut den Dispatcher live neu auf
(der Throttle-State wird dabei zurückgesetzt).

**Regel-UI:** Die Vigilantia-Einstellungen (`_alert_rules_section` in
`aifred/ui/vision_settings.py`) zeigen eine Zeile pro Vision-Kategorie
(`person`, `vehicle`, `animal`, `face_known`, `face_unsure`, `face_unknown`),
je einen Schalter pro verfügbarem Kanal (entdeckt über
`plugin_registry.all_channels()`) und einen „Bild-Text"-Schalter
(`compose: "vlm"`). Änderungen schreiben `data/alert_rules.json`
(`rule_id` = `vision-<category>`) und rufen `reload_rules()` — wirksam ohne
Service-Neustart.

## Künftige Producer (kein Vorbau, nur Andocken)

System-Health (GPU-Temp, Disk, Dienst-Crash), Scheduler/Erinnerungen,
Fertig-Meldungen langer Tasks (Deep-Research, Kalibration), Schwellwert-
Watchdogs, Kalender/EPIM. Jeder = „ein `AlertEvent` `emit`en".

## Arbeitspakete

1. **Kern** — `AlertEvent`, `AlertRule`, `AlertDispatcher` (Matching +
   Drossel). Reine lib, unit-testbar ohne I/O. — erledigt
2. **Live-Clustering** — inkrementeller Clusterer (SSoT), Watcher schreibt
   `cluster_id` bei Erkennung, Batch wird Backfill, Describe entschlackt. — erledigt
3. **Telegram-Proaktiv-Send** — Sink-Fähigkeit „send an Ziel", Text + Foto. — erledigt
4. **Vision-Producer** — `emit` aus dem Watcher (scharf), `dedup_key = cluster_id`. — erledigt
5. **Zentrale Regel-Config** + Verdrahtung. — erledigt
6. **Tests + Checks** (`tests/test_alert_bus.py`, `tests/test_vision_alerts.py`,
   FreeEcho.2-Tests s.o.); Regel-UI — erledigt; weitere Producer/Sinks — offen.

## Status & offene Punkte

Umgesetzt: Kern (Paket 1), Live-Clustering (2), Telegram-Proaktiv-Send (3),
Vision-Producer + Regel-Config + Verdrahtung (4/5), Regel-UI, FreeEcho.2-Sink,
compose-Modi `template`/`llm`/`vlm`/`vlm+llm`. Der Vision-Producer meldet
Gesichter (`face_known`, `face_unsure`, `face_unknown`), Personen (`person`)
und Edge-AI-Objekte (`vehicle`, `animal`) scharfer Kameras; bei Quellen mit
Face-Hunt-Burst gehen Gesichts- und Personen-Erkennungen als Burst-Bilanz
raus, sonst als Sofort-Alert.

Offen / später:
- Producer-Registrierung formalisieren (Manifest) — die Regel-UI kennt derzeit
  nur die festen Vision-Kategorien; der Vision-Producer emittiert direkt.
- Weitere Producer (System-Health, Scheduler als Alert-Producer).
- Actions (Webhook, Skript, Kamera scharf/unscharf).
