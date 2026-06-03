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
  an die Sinks der Regel zustellen. **Keine eigene Sink-Registry** — die
  SSoT ist `plugin_registry`: der Dispatcher löst jeden Sink über
  `plugin_registry.get_channel(name)` auf und ruft dessen `send_proactive(...)`.
  (Für Tests injizierbarer Resolver, Default = das echte Registry.)
  Throttle-State: `(rule, dedup_key) → letzter Versand`.

Gating wie `armed` (scharf) ist **producer-spezifisch** und bleibt im
Producer (Vision emittiert nur, wenn scharf) — der Kern bleibt agnostisch.

## Plugbare Rollen

1. **Producer** (neu) — meldet sich mit Manifest an (`name`, `categories`,
   Quell-Enumeration) und `emit`-et. Die zentrale Regel-UI baut sich daraus
   selbst auf. Erster Producer: **Vision**.
2. **Sink** — die bestehenden **Channel-Plugins** (im `plugin_registry`, der
   SSoT) bekommen eine neue Methode `send_proactive(...)` auf der
   `BaseChannel`-Basis — Default „nicht unterstützt", einzelne Channels
   überschreiben. Heute können sie nur `send_reply` auf Inbound. Das **Ziel
   löst das Channel-Plugin selbst auf** (Telegram: erlaubte Chats /
   `routing_table`), die Regel nennt nur den Kanalnamen. Erster Sink:
   **Telegram** (Text + Foto).
3. **Action** (später) — nicht jede Reaktion ist „Nachricht": Webhook,
   Skript, Kamera scharf/unscharf. Channel-Send ist nur *eine* Action-Art.

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

## Offene Entscheidungen

- Producer-Registrierung: simple Startup-Registrierung jetzt, formales
  Manifest-Plugin, sobald die Regel-UI kommt.
- Regel-Config-Format/Ort (JSON neben den Plugin-Settings vs. zentral).
- Nachricht: Template zuerst (deterministisch), AIfred-formuliert optional.
