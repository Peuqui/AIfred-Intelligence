# Narrator Plugin (Dokument → Audio)

> **English version:** [narrator.md](../../../en/guides/plugins/narrator.md)

**Datei:** `aifred/plugins/tools/narrator/`

Vertont ein ganzes Textdokument aus dem Dokumentenbaum zu **einer** Audio-Datei (MP3) — das gesprochene Gegenstück zu `translate_file`. Der Dateiinhalt wird komplett serverseitig verarbeitet: gelesen, an Absatzgrenzen in ~800-Zeichen-Stücke zerlegt, stückweise über die TTS-Engine synthetisiert, per ffmpeg zusammengefügt und als MP3 (Sprach-VBR ≈ 130 kbps, rund Faktor 10 kleiner als WAV) neben der Quelldatei abgelegt. Der Text passiert dabei **nie den LLM-Kontext** — auch ein 100k-Zeichen-Transkript kostet kein Kontextfenster.

Der Narrator vertont **1:1**: kein Übersetzen, kein Zusammenfassen, keine Korrektur. Das ist Aufgabe der vorgelagerten Schritte (Arbeitsteilung der Pipeline).

## Typische Pipeline (Meeting-Mitschnitt)

1. Audio-Upload → Whisper-Transkript landet als `transcript-….txt` im Workspace (Original-Sprache, `language=auto`)
2. Optional: LLM korrigiert/formatiert das Rohtranskript (`read_file` / `write_file`)
3. `translate_file` → DeepL-Übersetzung als eigene Datei
4. `narrate_file` → MP3 neben der Quelldatei, über den Dokumente-Button herunterladbar (z. B. fürs Handy)

## Engine-Wahl und Einstellungen

**Engine:** Der Narrator hat keine eigene Engine-Einstellung. Er nimmt die Engine aus der **TTS-Eskalationsliste** (`aifred/lib/tts_escalation.py`, `choose_speaker`):

- Ohne Parameter `engine`: der oberste Eintrag der Liste, der jetzt sprechen kann. Ein lokaler GPU-Eintrag, dessen Container nicht läuft, wird gestartet, wenn sein gemessener Spitzenbedarf in den freien VRAM einer Karte passt — das Hauptmodell wird dafür nie neu geladen. Passt er nicht, geht die Liste weiter.
- Mit Parameter `engine` (Engine-Schlüssel, z. B. `piper`): genau diese Engine auf diesem Rechner, sofern sie jetzt kann; sonst Fehler mit Grund (`NoSpeechAvailable`), kein Ausweichen auf eine andere Engine.
- Die gewählte Engine bleibt für die **ganze Datei**: kein Stimmwechsel mitten im Hörbuch, auch nicht bei einem Ausfall.

Die Engines liegen als Plugins in `aifred/lib/tts_engines/<key>/engine.py` (je mit eigener `i18n.json`); Reihenfolge, Hosts und Aktivierung der Liste: [TTS + VRAM-Workflow](../../architecture/tts-vram-workflow.md).

**Stimme** (Zahnrad im Plugin-Tab des Agent-Editors, `narrator_voices` in den Einstellungen): wird **pro Engine** gespeichert. Der Dialog hat zwei Auswahlfelder — die Engine, deren Stimme bearbeitet wird (nur Engines, die hier bereit sind oder in der Liste auf einem anderen Host stehen), und deren eigene Stimmen (`voice_names`). Es wählt damit nicht die Engine der Vertonung. Reihenfolge der Stimmenwahl: Parameter `voice`, sonst die gespeicherte Stimme der gewählten Engine, sonst deren erste eigene Stimme (nie der Klonname „AIfred“ für z. B. Piper), zuletzt `NARRATE_DEFAULT_VOICE` (`AIfred`).

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `narrate_file` | Textdatei zu einer MP3-Audiodatei vertonen | WRITE_DATA |
| `list_narrator_voices` | Stimmen der effektiven Engine auflisten (Discovery für Multi-Voice) | READONLY |

## Parameter

| Parameter | Pflicht | Beschreibung |
|-----------|---------|-------------|
| `filename` | Ja | Quelldatei relativ zum Dokumenten-Root, ohne `documents/`-Präfix (z. B. `meeting-DE.txt`) |
| `output_filename` | Nein | Standard: `<name>.mp3` im selben Ordner; Endung `.wav` überspringt den MP3-Encode |
| `voice` | Nein | Standard: gespeicherte Stimme der gewählten Engine, sonst deren erste eigene Stimme |
| `language` | Nein | Sprachcode des Texts (Standard `de`) |
| `engine` | Nein | Engine-Schlüssel. Standard: oberster Eintrag der TTS-Eskalationsliste, der jetzt sprechen kann (siehe oben) |
| `speaker_voices` | Nein | Multi-Voice-Mapping Sprecher-Label → Stimmenname (siehe unten) |

Rückgabe (JSON): `written`, `url`, `chunks`, `chars`, `engine`, `voice`, `size_mb` — im Multi-Voice-Modus zusätzlich `speaker_voices`, `segments`.

## Multi-Voice-Modus (Hörspiel)

Interview-/Dialog-Transkripte lassen sich mit **einer Stimme pro Sprecher** vertonen. Die Sprechertrennung macht das LLM (nicht die Akustik): AIfred bereitet das Transkript als markierten Dialog auf und ruft `narrate_file` **einmal** mit `speaker_voices` auf.

- **Stimmen-Discovery**: `list_narrator_voices` liefert die gültigen Stimmennamen der effektiv aufgelösten Engine (plus Default-Stimme) — Stimmennamen sind engine-spezifisch, das Modell ruft das Tool vor einem Multi-Voice-Lauf auf statt zu raten.
- **Klon-Stimmen bevorzugt**: Stimmen mit `★`-Prefix sind selbst geklonte Stimmen (SSOT: das Prefix kommt direkt aus `engine.get_voices()`, z. B. bei xtts und DashScope). Das Modell ist angewiesen, sie bei der Rollenvergabe zu bevorzugen; `generate_tts` strippt das Prefix zentral vor der Synthese. Bei reinen Klon-Engines (qwen3local) tragen die Stimmen kein `★` — dort ist ohnehin jede Stimme ein Klon.
- **Marker-Format**: Zeilen, die mit `[LABEL]:` beginnen (Label frei: `FRAGE`/`ANTWORT`/`S1`/…). Ein Segment läuft bis zum nächsten Marker; der Marker selbst wird **nicht mitvertont**. Text vor dem ersten Marker bekommt die Default-Stimme (`voice`).
- **`speaker_voices`**: JSON-Objekt Label → Stimmenname (aus der `list_narrator_voices`-Liste). Alle Stimmen müssen zur **einen** effektiven Engine gehören (kein Engine-Mix).
- **Strikte Validierung**: unbekannte Stimme oder ein Label im Text ohne Mapping → Klartext-Fehler, **kein stiller Fallback**.
- **Sprecheranzahl**: unbegrenzt — der Parser kennt kein Limit. Praktisch begrenzt nur die Stimmenzahl der Engine die Klangvielfalt; mehrere Labels dürfen sich dieselbe Stimme teilen (Zehn-Personen-Hörspiel mit vier Stimmen ist legitim).
- **Chunking**: Sprecherwechsel ist immer eine harte Chunk-Grenze; lange Segmente werden intern weiter an Absatzgrenzen geteilt.
- Die Marker überleben die DeepL-Übersetzung (`translate_file`) — übersetzte Hörspiele funktionieren mit derselben markierten Datei-Pipeline.

**Hinweis:** Lange Dokumente brauchen real Zeit (grob 5–10× der Audiodauer, engine-abhängig). Fortschritt erscheint alle 5 Chunks in der Debug-Konsole.
