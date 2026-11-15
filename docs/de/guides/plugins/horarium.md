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
| `horarium_update` | Geplanten Job an Ort und Stelle ändern (nur die übergebenen Felder; auch ein-/ausschalten) | WRITE_DATA |
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

## Features

- **Drei Schedule-Typen:** `cron` (Cron-Ausdruck, z.B. `0 8 * * *` = täglich 8 Uhr), `interval` (Sekunden, z.B. `3600` = stündlich), `once` (ISO-Timestamp, z.B. `2026-03-30T10:00:00`)
- **Delivery-Modi:** `review` (Standard, in UI anzeigen), `announce` (an einen Kanal senden), `webhook` (HTTP POST); zugestellt wird die ganze Antwort wie im Browser (ohne Denkblöcke), der Agent verschickt nichts selbst
- **Historie pro Job:** Jeder Lauf sieht, was die letzten Läufe zugestellt haben
- **Tier-Begrenzung:** Jobs laufen mit `TIER_COMMUNICATE`, nicht mit dem Tier des erstellenden Users; Erinnerungen schreiben dürfen sie trotzdem, weil sie dem Owner gehören
- **Isolierte Ausführung:** Jeder Job läuft aus seinem eigenen gespeicherten Payload
- **Persistent:** Jobs werden im Job-Store abgelegt und überleben Neustarts des Services; was beim Neustart überfällig ist, läuft nach
- **Aus heißt aus:** Die Ausführungsschleife gehört dem Plugin. Ist es aus, startet kein Job; ein gerade laufender Job läuft zu Ende. Termine der Aus-Zeit verfallen: Beim Einschalten rücken fällige Jobs auf ihren nächsten regulären Termin, ohne Lauf und ohne Eintrag im Laufprotokoll (ein einmaliger Job wird ausgeschaltet)

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
