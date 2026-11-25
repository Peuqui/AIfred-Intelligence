# Sub-Agenten Plugin

> **English version:** [subagent.md](../../../en/guides/plugins/subagent.md)

**Datei:** `aifred/plugins/tools/subagent/`

Ein Hauptagent (AIfred, Codine, jeder Agent aus `data/agents.json`) kann während
seiner Tool-Schleife eine abgegrenzte Aufgabe an einen Sub-Agenten delegieren.
Der Sub-Agent ist ein frischer Modellaufruf mit eigenem Kontextfenster, eigenem
Werkzeugkasten und eigener Tool-Schleife. Er sieht weder das Gespräch noch das
Gedächtnis des Aufrufers und kann nicht nachfragen. Eine Persona hat er nur,
wenn er als anderer Hauptagent läuft (Parameter `agent`): Dann bringt er dessen
Identity und Personality mit, also Fachrolle und Arbeitsweise. Zurück
kommt nur sein Bericht, als gewöhnliches Werkzeug-Ergebnis. Das vollständige
Transkript des Sub-Agenten, also Denken, Text, jeder Werkzeugaufruf mit
Argumenten und Ergebnis und der Bericht, erscheint als aufklappbarer Block in
der Chat-Bubble des Hauptagenten. Es geht nie an das Modell und kostet in der
History keine Token.

Architektur und Entscheidungen: [Sub-Agenten als Plugin](../../architecture/subagent-plugin.md).

## Warum: schlanke Hauptagenten, verteilte Last

Jedes Werkzeug, das ein Agent hat, steht mit seiner Beschreibung in jedem
Prompt. Ein Hauptagent mit allen Plugins schleppt so zehntausende Token mit,
bevor er ein Wort der Frage gelesen hat. Mit Sub-Agenten muss er das nicht:
Er behält die Werkzeuge für den Alltag und `delegate_task`, und Spezialarbeit
wie Code, Sandbox oder Dokumente übernimmt ein anderer Hauptagent als
Sub-Agent, mit dessen Identity und Personality, Modell und Werkzeugliste
(Einstellung „Sub-Agent als anderer Hauptagent“). Die Arbeit verteilt sich damit auf mehrere Agenten, jeder
mit dem Werkzeugkasten, den er wirklich braucht, und auf Wunsch jeder mit
eigenem Modell.

Stand 13.09.2026 (seitdem hat AIfred weitere Werkzeuge, darunter `run_task_plan`, in seiner Whitelist; die Zahlen sind eine Messung von damals, keine Neuberechnung), gemessen mit Qwen3.8-27B, Prompt-Anteile in Token laut Debug-Log (gesamt inklusive Memory und kurzer History):

| Agent | Werkzeuge | System | Tool-Schemata | Prompt gesamt |
|---|---|---|---|---|
| AIfred ohne Whitelist | 92 | 10.485 | 20.683 | 31.730 |
| AIfred mit Whitelist und `delegate_task` | 51 | 5.391 | 11.551 | 17.449 |
| Codine (Coding, Sandbox, Workspace) | 27 | 5.276 | 7.315 | 13.299 |

Der schlanke AIfred spart rund 14.000 Prefill-Token je Turn, also 45 % seines
Prompts. Das System-Prompt schrumpft mit, weil nur die Anleitungen der
freigeschalteten Plugins darin stehen. Bei einem Cache-Fehlschlag sind das
bei rund 500 Token pro Sekunde Prefill etwa 28 Sekunden weniger bis zum ersten
Token (gerechnet, nicht einzeln gemessen). Welche Agenten mit welchen
Werkzeuggruppen zur Wahl stehen, sieht der Aufrufer in der Beschreibung des
Parameters `agent`; AIfred wählt dort selbstständig den passenden Agenten, auch
wenn der Nutzer keinen nennt.

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `delegate_task` | Teilaufgabe an einen Sub-Agenten geben; Ergebnis ist dessen Bericht | READONLY |
| `run_task_plan` | Großen Auftrag als Plan aus mehreren Teilen ausführen, je Teil ein Sub-Agent; Ergebnis sind die Zählzeile und alle Einträge | READONLY |

### Parameter (`delegate_task`)

| Parameter | Typ | Pflicht | Beschreibung |
|-----------|-----|---------|--------------|
| `task` | string | ja | Die vollständige, in sich geschlossene Aufgabe: alles, was der Sub-Agent wissen muss |
| `expected_result` | string | ja | Was der Bericht enthalten soll, damit der Aufrufer weiterarbeiten kann |
| `agent` | string | nein | Nur wenn die Einstellung „Sub-Agent als anderer Hauptagent“ an ist: Agent, als der der Sub-Agent läuft: dessen Identity und Personality, Modell und Werkzeugliste. Angeboten werden alle Hauptagenten außer dem Aufrufer; gibt es keinen, fehlt der Parameter. Systemagenten (Kalibrierung, Vision) werden nie angeboten. Die Parameter-Beschreibung listet je Agent die Werkzeug-Gruppen, die sein Sub-Agent hätte (Plugins nach Whitelist und erlaubten Tiers), plus je Gruppe die Plugin-Beschreibung aus der `i18n.json` des Plugins in der Sprache des Turns |

### Parameter (`run_task_plan`)

Dieselben Felder wie bei einem Plan im Horarium (ein Schema, `plan_parameters_schema()`).

| Parameter | Typ | Pflicht | Beschreibung |
|-----------|-----|---------|--------------|
| `frame` | string | ja | Der Rahmen für alle Teile: worum es geht, was ein guter Fund ist, was zu meiden ist. Genannte Quellen sind Startpunkte, keine Grenze |
| `parts` | Liste | ja | Die Teilaufträge, je `id`, `title`, `task` (vollständig: der Teil sieht weder das Gespräch noch die anderen Teile) und `positions` (Liste aus `id` und `name`, mindestens eine; `zusatz` ist reserviert) |
| `columns` | Liste | nein | Typisierte Felder, die jeder Eintrag trägt, je `name` und `type` (`text`, `zahl`, `link`); nicht `position_id`, `status`, `befund`, `quelle`, `neue_quellen` |

Das Werkzeug selbst hat Tier READONLY. Was der Sub-Agent tun darf, bestimmen
die Obergrenze des Aufrufers (Quelle und `max_tier`, ein Telegram-Kanal ohne
Schreibrechte bekommt sie auch über einen Sub-Agenten nicht) und die
Plugin-Einstellung „erlaubte Tiers“.

## Große Aufträge: `run_task_plan`

Für einen Auftrag, der in mehrere unabhängige Gruppen zerfällt (etwa die Preise vieler Produkte in mehreren Shops),
nimmt AIfred nicht `delegate_task`, sondern `run_task_plan`. Du musst das Werkzeug nicht nennen: Sag den Auftrag
vollständig, AIfred entscheidet selbst, ob er groß genug zum Zerlegen ist. Kleine Aufgaben beantwortet er selbst oder
gibt sie mit `delegate_task` ab.

AIfred baut dafür einen Plan aus einem Rahmen (gilt für alle Teile; genannte Quellen sind Startpunkte, keine Grenze), den
Teilen mit je einer Aufgabe und ihren Positionen, optional Spalten für Werte, die jeder Eintrag festhält (z. B. Preis).
Jeder Teil läuft nacheinander als eigener Sub-Agent und trägt für jede Position einen
Status mit Befund und Quelle ein. Ein Wert gilt nur, wenn der Sub-Agent die Quelle in diesem Lauf wirklich abgerufen hat.
Ein langer Lauf kann Minuten dauern; die Transkripte der Teile stehen danach aufklappbar in der Bubble.

Zurück an AIfred kommen zuerst eine feste Zeile, wie viele Positionen geprüft wurden
(„N von M Positionen geprüft“), mit den fehlenden Positionen und gescheiterten Teilen samt Grund, und dann alle Einträge
aller Teile (die Zeile steht vorn, damit sie nicht abgeschnitten wird, wenn ein langes Ergebnis gekürzt wird). Jeder Teil
hat ein Zeitlimit (30:00 min, `PLAN_PART_TIMEOUT_SECONDS`); überschreitet er es, scheitert er mit Grund, und die anderen
Teile laufen weiter. AIfred fasst das
Ergebnis in der Antwort zusammen. Soll derselbe Plan danach regelmäßig laufen, sag es: AIfred speichert ihn unverändert
als Job im [Horarium](horarium.md#jobs-mit-teilaufträgen). Einzelheiten und Grenzen:
[Aufgabenpläne](../../architecture/task-plan.md).

## Wer delegieren darf

Wie jedes Werkzeug müssen `delegate_task` und `run_task_plan` in der Tool-Liste des Agenten in
`data/agents.json` stehen. AIfred hat beide. Sokrates, Salomo, Pater, Codine, Hal und Rabbi haben
`delegate_task`, aber nicht `run_task_plan`. Die Liste ist Pflicht: ein Agent ohne Eintrag `tools` oder mit `null` wird beim
Laden abgelehnt, eine leere Liste (so Vision und Kalibrierung) heißt keine Werkzeuge; ein neu angelegter Agent startet mit
leerer Liste, bis jemand im Editor Werkzeuge freigibt. Hat ein Agent keine Werkzeuge, bekommt sein Sub-Agent nur die
mitgegebenen (`extra_tools`).

## Einstellungen (Zahnrad im Plugin-Modal, `settings.json` beim Plugin)

| Einstellung | Schlüssel | Vorgabe | Bedeutung |
|---|---|---|---|
| Erlaubte Tiers | `SUBAGENT_ALLOWED_TIERS` | `0+2` | Lesen, Recherche, Dateien schreiben, Sandbox. Kein Senden (Tier 1), kein Löschen (Tier 3). Senden bleibt beim Hauptagenten, der es im Gespräch verantwortet |
| Rekursionstiefe | `SUBAGENT_MAX_DEPTH` | `1` | Ob ein Sub-Agent selbst delegieren darf. Bei 1 bekommt der Sub-Agent die Werkzeuge (`delegate_task` und `run_task_plan`) gar nicht erst, die Grenze ist strukturell |
| Sub-Agent als anderer Hauptagent | `SUBAGENT_DELEGATE_TO_OTHER_AGENTS` | aus | Schaltet den Parameter `agent` frei: Der Sub-Agent läuft dann als anderer Hauptagent (z. B. Codine), mit dessen Identity und Personality, Modell und Werkzeugliste, aber ohne Gespräch und Gedächtnis. Ein anderes Modell bedeutet über llama-swap einen Modellwechsel je Delegation, oft Minuten. Der Schalter ist bewusst kein Automatismus |

Memory ist für Sub-Agenten aus und keine Einstellung: Alles, was der Sub-Agent
wissen muss, kommt in der Übergabe vom Hauptagenten. Ein Sub-Agent, der
Erinnerungen anlegt, würde Dinge festschreiben, die niemand im Gespräch gesehen
hat.

## Was der Sub-Agent bekommt

- **System-Prompt**, bewusst schlank: der Rahmen aus `prompts/<lang>/shared/subagent_frame.txt`,
  die Werkzeug-Anleitungen genau der Plugins, deren Werkzeuge er hält, die
  Sicherheitsgrenze, wenn die Nachricht nicht vom Eigentümer ist, und die geteilte
  Schicht `disciplines`. Er antwortet dem Aufrufer, nicht dem Benutzer: kein
  Ausgabeformat, keine Formatregeln, keine Rückfrage. Kein Reminder, kein
  Memory-Kontext. Ohne `agent` auch keine Identity, kein Reasoning und keine
  Personality. Mit `agent` kommen Identity, Reasoning-Schalter und Personality des
  gewählten Agenten dazu, in derselben Schichten-Reihenfolge wie im normalen Turn:
  Identity, Reasoning, Rahmen, Sicherheitsgrenze, Personality, Werkzeug-Anleitungen, `disciplines`.
  Ist der Persönlichkeits-Schalter dieses Agenten in den Einstellungen aus,
  fehlt die Personality auch hier.
- **User-Nachricht**: `task` und `expected_result` aus `prompts/<lang>/shared/subagent_task.txt`. History: leer.
- **Modell, Sampling, Thinking und Kontextgröße** des Aufrufers, oder des per
  `agent` gewählten Agenten, über dieselben Helfer wie ein normaler Turn.
- **Werkzeugkasten**: der normale Kasten des Agenten über die Werkzeug-Fabrik,
  Memory aus, gefiltert auf die erlaubten Tiers, ohne `delegate_task` an der
  letzten erlaubten Tiefe. Eigene Kettentiefe und eigener Schleifenbrecher, ein
  eigenes Ausgabebudget für Werkzeug-Ergebnisse, der Kontext-Wächter des
  Backends wie bei jedem Turn.

## Was der Hauptagent bekommt

Den Bericht, als `role=tool`-Nachricht. Hat der Sub-Agent in der Sandbox Seiten
oder Bilder erzeugt, stehen deren `SANDBOX_HTML_URL`- und
`SANDBOX_IMAGE_URL`-Zeilen vor dem Bericht, genau wie bei einem eigenen
`execute_code`-Aufruf; so kann der Hauptagent sie etwa mit `render_html`
weiterverwenden. Das Ergebnis läuft durch dieselbe Kappung wie
jedes Werkzeug-Ergebnis, die nur greift, wenn er den freien Kontext des
Hauptmodells sprengen würde. Beim Aufrufer zählt die Delegation als ein
Werkzeugaufruf.

## Was der User sieht

Während der Sub-Agent arbeitet, zeigt die Statuszeile, welches Werkzeug er
gerade ruft. Danach steht das Transkript als aufklappbarer Block in der Bubble
des Hauptagenten, an der Stelle des Turns, an der delegiert wurde: Denkprozess,
Text und Transkripte erscheinen in der Reihenfolge, in der sie entstanden. Was
die Werkzeuge des Sub-Agenten für die Bubble erzeugt haben, geht mit nach oben,
als hätte der Hauptagent es selbst erzeugt: Sandbox-Seiten und -Bilder,
Kamerabilder samt VLM-Beschreibung, die abgerufenen Webseiten im Quellen-Block
und die Transkripte verschachtelter Sub-Agenten. Alles steht direkt unter dem
Transkript an der Stelle der Delegation. Die Blöcke sind zugeklappt: Eine
Sandbox-Seite zeigt ihre Zeile mit „Im Browser öffnen“ (öffnet einen neuen Tab,
ohne den Block aufzuklappen), aufgeklappt das eingebettete Programm und darunter
seinen Quelltext. Der Hauptagent fasst das Ergebnis in seiner Antwort nur
zusammen und schreibt Code oder Dateien nicht erneut ab. Über den Message Hub
(Telegram, E-Mail) gibt es keine Bubble; dort steht der Ablauf im Debug-Log der
Session.

**Kennzahlen:** Das Transkript endet mit derselben Kennzahlen-Zeile wie eine
Antwort, kursiv in Klammern: TTFT, Prefill, Token pro Sekunde, Dauer, Modell
und Backend. Sie gilt nur für die Arbeit des Sub-Agenten, samt der
Sub-Agenten, an die er selbst weiterdelegiert hat. Die Zeile unter der Antwort
des Hauptagenten zeigt dagegen den ganzen Turn: Prefill und Token pro Sekunde
rechnen alle Serveranfragen zusammen, also jede Werkzeugrunde des Hauptagenten
und alle Anfragen seiner Sub-Agenten. Gerechnet wird gesamte Token durch
gesamte Rechenzeit, nicht der Mittelwert der einzelnen Raten, damit ein kurzer
Folge-Prefill nicht so viel zählt wie ein langer kalter. Die Denkzeit addiert
jeden Denkblock des Turns, auch die vor späteren Werkzeugrunden und die der
Sub-Agenten. Die Dauer ist die
Wanduhr des ganzen Turns, Wartezeit auf den Sub-Agenten eingeschlossen. Ist
eine einzige Anfrage nicht messbar, steht beim Prefill „n/a“ statt einer Zahl,
die Arbeit unterschlägt.

## Bewusst nicht enthalten

- Keine parallelen Sub-Agenten: Auf Single-User-Hardware bringt Parallelität
  nichts, die Tool-Schleife arbeitet nacheinander.
- Keine Zeitgrenze nach der Wanduhr bei `delegate_task`: Kettentiefe und Kontextfenster begrenzen
  den Sub-Agenten, das Lebenszeichen der Tool-Schleife hält lange Delegationen
  am Leben. Die Teile eines Plans (`run_task_plan`, Horarium) haben dagegen ein Zeitlimit je Teil.
- Kein Weg, das Transkript ans Hauptmodell zu geben.

## Beispiel

AIfred bekommt: „Vergleiche die drei Angebote in `angebote/` und sag mir, welches
das günstigste über fünf Jahre ist.“ Er delegiert: `task` mit den drei
Dateinamen, dem Vergleichszeitraum und der Bitte, alle Kostenpositionen zu
tabellieren; `expected_result`: „Tabelle je Angebot mit Gesamtkosten über fünf
Jahre und ein Satz zur Empfehlung“. Der Sub-Agent liest die Dateien, rechnet in
der Sandbox, liefert die Tabelle. AIfred antwortet dem User mit der Empfehlung,
das Transkript mit den gelesenen Dateien und Rechnungen liegt aufklappbar
darunter.
