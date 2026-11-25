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
| `horarium_list` | Alle geplanten Jobs mit Status, nächstem Lauf, Delivery-Modus und Auftragstext auflisten; bei einem Job mit Plan auch Rahmen, Teile (mit Aufgabe und Positionen) und Spalten | READONLY |
| `horarium_update` | Geplanten Job an Ort und Stelle ändern (nur die übergebenen Felder; auch ein-/ausschalten; einzelne Teile eines Plans anfügen, ändern, entfernen) | WRITE_DATA |
| `horarium_delete` | Geplanten Job anhand seiner ID löschen | WRITE_DATA |

## `horarium_create`-Parameter

| Parameter | Pflicht | Beschreibung |
|-----------|---------|--------------|
| `name` | ja | Kurzer, aussagekräftiger Name für den Job |
| `schedule_type` | ja | Einer von `cron`, `interval`, `once` |
| `schedule_expr` | ja | Cron-Ausdruck, Intervall in Sekunden oder ISO-Timestamp |
| `message` | ja | Der Prompt, den AIfred zum geplanten Zeitpunkt verarbeitet |
| `keeps_notes` | ja | Ob der Job eine Akte führt, eine Notizdatei im Arbeitsbereich, in der er festhält, was er bei späteren Läufen wissen muss. `true`, wenn der Job über Läufe hinweg etwas wissen muss (Beobachtungen über Wochen, Preisverläufe, schon Verwendetes, das sich nicht wiederholen soll); `false`, wenn jeder Lauf alles frisch holt (z. B. eine Tagesübersicht aus Kalender und Mails). Hat der Benutzer es nicht gesagt und ist es nicht eindeutig, fragt AIfred vor dem Anlegen kurz nach. Die Akte braucht zusätzlich die Stufe 2 und einen Agenten mit `read_file` und `write_file` |
| `agent` | nein | Zu verwendender Agent (Standard: `aifred`) |
| `delivery` | nein | `review`, `announce`, `webhook` (Standard: `review`) |
| `channel` | nein | Zielkanal für `announce` (z.B. `telegram`, `discord`, `email`, `freeecho2`; auf `freeecho2` wird die Antwort in den Räumen vorgelesen) |
| `recipient` | nein | Empfänger für `announce` (E-Mail-Adresse usw.; bei `freeecho2` ein Raum, `@gruppe` oder `*` für alle verbundenen Pucks, Standard) |
| `webhook_url` | nein | URL für `webhook`-Delivery |
| `fixed_text` | nein | `true` = kein Modell: `message` geht wörtlich und pünktlich raus. Braucht `delivery` `announce`; schließt Teile aus. Für Wecker und einfache Erinnerungen |
| `audio_type` | nein | Für `freeecho2`: der Ton vor der Ansage, `notification` (Standard, sanfter Gong) oder `alarm` |
| `max_tier` | nein | Sicherheitsstufe des Jobs (0 nur lesen, 1 kommunizieren, 2 Daten schreiben, 3 System). Ohne Angabe die eigene Stufe, höchstens 2 (`JOB_DEFAULT_TIER`); mehr, als man selbst hat, lässt sich nicht vergeben |
| `end_date` | nein | Ende eines `cron`-/`interval`-Jobs: `JJJJ-MM-TT` (der Tag läuft noch mit) oder `JJJJ-MM-TTTHH:MM`; ohne Angabe läuft der Job unbegrenzt. `horarium_update` setzt oder entfernt es (leerer Wert) |
| `frame` | nein | Nur bei Jobs mit Teilaufträgen: der Rahmen, der für alle Teile gilt |
| `parts` | nein | Die Teilaufträge, je `id`, `title`, `task` und `positions` (je `id`, `name`) |
| `columns` | nein | Typisierte Felder der Einträge, je `name` und `type` (`text`, `zahl`, `link`) |

## `horarium_update`-Parameter

Nur `job_id` ist Pflicht; was fehlt, bleibt, wie es ist. Dieselben Felder wie bei `horarium_create` (`name`, `schedule_type`,
`schedule_expr`, `message`, `agent`, `delivery`, `channel`, `recipient`, `webhook_url`, `fixed_text`, `audio_type`,
`end_date`, `max_tier`) mit diesen Unterschieden und Ergänzungen:

| Parameter | Beschreibung |
|-----------|--------------|
| `job_id` | ID des Jobs (Pflicht; „41“ als Text wird als Zahl gelesen) |
| `schedule_type` | Neuer Typ; dann auch `schedule_expr` angeben |
| `enabled` | `true` schaltet den Job ein, `false` aus |
| `end_date` | Setzt oder verschiebt das Ende; ein leerer Wert entfernt es |
| `max_tier` | Neue Stufe; ohne Angabe bleibt sie |
| `keeps_notes` | Schaltet die Akte ein oder aus (hier nicht Pflicht; ohne Angabe bleibt es). Ausschalten lässt eine vorhandene Akte unberührt im Arbeitsbereich liegen |
| `frame` | Ersetzt den Rahmen des Plans ganz |
| `columns` | Ersetzt die Spalten ganz (leere Liste = keine Spalten) |
| `part_add` | Fügt einen Teil an (`id`, `title`, `task`, `positions`); die `id` darf noch nicht vorkommen |
| `part_update` | Ersetzt den Teil mit derselben `id` ganz; die `id` muss vorkommen |
| `part_remove` | `id` eines Teils (Text), der entfernt wird; muss vorkommen |

Die Operationen laufen in der Reihenfolge Rahmen, Spalten, entfernen, ändern, anfügen; danach wird der ganze Plan geprüft,
und bei einem Fehler wird nichts gespeichert.

## Features

- **Drei Schedule-Typen:** `cron` (Cron-Ausdruck, z.B. `0 8 * * *` = täglich 8 Uhr), `interval` (Sekunden, z.B. `3600` = stündlich), `once` (ISO-Timestamp, z.B. `2026-03-30T10:00:00`)
- **Delivery-Modi:** `review` (Standard, in UI anzeigen), `announce` (an einen Kanal senden), `webhook` (HTTP POST); zugestellt wird die ganze Antwort wie im Browser (ohne Denkblöcke), der Agent verschickt nichts selbst
- **Historie pro Job:** Das Laufprotokoll hält, was die letzten Läufe zugestellt haben (die Jobs-Seite zeigt es); der Lauf selbst erfährt davon nur die Zeiten der letzten Läufe, nicht die Texte
- **Akte führen:** Jeder Job hat den Schalter `keeps_notes`. Nur wenn er an ist und der Job die Stufe 2 und einen Agenten mit `read_file` und `write_file` hat, bekommt der Lauf den Notiz-Prompt, legt das Horarium den Job-Ordner an und trägt neue Quellen in die Akte ein. `horarium_list` zeigt den Schalter je Job, die Jobs-Seite hat dafür die Option „Akte führen“. Einzelheiten: [Scheduler](../../architecture/scheduler.md#akte-führen-keeps_notes)
- **Tier-Begrenzung:** Jobs laufen standardmäßig mit der Stufe des Besitzers (`JOB_DEFAULT_TIER`); mit `max_tier` lässt sich beim Anlegen und beim Ändern eine andere festlegen, auch eine niedrigere oder eine höhere als der Standard, aber höchstens bis zur eigenen Stufe (`_job_tier`). Teilaufträge senden nie selbst (ohne `TIER_COMMUNICATE`), die Zustellung macht das Horarium
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
  Quelle, die wirklich abgerufen wurde. Neu entdeckte Quellen hängt das Horarium an die Notizen des Jobs an (nur wenn der Job eine Akte führt).
- Fehlt nach einem Teil noch eine Position, wird der Teil genau einmal nachgefragt; danach gilt sie als „nicht geprüft“.
- Scheitert ein Teil, läuft der Rest trotzdem. In der Nachricht steht, welcher Teil gescheitert ist und warum. Ein Teil hat
  ein Zeitlimit von 30:00 min (`PLAN_PART_TIMEOUT_SECONDS`, per Umgebung änderbar); überschreitet er es, scheitert er mit
  dem Grund „Zeitlimit … überschritten“, die Einträge bis dahin bleiben. Die Zahl der Werkzeugaufrufe ist je Teil
  begrenzt (`SECURITY_MAX_TOOL_CHAIN_DEPTH`, Standard 100, geteilt von Lauf und Nachforderung des Teils).
- Neue Quellen, die ein Teil meldet (`neue_quellen`), nimmt das Horarium nur als einzeilige http(s)-Adressen an und trägt
  nur die in die Notizen ein, die dort noch nicht stehen, und nur in schon bestehende Notizen.
- Zum Schluss schreibt AIfred die Nachricht aus allen Ergebnissen. Darunter hängt das Horarium selbst eine Zeile wie
  „11 von 12 Positionen geprüft“, bei Lücken mit den fehlenden Positionen. Diese Zeile schreibt nicht das Modell; sie steht
  auch in der Sitzung des Laufs, als eigene Bubble nach der Antwort.
- Scheitert der Abschluss nach den Teilen, gehen die Ergebnisse nicht verloren: Sie werden mit einem Hinweis auf den Grund
  und der Zählzeile zugestellt, stehen im Laufprotokoll und als Nachricht in der Sitzung, die AIfred in seinem Verlauf sieht
  (dort kannst du weiterarbeiten, etwa den Bericht nachholen). Der Lauf zählt trotzdem als gescheitert. Stoppst du den Lauf
  nach den Teilen, stehen die Ergebnisse nur in der Sitzung; zugestellt wird nichts.
- Der ganze Lauf (Teile, Abschluss, Zustellung) ist ein Zug der Sitzung: Schreibst du dort in dieser Zeit, wartet deine
  Nachricht und startet danach; die Oberfläche zeigt „läuft“. Der Stopp bricht den Lauf ab, er zählt als Fehlschlag, der
  Scheduler läuft weiter.
- Im Verlauf der Sitzung erscheint gleich beim Start eine Bubble „Job … gestartet: N Teilaufträge“ mit den Teilen und ihren
  Positionen (zugeklappt), und nach jedem Teil eine eigene Bubble: ein aufklappbarer Block, dessen Titelzeile auch
  zugeklappt Teil, Stand („N von M Positionen, K Einträge, Dauer“, bei Lücken „nicht geprüft: …“, bei einem Fehler
  „gescheitert: Grund“) zeigt; aufgeklappt stehen die Transkripte des Teils. Diese Bubbles sieht nur die Oberfläche, nicht
  das Modell.

**Jeder Lauf steht für sich:** Die Teile sehen nie die Funde früherer Läufe; das Horarium bewahrt sie nicht auf. Wissen über Quellen liegt in den Notizen des Jobs (neue Quellen trägt das Horarium dort ein). Wer Werte
über Läufe sammeln will (z. B. Preise), schreibt das in den Auftrag: Der Abschluss führt die Tabelle dann selbst in einer
Datei im Ordner des Jobs.

**Bearbeiten im Chat:** Sag AIfred, was sich ändern soll; er ändert einzelne Teile, ohne den Rest zu berühren (Teil
anfügen, ändern oder entfernen, außerdem Rahmen, und Spalten ersetzen). „Zeig mir meine Jobs“ listet den
Plan vollständig. Wird die Änderung abgelehnt (z. B. doppelte Teil-ID), bleibt der Job unverändert.

**Ein Job ändert sich nicht selbst:** Ein laufender Job darf seinen eigenen Job weder ändern noch löschen. Ihm
auffallende Verbesserungen schreibt er in seinen Bericht; ob sie übernommen werden, entscheidest du im Chat.

**Auf der Jobs-Seite:** Im Formular eines Jobs fügt „Teilaufträge hinzufügen“ den Plan-Block an: Rahmen, Teile (ID, Titel,
Aufgabe, Positionen mit ID und Name) und Spalten (Name, Typ). Mit Teilen heißt das Nachrichtenfeld „Abschluss“: Was der
Abschluss mit den Ergebnissen tun soll (zusammenfassen, vergleichen, was in die Nachricht gehört). „Fester Text“ und Teile
schließen sich aus; der jeweils andere Schalter ist ausgegraut, mit dem Grund daneben. Die Jobliste zeigt bei einem Job mit
Plan, wie viele Teile und Positionen er hat. Gespeichert wird über die Jobs-API (`frame`, `parts`, `columns`), mit
derselben Prüfung wie beim Werkzeug.

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
