# Aufgabenpläne (Plan-Engine)

> **English version:** [task-plan.md](../../en/architecture/task-plan.md)

Stand 2026-11-24. Die Plan-Engine zerlegt einen großen Auftrag in Teile, lässt jeden Teil von einem eigenen Sub-Agenten
bearbeiten und sorgt dafür, dass keine Position still verloren geht. Sie liegt in `aifred/lib/` und kennt weder das
Horarium noch einen Kanal; zwei Aufrufer nutzen sie (siehe unten).

## Zweck

Ein einziger Prompt für zwölf Positionen, Recherche, Tabelle und Bericht lässt Positionen fallen, kopiert Werte aus
früheren Läufen und merkt es nicht. Die Engine macht daraus kleine Aufträge mit frischem Kontext und prüft im Code, was
das Modell nur zusagen würde: Jede Position braucht einen Eintrag, ein gefundener Wert braucht eine Quelle, die der
Sub-Agent in diesem Lauf tatsächlich gelesen hat, und was fehlt, steht als „nicht geprüft“ im Ergebnis.

## Bausteine

| Modul | Aufgabe |
|---|---|
| `lib/task_plan.py` | Datenmodell (`TaskPlan`, `PlanPart`, `PlanPosition`, `PlanColumn`), die eine Prüfung `plan_problem`, das Schema der Plan-Felder für Werkzeuge (`plan_parameters_schema`, Quelle `prompts/tools/task_plan.params.json`) und die Teil-Operationen (`parts_with_added`, `parts_with_replaced`, `parts_without`) |
| `lib/plan_record.py` | das Eintragswerkzeug `plan_record` (`make_record_tool`), die gesammelten Einträge (`PartRecords`, `PlanEntry`) |
| `lib/plan_engine.py` | `run_plan` (Teile nacheinander), `render_results` (der Text für ein Modell), `coverage_line` (die feste Zählzeile) |
| `lib/subagent_run.py` | `SubagentSession`, der Lauf eines Sub-Agenten, siehe [Sub-Agenten](subagent-plugin.md) |
| `prompts/<lang>/task_plan/` | jeder Text, den das Modell sieht: Teil-Prompt, Nachforderung, Werkzeugbeschreibung und Ablehnungen von `plan_record`, Ergebnisdarstellung, Zählzeile |

Der Plan hat drei Felder: `frame` (Rahmen für alle Teile), `parts` (je `id`, `title`, `task` und die Positionen mit `id` und
`name`) und `columns` (die typisierten Felder der Einträge mit Typ `text`, `zahl` oder `link`).
Ein Plan mit Rahmen oder Spalten, aber ohne Teile wird abgelehnt (`plan_no_parts`): Beides gilt nur für Teile. Dieselbe Struktur ist die Payload eines Horarium-Jobs ([Scheduler](scheduler.md#jobs-mit-teilaufträgen)) und die
Parameter des Chat-Werkzeugs, ein Plan aus dem Chat lässt sich deshalb unverändert als Job speichern.

## Ablauf

`run_plan(plan, ctx, allowed_tiers=…, depth=…, notes_hint=…)` ist ein asynchroner Generator. Er läuft die Teile
nacheinander ab, nicht parallel. Für jeden Teil:

1. `part_task` baut den Auftrag aus `task_plan/part.txt`: Rahmen, Teil, Positionen, Spalten mit Typ, der Notizen-Hinweis und die Arbeitsregeln (breit
   suchen, Quellen sind Startpunkte, nur frisch abgerufene Werte eintragen, jede Position mindestens einmal eintragen,
   Zusatzfunde erlaubt, mit einem kurzen Bericht schließen).
2. Eine `SubagentSession` mit dem Agenten des Aufrufers, ohne Persona und mit `plan_record` als Zusatzwerkzeug führt den
   Teil aus. Die erlaubten Stufen und die Tiefe bestimmt der Aufrufer.
3. Bleiben danach Positionen ohne Eintrag (egal mit welchem Status; auch `nicht_gefunden` und `fehler` sind Einträge),
   schickt die Engine genau eine Nachforderung (`task_plan/missing.txt`) in denselben Lauf (`follow_up`). Was danach noch
   fehlt, steht als `not_checked` im Ergebnis des Teils.
4. Wirft ein Teil eine Ausnahme, wird sie geloggt und als Grund des Teils festgehalten (`failure`); die weiteren Teile
   laufen. Einträge, die der Teil vorher angenommen hat, bleiben gültig.
5. Jeder Teil hat ein Zeitlimit, `PLAN_PART_TIMEOUT_SECONDS` in `config.py` (Standard 1800 s = 30:00 min, per
   Umgebungsvariable änderbar), für Lauf und Nachforderung zusammen. Läuft er länger, bricht die Engine ihn ab (`_within`);
   er scheitert mit dem Grund aus `task_plan/part_timeout.txt` („Zeitlimit von … überschritten“), die schon angenommenen
   Einträge bleiben, die weiteren Teile laufen. Das gilt für jeden Aufrufer, auch für `run_task_plan` im Chat. Der Bericht
   des Teils bleibt erhalten, auch wenn die Nachforderung scheitert (`outcome.report` wird nach dem Lauf und nach der
   Nachforderung gesetzt).
6. Das Kettenlimit `SECURITY_MAX_TOOL_CHAIN_DEPTH` (Standard 100 Werkzeugaufrufe) zählt je Teil: Jeder Teil hat seinen
   eigenen Werkzeugkasten und damit seinen eigenen Zähler; Lauf und Nachforderung desselben Teils teilen ihn, jeder
   `plan_record` zählt mit.

Die Ereignisse für den Aufrufer sind `progress` (zu Beginn jedes Teils „▶ Part i/n starts: Titel (N positions)“, danach mit dem Titel des Teils vorneweg), `work` und `artifacts` (die
Transkripte der Sub-Agenten) wie bei jedem Werkzeug-Executor, nach jedem Teil `{"part_done": PartDone}` (Ergebnis des Teils,
seine Transkripte als rohe Artefakte, Laufzeit in Sekunden; auch für einen gescheiterten Teil), zuletzt
`{"plan_result": PlanResult}`. Das Chat-Werkzeug `run_task_plan` reicht `part_done` nicht weiter.

## Eintragswerkzeug und Abrufprüfung

`plan_record` (Stufe `TIER_READONLY`) gibt es nur im Werkzeugkasten eines Teil-Sub-Agenten. Sein Schema entsteht je Teil: `position_id` ist
eine Aufzählung der Positionen des Teils plus `zusatz` (ein Fund außerhalb der Liste), `status` ist `gefunden`,
`nicht_gefunden` oder `fehler`, dazu `befund` (Text), `quelle`, je Spalte ein Feld (`zahl` als Zahl) und
`neue_quellen` (Liste von Adressen, die der Teil über die genannten hinaus entdeckt hat; nur einzeilige http(s)-Adressen, sonst
lehnt `plan_record` den Eintrag ab, denn sie landen in den Job-Notizen). Mehrere Einträge je Position sind erlaubt; ein
identischer Aufruf (gleiche Position, gleiche Felder) wird nur einmal gespeichert. Abgelehnt wird mit einem Grund, der als Prompt-Text im Werkzeugergebnis steht, damit der Sub-Agent
nachbessert: unbekannte Position oder Status, ein Wert vom falschen Typ (auch Text in einer Zahlspalte; eine Zahl muss endlich sein, NaN und Unendlich gibt es nicht), eine unbekannte
Spalte, ein leerer Befund bei `nicht_gefunden` und `fehler`, bei `gefunden` eine fehlende Quelle oder eine Quelle, die in
diesem Lauf nicht abgerufen wurde.

**Abrufprüfung.** Jede `ToolKit`-Instanz führt eine Menge `read_urls`. Ein Werkzeug meldet gelesene Seiten, indem sein
Executor `{"read": [urls]}` liefert; die Tool-Schleife (`execute_streaming`) normalisiert sie mit `normalize_url` und
legt sie in die Menge, sie sind nie ein Ergebnis. Gelesene Seiten melden nur die Werkzeuge des Research-Plugins:
`web_fetch` meldet die abgerufene Adresse nach Erfolg, `web_search` die Adressen der Seiten, die es tatsächlich
ausgelesen hat. Bei einem Eintrag mit Status `gefunden` normalisiert `plan_record` die angegebene Quelle und vergleicht
sie mit der Menge. `normalize_url` lässt das Schema weg (http und https gelten gleich), schreibt den Host klein, entfernt
ein führendes `www.` und einen abschließenden Schrägstrich des Pfades und das Fragment, behält aber die Query, und es
schneidet das Präfix des Lese-Proxys `https://r.jina.ai/` ab, sodass die eingebettete Adresse zählt. Nicht gezählt wird,
was ein Werkzeug nicht meldet: Abrufe aus der Sandbox (ein Python-Skript, das eine Seite lädt) tauchen nie in der Menge
auf. Die Menge gehört zum Werkzeugkasten des Sub-Agenten; die Nachforderung nutzt denselben, bereits gelesene Seiten
bleiben also gültig.

## Ergebnis und Zählzeile

`PlanResult` hält den Plan und je Teil ein `PartOutcome`: Einträge und neue Quellen (`PartRecords`), den Bericht des
Sub-Agenten, den Grund eines Fehlschlags und die nicht geprüften Positionen. `new_sources` fasst die neuen Quellen aller
Teile zusammen.

`render_results(result, lang)` ist die eine Stelle für den Text an ein Modell (Chat und Horarium): je Teil seine Positionen
mit allen Einträgen (Status, Befund, Quelle, Spaltenwerte, Zahlen in der Schreibweise der Sprache), das Wort „nicht
geprüft“ bei einer Lücke, der Grund eines gescheiterten Teils, die Zusatzfunde, der Bericht und zum Schluss die neuen
Quellen. `coverage_line(result, lang)` ist die feste Zeile „N von M Positionen geprüft“ und bei einer Lücke eine Liste
„Teil / Position“, bei gescheiterten Teilen eine Liste mit Grund. Zusatzfunde zählen nicht mit; Positionen eines
gescheiterten Teils zählen als geprüft, soweit sie Einträge haben. Die Zeile fügt der Aufrufer ein, das Modell schreibt
sie nicht: Das Horarium hängt sie an die Nachricht an, das Chat-Werkzeug stellt sie dem Ergebnis voran (die Kappung eines
langen Werkzeugergebnisses schneidet den Schwanz ab, und der Hinweis auf nicht Geprüftes muss erhalten bleiben).

## Zwei Aufrufer

**Horarium.** `_run_plan_parts` in `lib/scheduler.py` ruft `run_plan` für einen Job mit `parts` auf: mit den Stufen des
Jobs ohne Senden und dem Notizen-Hinweis, trägt neue Quellen in die Job-Notizen ein und lässt anschließend den Abschluss mit den Ergebnissen laufen. Der ganze Lauf ist die Pipeline der Sitzung des Jobs (Warteschlange für Chat-Nachrichten, Stopp, „läuft“); scheitert der Abschluss, werden die Ergebnisse der Teile trotzdem zugestellt. Einzelheiten: [Scheduler](scheduler.md#jobs-mit-teilaufträgen).

**Chat-Werkzeug `run_task_plan`.** Es gehört dem Sub-Agent-Plugin, steht auf jedem Kanal zur Verfügung, der das Plugin
nutzt (Browser, Telegram, E-Mail, Discord, FreeEcho, alles, was durch `process_inbound` läuft), und nutzt die Stufen-
und Tiefen-Einstellungen des Plugins. Das Modell entscheidet wie bei `delegate_task`, ob ein Auftrag groß genug ist; die
Kriterien stehen in der Werkzeugbeschreibung. Der Chat-Aufruf hat keine Notizen: Das
Ergebnis ist `coverage_line`, gefolgt von `render_results`, als Werkzeugergebnis, auf das das Modell im laufenden Zug antwortet.
Das Werkzeug steht nur in der Whitelist (`tools` in `data/agents.json`) von AIfred; wer eine Whitelist hat und `run_task_plan`
nicht darin, bekommt es nicht (Sokrates, Salomo, Pater, Codine, Hal, Rabbi); Agenten ohne Whitelist sehen alle Werkzeuge,
die ihre Stufe zulässt (Filter in `agent_memory.py`). Siehe [Sub-Agenten](subagent-plugin.md).

## Bewusste Grenzen

- **Gebaut für die Web-Recherche.** „Gefunden“ verlangt als `quelle` eine Adresse, die der Teil in diesem Lauf mit `web_fetch`
  (oder über `web_search`) gelesen hat (`plan_record.py`). Andere Lesewerkzeuge, etwa Dokumente, Nextcloud oder E-Mail,
  melden keine gelesene Adresse (`read`); ein Fund aus ihnen lässt sich deshalb nicht als `gefunden` eintragen (auch nicht als Zusatzfund);
  übrig bleiben `nicht_gefunden` und `fehler`, deren Befund nur Text ist. Die Plan-Engine passt zu Aufträgen, deren Belege Webseiten
  sind (Preise, Verfügbarkeit, Quellenlage); für Aufträge über eigene Dokumente, Kalender oder Postfächer ist sie nicht
  gebaut. Das ist Design, kein Fehler.
- **Teile sehen einander nicht.** Innerhalb eines Laufs kennt jeder Teil nur Rahmen, seinen Auftrag und den Notizen-Hinweis; Absprachen zwischen Teilen gibt es nicht. Wer etwas gemeinsam braucht, schreibt es in den Rahmen.
- **Die Prüfung belegt den Abruf, nicht den Wert.** `plan_record` weiß, dass der Sub-Agent die Quelle gelesen hat, nicht,
  dass der eingetragene Wert dort steht. Ein erfundener oder falsch gelesener Wert zu einer echten Quelle fällt nicht auf.
- **Umleitungen werden nicht verfolgt.** Die Menge enthält die angegebene Adresse, nicht das Ziel einer Weiterleitung;
  trägt der Sub-Agent das Ziel ein, wird der Eintrag abgelehnt, bis er es abgerufen hat.
- **Keine Parallelität.** Die Teile laufen nacheinander (siehe auch „Bewusst nicht im Entwurf“ in
  [Sub-Agenten](subagent-plugin.md)).
- **Nur eine Nachforderung.** Was danach fehlt, wird gemeldet, nicht weiter verfolgt.
