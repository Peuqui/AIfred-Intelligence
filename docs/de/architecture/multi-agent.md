# Multi-Agent-System & Diskussionsmodi

> **English version:** [multi-agent.md](../../en/architecture/multi-agent.md)

Wie AIfreds Agenten zusammenarbeiten: die eingebauten und eigenen Agenten, die
fünf Diskussionsmodi, wie jeder Agent die Konversation sieht und welche
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
| 📷 **Vision** | Bildanalyst — OCR und visuelle Q&A, erbt AIfreds Persönlichkeit |
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

`_merge_prompt_layers()` in `aifred/lib/prompt_loader.py` setzt jeden
System-Prompt aus bis zu zehn Schichten zusammen, in dieser Reihenfolge:

| # | Schicht | Wann |
|---|---|---|
| 0 | User-Präfix | immer (einmal, ganz oben) |
| 1 | Identität — wer der Agent ist | immer |
| 2 | Reasoning — wie gedacht wird | wenn Reasoning für den Agenten an ist |
| 3 | Multi-Agent-Rollen — wer die anderen sind | nur in Multi-Agent-Modi |
| 4 | Aufgaben-Prompt | immer |
| 5 | Security Boundary | in Kontexten externer Kanäle (Message Hub) |
| 6 | Gedächtnis-Anweisungen | wenn das Agenten-Gedächtnis aktiv ist (nicht im Inkognito-Modus) |
| 7 | Persönlichkeit — wie gesprochen wird | wenn Persönlichkeit an ist |
| 8 | Tool-Anweisungen | wenn Tools verfügbar sind (nahe am Ende, damit Tool-Nutzung priorisiert wird) |
| 9 | Disziplinen — Datums-Verankerung, Zitat-/Währungsdisziplin, … | immer |

Aktuelles Datum und Uhrzeit sind Teil jedes Prompts, damit zeitbezogene Fragen
funktionieren.

---

## Diskussionsmodi

| Modus | Ablauf | Wer entscheidet? |
|---|---|---|
| **Standard** | Ein Agent antwortet (ausgewählt über die Agenten-Pills) | — |
| **Kritische Prüfung** | AIfred → Sokrates (Kritik + Pro/Contra) → Stopp | Du |
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
     │ 2/3 oder 3/3   │              │ Zu wenig Stimmen│
     │ = Konsens      │              │ = nächste Runde │
     └────────────────┘              └─────────────────┘
```

Konsens-Art (Einstellungen): **Mehrheit** (2 von 3 stimmen mit `[LGTM]`) oder
**einstimmig** (alle 3). Sokrates kritisiert nur — über den Konsens zu
entscheiden ist Salomos Aufgabe.

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
wiederholen. Es ist eine additive Prompt-Schicht
(`prompts/{de,en}/shared/symposion_reflection.txt`), kein Ersatz für den
Diskussions-Prompt (`shared/symposion.txt`).

### Prompt-Dateien pro Schritt

| Modus / Schritt | Agent | Prompt |
|---|---|---|
| Standard | beliebig | `<agent>/system_minimal` (Aufgaben-Schicht) |
| Direkte Ansprache | angesprochener Agent | `<agent>/direct` |
| Kritische Prüfung, Auto-Konsens — Kritik | Sokrates | `sokrates/critic` |
| Auto-Konsens — Überarbeitung | AIfred | `aifred/refinement` |
| Auto-Konsens — Synthese | Salomo | `salomo/mediator` |
| Tribunal — Angriff | Sokrates | `sokrates/tribunal` |
| Tribunal — Verteidigung | AIfred | `aifred/defense` |
| Tribunal — Urteil | Salomo | `salomo/judge` |
| Symposion | jeder Teilnehmer | `shared/symposion` (+ `shared/symposion_reflection` ab Runde 2) |

`{round_num}` wird eingesetzt, damit der Kritiker weiß, welche Runde es ist;
Kritiker sind angewiesen, pro Runde höchstens ein oder zwei Punkte anzubringen.

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
| 🎩 AIfred [Auto-Konsens: Überarbeitung R2] | Überarbeitung, Runde 2 |
| 👑 Salomo [Auto-Konsens: Synthese R2] | Synthese, Runde 2 |
| 🏛️ Sokrates [Tribunal R1] | Tribunal-Austausch, Runde 1 |
| 👑 Salomo [Tribunal: Urteil R3] | Finales Urteil |
| 📊 Zusammenfassung #1 (5 Nachrichten) | Ausklappbare Zusammenfassung der History-Kompression |

`<think>`-Blöcke aller Agenten erscheinen als Collapsibles mit Modellname und
Inferenzzeit.

---

## Temperatur und Sampling

- **Auto-Modus:** Die Intent-Erkennung wählt die Basis-Temperatur
  (FACTUAL 0.2, MIXED 0.5, CREATIVE 1.0); Sokrates und Salomo bekommen einen
  Offset obendrauf (Standard +0.2 und +0.3, gedeckelt bei 1.0; `config.py`).
- **Manual-Modus:** Temperatur pro Agent in der Sampling-Tabelle.
- Top-K, Top-P, Min-P und Repeat-Penalty kommen aus dem llama-swap-YAML-Eintrag
  des Modells und werden darauf zurückgesetzt, wenn das Backend startet oder das
  Modell wechselt; die Temperatur überlebt einen Neustart. Der ↺-Button setzt
  alles auf die YAML-Defaults zurück.

Sokrates, Salomo und jeder andere Agent können auf einem anderen Modell laufen
als AIfred.
