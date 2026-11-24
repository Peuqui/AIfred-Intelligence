# Horarium Plugin

> **English version:** [horarium.md](../../../en/guides/plugins/horarium.md)

**Datei:** `aifred/plugins/tools/horarium/`

Geplante Aufgaben und Cron-Jobs, die AIfred automatisch zu definierten Zeitpunkten ausführt.
Zum geplanten Zeitpunkt wird die `message` des Jobs wie ein normaler Prompt verarbeitet und
das Ergebnis an den konfigurierten Delivery-Modus übergeben.

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `horarium_create` | Neuen geplanten Job anlegen | WRITE_DATA |
| `horarium_list` | Alle geplanten Jobs mit Status, nächstem Lauf, Delivery-Modus und Auftragstext auflisten | READONLY |
| `horarium_update` | Geplanten Job an Ort und Stelle ändern (nur die übergebenen Felder; auch ein-/ausschalten; einzelne Teile eines Plans anfügen, ändern, entfernen) | WRITE_DATA |
| `horarium_delete` | Geplanten Job anhand seiner ID löschen | WRITE_DATA |

## `horarium_create`-Parameter

| Parameter | Pflicht | Beschreibung |
|-----------|---------|--------------|
| `name` | ja | Kurzer, aussagekräftiger Name für den Job |
| `schedule_type` | ja | Einer von `cron`, `interval`, `once` |
| `schedule_expr` | ja | Cron-Ausdruck, Intervall in Sekunden oder ISO-Timestamp |
| `message` | ja | Der Prompt, den AIfred zum geplanten Zeitpunkt verarbeitet |
| `agent` | nein | Zu verwendender Agent (Standard: `aifred`) |
| `delivery` | nein | `review`, `announce`, `webhook` (Standard: `review`) |
| `channel` | nein | Zielkanal für `announce` (z.B. `telegram`, `discord`, `email`) |
| `recipient` | nein | Empfänger für `announce` (E-Mail-Adresse usw.) |
| `webhook_url` | nein | URL für `webhook`-Delivery |
| `end_date` | nein | Ende eines `cron`-/`interval`-Jobs: `JJJJ-MM-TT` (der Tag läuft noch mit) oder `JJJJ-MM-TTTHH:MM`; ohne Angabe läuft der Job unbegrenzt. `horarium_update` setzt oder entfernt es (leerer Wert) |
| `frame` | nein | Nur bei Jobs mit Teilaufträgen: der Rahmen, der für alle Teile gilt |
| `parts` | nein | Die Teilaufträge, je `id`, `title`, `task` und `positions` (je `id`, `name`) |
| `columns` | nein | Typisierte Felder der Einträge, je `name` und `type` (`text`, `zahl`, `link`) |

## Features

- **Drei Schedule-Typen:** `cron` (Cron-Ausdruck, z.B. `0 8 * * *` = täglich 8 Uhr), `interval` (Sekunden, z.B. `3600` = stündlich), `once` (ISO-Timestamp, z.B. `2026-03-30T10:00:00`)
- **Delivery-Modi:** `review` (Standard, in UI anzeigen), `announce` (an einen Kanal senden), `webhook` (HTTP POST); zugestellt wird die ganze Antwort wie im Browser (ohne Denkblöcke), der Agent verschickt nichts selbst
- **Historie pro Job:** Jeder Lauf sieht, was die letzten Läufe zugestellt haben
- **Tier-Begrenzung:** Jobs laufen standardmäßig mit der Stufe des Besitzers (`JOB_DEFAULT_TIER`); beim Anlegen lässt sich mit `max_tier` eine niedrigere festlegen, höchstens bis zur eigenen Stufe. Teilaufträge senden nie selbst (ohne `TIER_COMMUNICATE`), die Zustellung macht das Horarium
- **Isolierte Ausführung:** Jeder Job läuft aus seinem eigenen gespeicherten Payload
- **Persistent:** Jobs werden im Job-Store abgelegt und überleben Neustarts des Services; was beim Neustart überfällig ist, läuft nach
- **Aus heißt aus:** Die Ausführungsschleife gehört dem Plugin. Ist es aus, startet kein Job; ein gerade laufender Job läuft zu Ende. Termine der Aus-Zeit verfallen: Beim Einschalten rücken fällige Jobs auf ihren nächsten regulären Termin, ohne Lauf und ohne Eintrag im Laufprotokoll (ein einmaliger Job wird ausgeschaltet)

## Jobs mit Teilaufträgen

Ein großer Auftrag (viele Positionen, mehrere unabhängige Gruppen, z. B. Preise vieler Produkte in mehreren Shops, jede
Woche neu) wird in einem einzigen Prompt unzuverlässig: Positionen fallen still weg, Werte werden aus früheren Läufen
kopiert. Deshalb zerlegt AIfred so einen Auftrag schon beim Anlegen selbst. Du sagst nur, was du willst; AIfred baut daraus
einen Rahmen, die Teile mit ihren Positionen, bei Bedarf Spalten für Werte, die jeder Eintrag festhält. Kleine Aufträge bleiben
ein einzelner Auftrag ohne Teile. Hat AIfred den Plan vorher im Gespräch mit `run_task_plan` ausgeführt, übernimmt er ihn
unverändert als Job. Mit festem Text (`fixed_text`) lassen sich Teile nicht kombinieren.

**Was bei einem Lauf passiert:**

- Die Teile laufen nacheinander, jeder als eigener Sub-Agent. Sie suchen frei; genannte Quellen sind Startpunkte, keine
  Grenze. Für jede Position tragen sie einen Eintrag ein (gefunden, nicht gefunden, Fehler), ein Fund zählt nur mit einer
  Quelle, die wirklich abgerufen wurde. Neu entdeckte Quellen hängt das Horarium an die Notizen des Jobs an.
- Fehlt nach einem Teil noch eine Position, wird der Teil genau einmal nachgefragt; danach gilt sie als „nicht geprüft“.
- Scheitert ein Teil, läuft der Rest trotzdem. In der Nachricht steht, welcher Teil gescheitert ist und warum.
- Zum Schluss schreibt AIfred die Nachricht aus allen Ergebnissen. Darunter hängt das Horarium selbst eine Zeile wie
  „11 von 12 Positionen geprüft“, bei Lücken mit den fehlenden Positionen. Diese Zeile schreibt nicht das Modell.
- Die Transkripte der Teile stehen aufklappbar im Verlauf der Sitzung des Laufs, nicht in dem, was das Modell liest.

**Jeder Lauf steht für sich:** Das Horarium führt kein Archiv und keine Verlaufstabelle; die Teile sehen nie die Funde
früherer Läufe. Wissen über Quellen liegt in den Notizen des Jobs (neue Quellen trägt das Horarium dort ein). Wer Werte
über Läufe sammeln will (z. B. Preise), schreibt das in den Auftrag: Der Abschluss führt die Tabelle dann selbst in einer
Datei im Ordner des Jobs.

**Bearbeiten im Chat:** Sag AIfred, was sich ändern soll; er ändert einzelne Teile, ohne den Rest zu berühren (Teil
anfügen, ändern oder entfernen, außerdem Rahmen, und Spalten ersetzen). „Zeig mir meine Jobs“ listet den
Plan vollständig. Wird die Änderung abgelehnt (z. B. doppelte Teil-ID), bleibt der Job unverändert.

**Ein Job ändert sich nicht selbst:** Ein laufender Job darf seinen eigenen Job weder ändern noch löschen. Ihm
auffallende Verbesserungen schreibt er in seinen Bericht; ob sie übernommen werden, entscheidest du im Chat.

Aufbau und Ablauf im Detail: [Scheduler](../../architecture/scheduler.md#jobs-mit-teilaufträgen).

## Jetzt ausführen

Auf der Seite (Knopf „Jetzt ausführen“) startet ein Auftrag sofort, auch außerhalb seiner Zeit — auch ein
ausgeschalteter Auftrag (der Knopf ist die ausdrückliche Absicht). Seine Termine bleiben unverändert: Der nächste
reguläre Lauf kommt zu seinem eingetragenen Termin, der Ein-/Aus-Zustand und das Datum des letzten Laufs rücken nicht,
und ein einmaliger Auftrag wird vom Probelauf nicht verbraucht. Der Lauf erscheint im Protokoll der letzten Läufe.
Abgelehnt mit klarer Meldung, wenn Horarium ausgeschaltet ist oder der Auftrag gerade läuft; ein unbekannter Auftrag
gibt 404.

## Anwendungsbeispiele

Gesprochene bzw. Chat-Anfragen, die AIfred auf die Tools abbildet:

- „Fasse jeden Morgen um 7 meine E-Mails zusammen und schick es an Telegram"
  → `horarium_create(name="Morgen-Mail-Digest", schedule_type="cron", schedule_expr="0 7 * * *", message="Fasse meine neuen E-Mails zusammen", delivery="announce", channel="telegram")`
- „Erinnere mich morgen um 10 an den Arzttermin“
  → `horarium_create(name="Arzt-Reminder", schedule_type="once", schedule_expr="2026-03-31T10:00:00", message="Erinnere mich an den Arzttermin", delivery="review")`
- „Prüfe die Black-Week-Angebote 3x täglich bis Ende November“
  → `horarium_create(name="Black Week", schedule_type="cron", schedule_expr="0 8,14,20 * * *", end_date="2026-11-30", message="…")` (EIN Job mit einer Liste von Uhrzeiten)
- „Zeig mir meine geplanten Jobs“ → `horarium_list()`
- „Verschieb Job 3 auf 7:05“ → `horarium_update(job_id=3, schedule_expr="5 7 * * *")`
- „Lösche Job 3“ → `horarium_delete(job_id=3)`
