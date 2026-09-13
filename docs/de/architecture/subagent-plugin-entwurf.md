# Entwurf: Sub-Agenten als Plugin

Stand 13.09.2026, zweite Fassung nach der Diskussion mit Peuqui. Kein Code,
bis die verbliebene Entscheidung am Ende getroffen ist.

## Ziel

Ein Hauptagent (AIfred, Codine, jeder Agent aus `data/agents.json`) kann
während seiner Tool-Schleife eine abgegrenzte Aufgabe an einen Sub-Agenten
delegieren. Der Sub-Agent ist ein frischer Modellaufruf mit eigenem
Kontextfenster, eigenem Werkzeugkasten und eigener Tool-Schleife. Zurück
kommt nur sein Abschlussbericht, als gewöhnliches Werkzeug-Ergebnis. Der
innere Ablauf bleibt dem Hauptmodell verborgen und wird dem User als
aufklappbarer Block in der Chat-Bubble gezeigt.

Das Ganze ist ein Plugin wie Calculator oder Workspace: ein Verzeichnis
unter `aifred/plugins/tools/`, ein- und ausschaltbar im Plugin-Modal, keine
Sonderarchitektur. Leitbild ist das Manager-Arbeiter-Muster aus dem
GVS5H-Paper: Planung oben, Teilaufgaben mit frischem Kontext unten,
gemeinsames Gedächtnis über Dateien im Workspace.

## Was AIfred schon hat und was der Entwurf wiederverwendet

| Baustein | Wo | Rolle im Entwurf |
|---|---|---|
| Plugin-Protokoll `ToolPlugin`, `PluginContext` | `aifred/lib/plugin_base.py` | Das Plugin ist ein gewöhnliches Tool-Plugin, Kontext liefert Agent, Sprache, Session, State, `max_tier`, `source` |
| Werkzeugkasten pro Agent `prepare_agent_toolkit` | `aifred/lib/agent_memory.py` | Baut den Werkzeugkasten des Sub-Agenten, inklusive Tier-Filter und Agent-Whitelist |
| Kompletter Agent-Turn ohne Reflex-State `call_llm` | `aifred/lib/llm_engine.py` | Führt den Sub-Agenten aus, mit `external_toolkit`, so wie Message Hub und Scheduler es heute tun |
| Tool-Schleife mit Guards | `aifred/backends/base.py`, `aifred/lib/function_calling.py` | Der Sub-Agent bekommt sie geschenkt: Kettentiefe, Schleifenbrecher, Kontext-Wächter, erzwungene Schlussrunde |
| Async-Generator-Executor mit `progress`-Ereignissen | `function_calling.py`, Präzedenz `research` | Das Delegations-Werkzeug streamt Fortschritt in die Statuszeile, ohne den Bericht zu blockieren |
| Aufklappbare Blöcke | `aifred/lib/formatting.py`, `llm_pipeline.PipelineResult` | Der innere Ablauf wird wie die Recherche-Quellen als HTML neben dem Text in die Bubble gehängt |
| Token-Schätzung ignoriert `<details>` | `aifred/lib/context_manager.py` | Der Ablauf-Block kostet in der History nichts und geht nie ans Modell |
| Prompt-Schichten und Plugin-Anleitungen | `aifred/lib/prompt_loader.py`, `plugin_base.load_plugin_instructions` | Der Sub-Agent bekommt einen schlanken Prompt aus Rahmen, Werkzeug-Anleitungen und `disciplines`; der Aufrufer bekommt eine Anleitung, wann Delegieren sinnvoll ist und was in die Übergabe gehört |
| Debug-Bus mit `session_scope` | `aifred/lib/debug_bus.py` | Debug-Zeilen des Sub-Agenten landen in derselben Session, ContextVar-basiert und damit verschachtelungssicher |

Neu gebaut wird nur das Plugin selbst, ein generischer Rahmen-Prompt in
beiden Sprachen und eine kleine, generische Erweiterung der Pipeline, damit
ein Werkzeug einen aufklappbaren Block liefern kann.

## Das Plugin

Verzeichnis `aifred/plugins/tools/subagent/`, Name `subagent`, ein Werkzeug
`delegate_task`.

**Parameter des Werkzeugs**

| Parameter | Pflicht | Bedeutung |
|---|---|---|
| `task` | ja | Die vollständige Aufgabe mit allem Kontext, den der Sub-Agent braucht. Er sieht weder die Konversation noch das Gedächtnis. |
| `expected_result` | ja | Was der Bericht enthalten soll, damit der Aufrufer weiterarbeiten kann |
| `agent` | nein | Nur wenn die Plugin-Einstellung „Delegation an andere Agenten“ an ist: Agent-ID, deren Modell, Tuning und Werkzeugliste der Sub-Agent bekommt, weiterhin ohne Persona. Die erlaubten Werte werden bei jedem Turn beim Bau des Werkzeugkastens abgeleitet: nur Agenten, die sich vom Aufrufer in Modell oder Whitelist unterscheiden. Gibt es keinen, fehlt der Parameter im Schema. Vorgabe: der Aufrufer selbst |

Ohne Persona unterscheidet einen Sub-Agenten „nach Codine“ von einem „nach
AIfred“ nur noch Modell, Tuning und Werkzeugliste. Sinn hat der Parameter
also genau dann, wenn Agenten verschiedene Modelle fahren, etwa Codine auf
einem Coder-Modell, und AIfred eine Programmieraufgabe dorthin abgeben soll,
ohne dass der User den Agenten wechselt. Das ist in AIfred möglich, deshalb
ist der Parameter da, aber hinter einem Schalter mit Vorgabe aus: Ist der
Schalter aus, steht `agent` nicht im Werkzeug-Schema, und das Modell kann
ihn gar nicht erst benutzen.

Ruft AIfred so Codine, wird Codine wie jeder andere Sub-Agent behandelt:
ohne Persona, ohne Konversation und Gedächtnis, nur mit Modell, Tuning und
Werkzeugliste. Im Chat erscheint keine Codine-Bubble und kein Wortwechsel,
AIfred bleibt das Gegenüber des Users, Codines Bericht geht als
Werkzeug-Ergebnis an AIfred und ihr Transkript in den aufklappbaren Block
von AIfreds Antwort. Das ist Arbeitsteilung im Hintergrund und bewusst
nicht das Symposion, in dem Personas als eigene Gesprächsteilnehmer mit
eigenen Bubbles diskutieren.

Tier des Werkzeugs: `TIER_READONLY`. Der Werkzeugkasten des Sub-Agenten
erbt Quelle `source` und Obergrenze `max_tier` des Aufrufers und wird
zusätzlich durch die Plugin-Einstellung „erlaubte Tiers“ gefiltert (siehe
unten). Ein Telegram-Kanal, der heute keine Schreib-Werkzeuge bekommt,
bekommt sie auch über einen Sub-Agenten nicht.

**Ablauf eines Aufrufs**

1. Der Executor baut den Werkzeugkasten des Sub-Agenten über
   `prepare_agent_toolkit` mit der Agent-ID des Aufrufers (oder, bei
   eingeschaltetem Schalter und gesetztem `agent`, der des gewählten
   Agenten), Memory aus. Aus
   dem Kasten fliegen `delegate_task` und alle Werkzeuge, deren Tier nicht
   in der Plugin-Einstellung „erlaubte Tiers“ steht. Ohne `delegate_task`
   ist die Rekursionstiefe strukturell 1, ohne Zähler und ohne Lock. Die
   Einstellung „Rekursionstiefe“ (Vorgabe 1) erlaubt später mehr; die
   aktuelle Tiefe wandert dann als Feld in `PluginContext.metadata`.
2. System-Prompt des Sub-Agenten, bewusst schlank und ohne Persona: der
   Rahmen aus `prompts/<lang>/shared/subagent_frame.txt`, die
   Werkzeug-Anleitungen der Plugins, die er tatsächlich bekommt, und die
   geteilte Schicht `disciplines`. Keine Identity, keine Personality, kein
   Reminder, kein Memory-Kontext. Der Rahmen sagt: du bist ein Sub-Agent,
   du hast keinen Zugriff auf Gespräch und Gedächtnis, du stellst keine
   Rückfragen, du lieferst am Ende einen Bericht mit genau dem, was
   `expected_result` verlangt, und du delegierst nicht weiter.
3. User-Nachricht des Sub-Agenten: `task`. History: leer. Das ist der Kern
   der Kontextentlastung, und es zwingt den Aufrufer zu einer vollständigen
   Übergabe. Die Anleitung an den Aufrufer (`_intro.txt`) sagt genau das.
4. Ausführung über `call_llm` mit `external_toolkit`, Modell, Sampling und
   Thinking aus dem Tuning des Aufrufers beziehungsweise des gewählten
   Agenten, wie bei jedem seiner Turns.
5. Während der Ausführung yieldet der Executor `progress`-Ereignisse: welche
   Werkzeuge der Sub-Agent gerade ruft. Die Statuszeile zeigt also
   „Codine (Sub-Agent) ruft read_file auf“, nicht den inneren Text.
6. Ergebnis: der Abschlusstext des Sub-Agenten, durch `cap_tool_output`
   auf das bestehende Werkzeug-Ausgabebudget gekappt. Er geht als
   `role=tool`-Nachricht an das Hauptmodell, das damit weiterdenkt. Keine
   Rekursion, kein Rücksprung: die Tool-Schleife wartet auf das Ergebnis
   wie bei jedem anderen Werkzeug.
7. Das vollständige Transkript des Sub-Agenten, also sein Denken, sein
   Text, jeder Werkzeugaufruf mit Argumenten und vollständigem Ergebnis und
   der Bericht, wird als aufklappbarer Block geliefert (siehe unten). Nichts
   davon läuft unter dem Radar; der Preis ist Größe in der Session-Datei,
   nicht im Kontext.

**Budget.** Keine neue Budgetierung und kein eigener Schalter für die
Kappung. Der Sub-Agent hat seinen eigenen `ToolKit` und damit automatisch
eigene Kettentiefe und eigenen Schleifenbrecher. Sein Kontextfenster ist die
harte Grenze, der Kontext-Wächter des Backends greift wie bei jedem Turn.
Der Bericht läuft beim Aufrufer durch die bestehende Kappung, die nur
greift, wenn er den freien Kontext des Hauptmodells sprengen würde. Beim
Aufrufer zählt die Delegation als ein Werkzeugaufruf.

**Wer delegieren darf.** Wie jedes Werkzeug muss `delegate_task` in der
Tool-Liste des Agenten in `agents.json` stehen. Kein Sonderfall im Code,
auch nicht für Sokrates und Salomo; die sollen ohnehin einmal wie alle
anderen Agenten behandelt werden.

**Plugin-Einstellungen**, gespeichert in der `settings.json` des Plugins und
über das Zahnrad im Plugin-Modal erreichbar:

| Einstellung | Vorgabe | Bedeutung |
|---|---|---|
| Erlaubte Tiers | 0 und 2 | Lesen, Recherche, Dateien schreiben, Sandbox. Nicht 1: kein Senden von E-Mail, Telegram, Discord. Senden bleibt beim Hauptagenten, der es im Gespräch verantwortet. Nicht 3: kein Löschen. |
| Rekursionstiefe | 1 | Ob ein Sub-Agent selbst delegieren darf |
| Delegation an andere Agenten | aus | Schaltet den Parameter `agent` frei. Kein Automatismus: Der Schalter hängt nicht davon ab, ob ein anderer Agent ein anderes Modell fährt; die Nutzlosigkeit einer Option wird stattdessen pro Turn im Schema vermieden (siehe `agent`). Bewusst zu treffen, weil ein anderes Modell über llama-swap einen Modellwechsel je Delegation bedeutet, Minuten pro Aufruf. Die Anleitung an den Aufrufer nennt diese Kosten. |

Memory ist für Sub-Agenten aus und keine Einstellung: Alles, was der
Sub-Agent wissen muss, kommt in der Übergabe vom Hauptagenten, der das
Gespräch und das Gedächtnis hat. Ein Sub-Agent, der Erinnerungen anlegt,
würde Dinge festschreiben, die niemand im Gespräch gesehen hat.

## Der aufklappbare Block

Heute kommen aufklappbare Blöcke auf drei Wegen in die Bubble: das Modell
schreibt einen Tag, die Pipeline injiziert einen Tag, oder ein HTML-Feld
liegt neben dem Text in `PipelineResult`. Der dritte Weg passt, er ist aber
heute auf Quellen und Sandbox beschränkt.

Vorschlag, generisch und klein: Ein Werkzeug darf im Ereignisstrom neben
`progress` und `result` ein Ereignis `collapsible` mit Titel und Inhalt
liefern. `llm_pipeline` sammelt diese Ereignisse in ein neues Feld
`PipelineResult.tool_collapsibles`, und `multi_agent` rendert sie mit einem
Helfer in `formatting.py` neben Quellen und Sandbox in die Bubble. Der Block
ist ein `<details>`, wird von der Token-Schätzung ignoriert, nicht
vorgelesen und nie ans Modell geschickt.

Damit hat jedes künftige Werkzeug denselben Weg, einen Ablauf zu zeigen.
Über den Message Hub (Telegram, E-Mail) fällt der Block weg, dort gibt es
keine Bubble; der Ablauf steht dann im Debug-Log der Session.

## Was Codine daraus macht

Codine ist heute ein normaler Agent mit 37 Werkzeugen. Sie wird nicht
umgebaut. Bekommt sie `delegate_task` in ihre Whitelist, kann sie eine
Aufgabe zerlegen, Teilaufgaben an Arbeiter mit frischem Kontext und ihrer
eigenen Werkzeugliste geben, das Ergebnis in Workspace-Dateien halten und
mit der Sandbox prüfen. Das ist das Manager-Arbeiter-Muster aus dem Paper,
ohne dass Codine ein neuer Agententyp wird.

## Dateien

| Datei | Neu oder Änderung |
|---|---|
| `aifred/plugins/tools/subagent/__init__.py` | neu, Plugin und Executor |
| `aifred/plugins/tools/subagent/prompts/tools/delegate_task.txt` | neu, Werkzeugbeschreibung für das Modell |
| `aifred/plugins/tools/subagent/prompts/de/_intro.txt`, `en/_intro.txt` | neu, Anleitung an den Aufrufer: wann delegieren, was in `task` gehört |
| `prompts/de/shared/subagent_frame.txt`, `prompts/en/shared/subagent_frame.txt` | neu, Rahmen für den Sub-Agenten |
| `aifred/plugins/tools/subagent/settings.json` | neu, erlaubte Tiers, Rekursionstiefe, Delegation an andere Agenten |
| `aifred/lib/llm_pipeline.py` | Ereignis `collapsible` sammeln, Feld `tool_collapsibles` |
| `aifred/lib/formatting.py` | Helfer, der `tool_collapsibles` als `<details>` rendert |
| `aifred/lib/multi_agent.py` | Block neben Quellen und Sandbox einhängen |
| `data/agents.json` | `delegate_task` in die Whitelist der gewünschten Agenten |
| `tests/test_subagent_plugin.py` | neu: Name gleich Ordnername, Werkzeug und Tier, Rekursionsfilter, Tier-Filter aus der Einstellung, Vererbung von `max_tier` und `source`, Memory aus, Ergebnis als Bericht, Block-Ereignis; `call_llm` gemockt |
| `docs/de/guides/plugins/subagent.md` | neu, Nutzerdoku |

## Bewusst nicht im Entwurf

- Keine parallelen Sub-Agenten. Auf Single-User-Hardware, mit einer
  llama.cpp-Instanz oder knappem KV-Speicher unter vLLM, bringt Parallelität
  nichts; die Tool-Schleife arbeitet ohnehin nacheinander.
- Keine Zeitgrenze nach der Wanduhr. Kettentiefe und Kontextfenster
  begrenzen den Sub-Agenten. Das vorhandene Lebenszeichen der Tool-Schleife,
  alle zwanzig Sekunden während eines stillen Werkzeugaufrufs, deckt auch
  lange Delegationen ab; nichts Neues nötig.
- Keine Weitergabe von Konversation oder Gedächtnis. Der Aufrufer packt,
  was nötig ist, in `task`.
- Kein Weg, den inneren Ablauf ans Hauptmodell zu geben. Es sieht den
  Bericht, der User sieht alles.
- Kein Schalter für die Kappung, keine Memory-Einstellung (Begründungen
  oben).

## Entschieden (13.09.)

- Jeder Agent, dem `delegate_task` eingetragen wird, darf delegieren; kein
  Sonderfall im Code.
- Sub-Agent ohne Persona, ohne Gedächtnis; Modell und Werkzeuge des
  Aufrufers, oder per Schalter die eines anderen Agenten (`agent`).
- Rekursionstiefe konfigurierbar, Vorgabe 1.
- Aufklappbarer Block mit dem vollständigen Transkript.
- Keine parallele Delegation, kein Kappungs-Schalter.

## Noch offen

1. Die Vorgabe der erlaubten Tiers: 0 und 2 wie vorgeschlagen?
