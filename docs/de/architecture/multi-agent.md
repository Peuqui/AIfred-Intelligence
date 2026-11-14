# Multi-Agent-System & Diskussionsmodi

> **English version:** [multi-agent.md](../../en/architecture/multi-agent.md)

Wie AIfreds Agenten zusammenarbeiten: die eingebauten und eigenen Agenten, die
sechs Diskussionsmodi, wie jeder Agent die Konversation sieht und welche
Prompt-Dateien welchen Schritt steuern.

Zum Delegieren abgegrenzter Aufgaben an einen frischen Modellaufruf siehe das
[Sub-Agenten-Plugin](../guides/plugins/subagent.md) und seine
[Architektur-Notizen](subagent-plugin.md).

---

## Agenten

| Agent | Rolle |
|---|---|
| 🎩 **AIfred** | Hauptagent — Butler & Gelehrter, beantwortet Fragen (britischer Butler-Stil) |
| 🏛️ **Sokrates** | Kritiker — hinterfragt und fordert heraus mit der sokratischen Methode |
| 👑 **Salomo** | Richter — wägt Argumente ab, synthetisiert, fällt das Urteil |
| 📷 **Vision** | Bildanalyst — OCR und visuelle Q&A; Systemagent ohne Persona, seine Prompts sind die Dateien unter `prompts/<lang>/vision/` |
| 🔥 **Pater Tuck**, 🤖 **Codine**, 🔴 **HAL 9000**, 🕎 **Rabbi Shmuel** | Eigene Agenten, als Beispiele in `data/agents.json` mitgeliefert (Codine: Code- + Sandbox-Arbeit) |
| ➕ **Deine eigenen** | Im Agenten-Editor angelegt — Name, Emoji, Rolle, zweisprachige Prompts, eigenes Gedächtnis |

Alle Agenten — eingebaut oder eigene — sind gleichberechtigt: Jeder hat seine
eigene Zeile in der `agent_tuning`-Map (Modell, Sampling, Thinking, Reasoning
Effort, Kontextgröße, TTS-Stimme). Ein neuer Agent bekommt automatisch seine
Settings-Zeile, Sampling-Zeile und Kontext-Spalte in der UI; keine
Code-Änderungen, keine hardcodierten Agenten-Listen.

**Anpassen:**
- Alle Prompts sind reine Textdateien in `prompts/de/` und `prompts/en/`
- Agenten-Definitionen liegen in `data/agents.json` (Prompt-Pfade, Toggles, Rollen)
- **Agenten-Editor** (Einstellungs-Modal): Agenten anlegen, bearbeiten, löschen; DE/EN-Prompt-Bearbeitung; Emoji-Auswahl; Tool-Pills mit Tier-Badges (T0–T4)
- **Memory-Browser**: das ChromaDB-Gedächtnis jedes Agenten inspizieren und aufräumen
- Persönlichkeit lässt sich pro Agent ausschalten — die Identität bleibt, der Sprechstil geht
- Agenten antworten in der Sprache des Users (deutsche Prompts für Deutsch, englische Prompts für jede andere Sprache)

---

## Prompt-Schichten

`agent_system_prompt()` in `aifred/lib/prompt_loader.py` ist der eine Bauer für
jeden System-Prompt (Direktweg, Kanäle, Debattenplätze, Symposion, Sub-Agent);
`_merge_prompt_layers()` setzt ihn aus bis zu zwölf Schichten zusammen, in dieser Reihenfolge:

| # | Schicht | Wann |
|---|---|---|
| 0 | User-Präfix (`shared/user_prefix`) | wenn die Antwort an den Benutzer geht und ein Name gesetzt ist (einmal, ganz oben) |
| 1 | Identität — wer der Agent ist | immer (nicht beim Sub-Agenten ohne Persona) |
| 2 | Reasoning — wie gedacht wird | wenn Reasoning für den Agenten an ist |
| 3 | Multi-Agent-Rollen — wer die anderen sind | nur in Multi-Agent-Modi (mit Plätzen) |
| 4 | Situation — was der Agent hier tut | immer |
| 5 | Ausgabeformat | wenn die Antwort an den Benutzer geht |
| 6 | Formatregeln | wenn die Antwort an den Benutzer geht und der Zustellkanal Markdown zeigt (nicht FreeEcho.2, Telegram) |
| 7 | Security Boundary | nur bei einer Nachricht, die nicht vom Eigentümer ist (Fremde, Webhook-Inhalt) |
| 8 | Gedächtnis-Anweisungen | wenn das Agenten-Gedächtnis aktiv ist (nicht im Inkognito-Modus) und der Aufruf `store_memory` hat |
| 9 | Persönlichkeit — wie gesprochen wird | wenn Persönlichkeit an ist (nicht beim Sub-Agenten ohne Persona) |
| 10 | Tool-Anweisungen (+ Ankündigung der Tool-Aufrufe) | für die Werkzeuge, die der Aufruf wirklich hat (nahe am Ende, damit Tool-Nutzung priorisiert wird); die Ankündigung nur bei einer Antwort an den Benutzer |
| 11 | Disziplinen — Datums-Verankerung, Zitat-/Währungsdisziplin, Termine, … und Rückfrage | immer, ganz zuletzt; die Rückfrage nur bei einer Antwort an den Benutzer |

Die Situation (Schicht 4) ist die einzige Schicht, die vom Weg abhängt:
`shared/conversation_roles` (Direktweg und Kanäle), die Aufgabe des Platzes
(`debate/*`), `shared/symposion`, `shared/subagent_frame` oder die eigene
Situation eines Kanals (`BaseChannel.situation()`; AI-Connect antwortet einem
anderen Assistenten, nicht dem Benutzer). Die Ausgabesprache steht nur in
`shared/output_language` und wird mit der Persönlichkeits-Erinnerung an jedem
Platz in die letzte Nutzernachricht gesetzt (Erinnerung davor, Ausgabesprache dahinter).

Aktuelles Datum und Uhrzeit sind Teil jedes Prompts, damit zeitbezogene Fragen
funktionieren.

---

## Diskussionsmodi

| Modus | Ablauf | Wer entscheidet? |
|---|---|---|
| **Standard** | Ein Agent antwortet (ausgewählt über die Agenten-Pills) | — |
| **Kritische Prüfung** | AIfred → Sokrates (Kritik + Pro/Contra) → Stopp | Du |
| **Recensio** (Code-Prüfung) | Auctor → Censor liest und testet die geänderten Dateien → je Runde Correctura des Auctors und erneute Prüfung, bis zu N Runden | Censor (`[LGTM]`) |
| **Auto-Konsens** | AIfred → Sokrates → Salomo, bis zu N Runden | Salomo (Abstimmung) |
| **Tribunal** | AIfred ↔ Sokrates für N Runden → Salomo | Salomo (Urteil) |
| **Symposion** | 2+ frei gewählte Agenten diskutieren N Runden, Reflection-Layer ab Runde 2 | Niemand — mehrere Perspektiven |

Runden: 1–10 (Standard 3), einstellbar im Einstellungs-Panel. Das 💡-Icon öffnet
ein Modal mit einer Übersicht aller Modi.

### Auto-Konsens

```
┌─────────────┐     ┌──────────────────┐     ┌────────────────────┐
│   User      │────▶│   🎩 AIfred      │────▶│   🏛️ Sokrates       │
│   Frage     │     │   + [LGTM/WEITER]│     │   + [LGTM/WEITER]  │
└─────────────┘     └──────────────────┘     └─────────┬──────────┘
                                                       │
                              ┌────────────────────────┘
                              ▼
                    ┌─────────────────────┐
                    │   👑 Salomo         │
                    │   + [LGTM/WEITER]   │
                    └──────────┬──────────┘
                               │
               ┌───────────────┴───────────────┐
               ▼                               ▼
     ┌────────────────┐              ┌─────────────────┐
     │ Mehrheit/alle  │              │ Zu wenig Stimmen│
     │ = Konsens      │              │ = nächste Runde │
     └────────────────┘              └─────────────────┘
```

Konsens-Art (Einstellungen): **Mehrheit** (mehr als die Hälfte stimmt mit `[LGTM]`) oder
**einstimmig** (alle). In Runde 1 stimmen nur Kritiker und Richter ab: Die erste Antwort
des Verfassers wurde nie zur Abstimmung aufgefordert; ab seiner ersten Überarbeitung stimmt
er mit. Bei zwei Stimmen braucht auch die Mehrheit beide.

### Recensio (Code-Prüfung)

Der Auctor (Verfasser) antwortet, der Censor (Prüfer, der Kritiker-Platz) prüft danach seine Arbeit: Er
bekommt die Liste der Dateien, die der Auctor in diesem Zug in den Dokumenten angelegt, geändert oder gelöscht
hat, liest sie, testet sie in der Sandbox und meldet jeden Befund mit Stelle und Beleg, ohne Höchstzahl und
ohne eigene Gegenfassung. Bei `[WEITER]` folgt eine Runde: Der Auctor behebt die Befunde in den Dateien
(Correctura), dann prüft der Censor erneut. `[LGTM]` beendet die Schleife, sonst endet sie nach N Runden mit
der letzten Prüfung; die erste Prüfung der Antwort gehört zu keiner Runde.

Der Censor ändert nichts: Von den Werkzeugen seines Agenten behält er nur die zum Lesen und Testen
(`RECENSIO_CENSOR_TOOLS` in `config.py`), und nur deren Anleitungen stehen in seinem Prompt. Seine Prüfaufgabe
ersetzt dort die allgemeine Aufgabe seines Agenten, die Rollen beschreibt `shared/multi_agent_roles_review`
statt der Dialektik; Identität und Persönlichkeit bleiben.

Welche Dateien ein Agent geändert hat, weiß sein Werkzeugkasten: Jeder schreibende Werkzeugaufruf wird von
einem Blick auf den Dokumentenordner vorher und nachher umklammert (`lib/document_changes.py`,
`ToolKit.changed_documents`). So zählt auch, was `execute_code_write` per Code schreibt.

### Tribunal

```
┌─────────────┐     ┌─────────────────────────────────────┐
│   User      │────▶│   🎩 AIfred ↔ 🏛️ Sokrates           │
│   Frage     │     │   Debatte über N Runden             │
└─────────────┘     └──────────────────┬──────────────────┘
                                       │
                                       ▼
                    ┌─────────────────────────────────────┐
                    │   👑 Salomo — finales Urteil        │
                    │   wägt beide Seiten, entscheidet    │
                    └─────────────────────────────────────┘
```

### Symposion-Reflection

Ab Runde 2 wird jeder Teilnehmer zusätzlich gefragt: *Welche Aspekte der
ursprünglichen Frage sind noch unbeantwortet, welche Perspektive wurde
übersehen, welche Annahme ist unhinterfragt geblieben?* Das erzeugt Tiefe, ohne
Konfrontation zu erzwingen — Agenten adressieren Lücken, statt ihre Sicht zu
wiederholen. Sie steht als Frage hinter dem Verlauf
(`prompts/{de,en}/shared/symposion_reflection.txt`), kein Ersatz für die Regeln
des Symposions (`shared/symposion.txt`, die Situation im System-Prompt).

### Prompt-Dateien pro Schritt

| Modus / Schritt | Agent | Prompt |
|---|---|---|
| Standard, direkte Ansprache, Kanal | antwortender Agent | `shared/conversation_roles` (die eigenen Dateien des Agenten sind nur `identity`, `personality`, `reminder`, `memory_context`) |
| Kritische Prüfung, Auto-Konsens — Kritik | Kritiker | `debate/critic` (+ `critic_first`/`critic_later`, Abstimmung `critic_voting` nur im Auto-Konsens) |
| Recensio — Prüfung | Censor (Kritiker-Platz) | `debate/review` (+ `review_first`/`review_later`) |
| Recensio — Correctura | Auctor | `debate/review_fix` |
| Auto-Konsens — Überarbeitung | Verfasser | `debate/refinement` |
| Auto-Konsens — Synthese | Richter | `debate/mediator` (+ `mediator_first`/`mediator_later`) |
| Tribunal — Angriff | Kritiker | `debate/tribunal` (+ `tribunal_first`/`tribunal_later`) |
| Tribunal — Verteidigung | Verfasser | `debate/defense` |
| Tribunal — Urteil | Richter | `debate/judge` |
| Symposion | jeder Teilnehmer | `shared/symposion` (+ `shared/symposion_reflection` ab Runde 2) |

Was die Aufgabe eines Platzes zu seiner Runde sagt, wählt der Code (`debate_seats.round_rules`: der Baustein
`_first` für die erste Runde, `_later` für jede weitere), damit das Modell nie selbst „WENN Runde = 1“ abwägt.
Die Rollen der Plätze beschreibt je Modus eine eigene Datei (`turn_debate._DEBATE_MODES`): `shared/multi_agent_roles` (Auto-Konsens, Tribunal), `shared/multi_agent_roles_critique` (Kritische Prüfung: zwei Rollen, kein Richter), `shared/multi_agent_roles_review` (Recensio).
Kritiker sind angewiesen, pro Runde höchstens ein oder zwei Punkte anzubringen (die Recensio nicht: sie
meldet alles, was sie belegen kann).

---

## Wie jeder Agent die Konversation sieht

Multi-Agent-Nachrichten werden einmal in `llm_history` mit Speaker-Labels
gespeichert. Wird ein Agent aufgerufen, wird die History aus **seiner**
Perspektive projiziert: Seine eigenen Nachrichten werden zu `assistant`, die
aller anderen zu `user` mit Label. So verwechseln Agenten die Worte eines
anderen Agenten nicht mit ihren eigenen.

```
┌─────────────────────────────────────────┐
│          llm_history (gespeichert)      │
│  [AIFRED]: "Antwort 1"                  │
│  [SOKRATES]: "Kritik"                   │
│  [AIFRED]: "Antwort 2"                  │
└─────────────────────────────────────────┘
                    │
    ┌───────────────┼───────────────┐
    ▼               ▼               ▼
┌─────────┐   ┌──────────┐   ┌─────────┐
│ AIfred  │   │ Sokrates │   │ Salomo  │
├─────────┤   ├──────────┤   ├─────────┤
│assistant│   │  user    │   │  user   │
│"Antw 1" │   │[AIFRED]: │   │[AIFRED]:│
│  user   │   │assistant │   │  user   │
│[SOKR].. │   │"Kritik"  │   │[SOKR].. │
│assistant│   │  user    │   │  user   │
│"Antw 2" │   │[AIFRED]: │   │[AIFRED]:│
└─────────┘   └──────────┘   └─────────┘

Eine Quelle, eine Sicht pro Sprecher.
Eigene Nachrichten = assistant (ohne Label), andere = user (mit Label).
```

---

## Agenten ansprechen

- Per Name, irgendwo im Satz: *„Sokrates, was denkst du über …?"*,
  *„Gut erklärt. Sokrates."* — der angesprochene Agent antwortet direkt.
- Eigene Agenten per ID oder Anzeigename (erkannt von der Intent-Erkennung).
- STT-Varianten werden erkannt („Alfred", „Eifred", „AI Fred").
- Funktioniert aus jedem Kanal: Browser, FreeEcho.2, Telegram, Discord, E-Mail.
- **Aktiver-Agent-Pills** wählen, wer im Standard-Modus antwortet; ein
  dauerhafter Wechsel („Ich möchte mit Pater Tuck weiterreden") wird auch in
  Sprache erkannt.

### Moduswechsel per Sprache oder Text

Die Intent-Erkennung läuft vor jeder Nachricht und erkennt in jeder Sprache
auch Modus- und Recherche-Wechsel — *„Start tribunal and discuss climate
change"*, *„Schalt auf Tiefrecherche"*, *„Nur Sokrates soll antworten"*. Sie
liefert die neuen Einstellungen plus ein `IS_PURE_COMMAND`-Flag; die Nachricht
selbst wird nie umgeschrieben. Ein reiner Befehl wird bestätigt; ein Befehl
zusammen mit einer Frage schaltet um und beantwortet die Frage im neuen Modus.

---

## Anzeige

Jede Nachricht wird einzeln mit dem Emoji des Agenten angezeigt;
Multi-Agent-Schritte tragen ein Label `[<Modus>: <Schritt> R<Runde>]` (gebaut in
`_chat_mixin.py`):

| Beispiel | Bedeutung |
|---|---|
| 🎩 AIfred | Standard-Antwort oder direkt angesprochener Agent (kein Label) |
| 🏛️ Sokrates [Kritische Prüfung] | Kritik in der Kritischen Prüfung |
| 🔴 HAL 9000 [Recensio] | Prüfung der Antwort |
| 🤖 Codine [Recensio: Correctura R1] | Korrektur, Runde 1 |
| 🔴 HAL 9000 [Recensio R1] | Nachprüfung, Runde 1 |
| 🎩 AIfred [Auto-Konsens: Überarbeitung R2] | Überarbeitung, Runde 2 |
| 👑 Salomo [Auto-Konsens: Synthese R2] | Synthese, Runde 2 |
| 🏛️ Sokrates [Tribunal R1] | Tribunal-Austausch, Runde 1 |
| 👑 Salomo [Tribunal: Urteil R3] | Finales Urteil |
| 📊 Zusammenfassung #1 (5 Nachrichten) | Ausklappbare Zusammenfassung der History-Kompression |

`<think>`-Blöcke aller Agenten erscheinen als Collapsibles mit Modellname und
Inferenzzeit.

---

## Temperatur und Sampling

- Jeder Agent antwortet mit seiner eigenen Temperatur aus der Sampling-Tabelle;
  eine automatische Temperatur gibt es nicht (seit 07.11.2026).
- Top-K, Top-P, Min-P und Repeat-Penalty kommen aus dem llama-swap-YAML-Eintrag
  des Modells und werden darauf zurückgesetzt, wenn das Backend startet oder das
  Modell wechselt; die Temperatur überlebt einen Neustart. Der ↺-Button setzt
  alles auf die YAML-Defaults zurück.

Sokrates, Salomo und jeder andere Agent können auf einem anderen Modell laufen
als AIfred.
