# Sub-Agenten Plugin

**Datei:** `aifred/plugins/tools/subagent/`

Ein Hauptagent (AIfred, Codine, jeder Agent aus `data/agents.json`) kann während
seiner Tool-Schleife eine abgegrenzte Aufgabe an einen Sub-Agenten delegieren.
Der Sub-Agent ist ein frischer Modellaufruf mit eigenem Kontextfenster, eigenem
Werkzeugkasten und eigener Tool-Schleife. Er hat keine Persona, sieht weder das
Gespräch noch das Gedächtnis des Aufrufers und kann nicht nachfragen. Zurück
kommt nur sein Bericht, als gewöhnliches Werkzeug-Ergebnis. Das vollständige
Transkript des Sub-Agenten, also Denken, Text, jeder Werkzeugaufruf mit
Argumenten und Ergebnis und der Bericht, erscheint als aufklappbarer Block in
der Chat-Bubble des Hauptagenten. Es geht nie an das Modell und kostet in der
History keine Token.

Architektur und Entscheidungen: [Sub-Agenten als Plugin](../../architecture/subagent-plugin.md).

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `delegate_task` | Teilaufgabe an einen Sub-Agenten geben; Ergebnis ist dessen Bericht | READONLY |

### Parameter (`delegate_task`)

| Parameter | Typ | Pflicht | Beschreibung |
|-----------|-----|---------|--------------|
| `task` | string | ja | Die vollständige, in sich geschlossene Aufgabe: alles, was der Sub-Agent wissen muss |
| `expected_result` | string | ja | Was der Bericht enthalten soll, damit der Aufrufer weiterarbeiten kann |
| `agent` | string | nein | Nur wenn die Einstellung „Sub-Agent als anderer Hauptagent“ an ist: Agent, dessen Modell und Werkzeugliste der Sub-Agent bekommt. Angeboten werden nur Agenten, die sich vom Aufrufer in Modell oder Werkzeugliste unterscheiden; gibt es keinen, fehlt der Parameter. Systemagenten (Kalibrierung, Vision) werden nie angeboten. Die Parameter-Beschreibung listet je Agent die Werkzeug-Gruppen, die sein Sub-Agent hätte (Plugins nach Whitelist und erlaubten Tiers), plus je Gruppe die Plugin-Beschreibung aus der `i18n.json` des Plugins in der Sprache des Turns |

Das Werkzeug selbst hat Tier READONLY. Was der Sub-Agent tun darf, bestimmen
die Obergrenze des Aufrufers (Quelle und `max_tier`, ein Telegram-Kanal ohne
Schreibrechte bekommt sie auch über einen Sub-Agenten nicht) und die
Plugin-Einstellung „erlaubte Tiers“.

## Wer delegieren darf

Wie jedes Werkzeug muss `delegate_task` in der Tool-Liste des Agenten in
`data/agents.json` stehen. Agenten ohne Whitelist (wie AIfred) haben es
automatisch, Codine hat es eingetragen. Sokrates und Salomo nicht.

## Einstellungen (Zahnrad im Plugin-Modal, `settings.json` beim Plugin)

| Einstellung | Schlüssel | Vorgabe | Bedeutung |
|---|---|---|---|
| Erlaubte Tiers | `SUBAGENT_ALLOWED_TIERS` | `0+2` | Lesen, Recherche, Dateien schreiben, Sandbox. Kein Senden (Tier 1), kein Löschen (Tier 3). Senden bleibt beim Hauptagenten, der es im Gespräch verantwortet |
| Rekursionstiefe | `SUBAGENT_MAX_DEPTH` | `1` | Ob ein Sub-Agent selbst delegieren darf. Bei 1 bekommt der Sub-Agent das Werkzeug gar nicht erst, die Grenze ist strukturell |
| Sub-Agent als anderer Hauptagent | `SUBAGENT_DELEGATE_TO_OTHER_AGENTS` | aus | Schaltet den Parameter `agent` frei: Der Sub-Agent läuft dann mit Modell und Werkzeugliste eines anderen Hauptagenten (z. B. Codine), weiterhin ohne Persona. Ein anderes Modell bedeutet über llama-swap einen Modellwechsel je Delegation, oft Minuten. Der Schalter ist bewusst kein Automatismus |

Memory ist für Sub-Agenten aus und keine Einstellung: Alles, was der Sub-Agent
wissen muss, kommt in der Übergabe vom Hauptagenten. Ein Sub-Agent, der
Erinnerungen anlegt, würde Dinge festschreiben, die niemand im Gespräch gesehen
hat.

## Was der Sub-Agent bekommt

- **System-Prompt**, bewusst schlank: der Rahmen aus `prompts/<lang>/shared/subagent_frame.txt`,
  die Werkzeug-Anleitungen genau der Plugins, deren Werkzeuge er hält, die
  Sicherheitsgrenze bei externen Kanälen und die geteilte Schicht `disciplines`.
  Keine Identity, keine Personality, kein Reminder, kein Memory-Kontext.
- **User-Nachricht**: `task` und `expected_result` aus `prompts/<lang>/shared/subagent_task.txt`. History: leer.
- **Modell, Sampling, Thinking und Kontextgröße** des Aufrufers, oder des per
  `agent` gewählten Agenten, über dieselben Helfer wie ein normaler Turn.
- **Werkzeugkasten**: der normale Kasten des Agenten über die Werkzeug-Fabrik,
  Memory aus, gefiltert auf die erlaubten Tiers, ohne `delegate_task` an der
  letzten erlaubten Tiefe. Eigene Kettentiefe und eigener Schleifenbrecher, ein
  eigenes Ausgabebudget für Werkzeug-Ergebnisse, der Kontext-Wächter des
  Backends wie bei jedem Turn.

## Was der Hauptagent bekommt

Nur den Bericht, als `role=tool`-Nachricht. Er läuft durch dieselbe Kappung wie
jedes Werkzeug-Ergebnis, die nur greift, wenn er den freien Kontext des
Hauptmodells sprengen würde. Beim Aufrufer zählt die Delegation als ein
Werkzeugaufruf.

## Was der User sieht

Während der Sub-Agent arbeitet, zeigt die Statuszeile, welches Werkzeug er
gerade ruft. Danach steht das Transkript als aufklappbarer Block in der Bubble
des Hauptagenten, neben Denkprozess und Quellen. Über den Message Hub
(Telegram, E-Mail) gibt es keine Bubble; dort steht der Ablauf im Debug-Log der
Session.

## Bewusst nicht enthalten

- Keine parallelen Sub-Agenten: Auf Single-User-Hardware bringt Parallelität
  nichts, die Tool-Schleife arbeitet nacheinander.
- Keine Zeitgrenze nach der Wanduhr: Kettentiefe und Kontextfenster begrenzen
  den Sub-Agenten, das Lebenszeichen der Tool-Schleife hält lange Delegationen
  am Leben.
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
