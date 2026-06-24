# DashScope Qwen3-TTS — Cloud Voice Cloning

> Stand: 2026-06-25 | Region: international/Singapur (`dashscope-intl.aliyuncs.com`)
> Code: [`aifred/lib/dashscope_enroll.py`](../../../aifred/lib/dashscope_enroll.py),
> [`aifred/lib/tts_engines/dashscope.py`](../../../aifred/lib/tts_engines/dashscope.py),
> [`aifred/state/_tts_config_mixin.py`](../../../aifred/state/_tts_config_mixin.py)

DashScope ist unsere **Cloud-TTS-Engine**: keine lokale GPU, schnelle Ergebnisse,
Qwen3-TTS als großes Modell. Dieses Dokument hält fest, **wie** das Voice-Cloning
korrekt angesprochen wird und — wichtiger — **wo die Cloud-API prinzipielle
Grenzen** hat, damit wir das nicht wiederholt recherchieren müssen.

## Die korrekte Enrollment-API (verifiziert 2026-06-25)

Es gibt zwei verwechselbare Cloning-Pfade bei DashScope. Für die **Qwen3-TTS**-Stimmen
gilt ausschließlich:

- **Modell:** `qwen-voice-enrollment` (NICHT `voice-enrollment` — das ist die CosyVoice-Variante)
- **Action:** `"create"` (NICHT `"create_voice"`)
- **Endpoint:** `POST /api/v1/services/audio/tts/customization`
- **Referenz-Audio:** inline als **base64-data-URI** in `input.audio.data`
- **Synthese-Modell:** `qwen3-tts-vc-2026-01-22` — die Synthese **muss** dasselbe
  `target_model` nutzen, mit dem die Stimme erstellt wurde.

Der falsche Pfad (SDK `VoiceEnrollmentService` mit `model="voice-enrollment"`,
`action="create_voice"`, `url=...`) ist die **CosyVoice**-Variante und scheitert für
die Qwen-Modelle mit „preprocess service not found".

### Volle Referenzlänge statt 10-Sekunden-Cap

Wir übergeben die Referenz-WAV in **voller Länge** (kein `max_prompt_audio_length`).
Das gibt der Cloud die komplette Referenz statt der 10-Sekunden-Vorgabe und liefert
hörbar besseren Akzent/Charakter (empirisch bestätigt: 25 s > 10 s). Die offizielle
Doku empfiehlt 10–20 s (max. 60 s) — beides liegt im erlaubten Rahmen; entscheidend
ist saubere, rauschfreie Referenz.

## Automatisches Enrollment beim Engine-Switch

Wer DashScope als TTS-Engine anwählt, bekommt seine geklonten Stimmen **automatisch**
— kein manueller Anstoß. Ablauf ([`_tts_config_mixin.py`](../../../aifred/state/_tts_config_mixin.py)
→ `set_tts_engine_or_off`):

1. SSOT-Stimmenordner [`docker/tts/voices/<Name>/<Name>.wav`](../../../docker/tts/voices/)
   wird gescannt.
2. `enroll_progress()` klont jede **neue oder geänderte** Referenz-WAV in voller Länge.
3. Läuft als sichtbare Generator-Schritte (`add_debug` + `yield`) — jede Zeile
   erscheint sofort in der Debug-Konsole, inklusive einer **Abschluss-Zeile**, damit
   man Fortschritt UND Ende sieht.

### Verifikation: ist eine Stimme schon enrollt?

Die Cloud-`list_voices` liefert für unsere Enrollments **0** und taugt nicht als
Prüfung. Stattdessen führt ein **lokales Mapping**
[`data/tts/dashscope_voices.json`](../../../data/tts/) pro Stimmenname die
zurückgegebene `voice_id`, das `target_model` und den **SHA-256 der Referenz-WAV**.
Eine Stimme gilt als enrollt, **genau dann wenn** ihr Name im Mapping steht UND der
aktuelle WAV-Hash mit dem gespeicherten übereinstimmt:

- neue WAV (Name fehlt im Mapping) → enrollen
- geänderte WAV (Hash weicht ab) → re-enrollen
- unveränderte WAV (Hash passt) → überspringen (kein Cloud-Call, keine Kosten)

Dadurch ist der Switch nach dem ersten Lauf effektiv instant (nur Hash-Checks).
**Re-Enrollment erzwingen:** Mapping-Eintrag (oder die ganze Datei) löschen — der
Ordner wird nicht zur Laufzeit überwacht, Auslöser ist immer der Engine-Switch.

### Stimmenliste

Die geklonten Stimmen sind **nicht** im Code hartcodiert. `_cloned_voices()` liest
das Mapping live (`★ Name` → `qwen-tts-vc-*`-id) und legt sie über die Built-in-Stimmen.
Eine frisch enrollte WAV erscheint dadurch ohne Neustart in der Auswahl.

## Prinzipielle Grenzen der Cloud-API

Recherchiert in der offiziellen Model-Studio-Doku (Quellen unten). Diese Grenzen
sind **API-seitig** (Server), nicht durch das SDK bedingt — ein SDK-Update behebt
sie nicht.

### Kein Referenztext beim Enrollment

Der `qwen-voice-enrollment`/`create`-Endpoint nimmt **ausschließlich Audio**, kein
Transkript-Feld (`text`/`reference_text`/`prompt_text`/`transcript` existieren nicht).
Das Cloning ist bewusst audio-only („3-Sekunden-Cloning", ASR-frei).

**Das erklärt den „hölzernen" Klang gegenüber lokalem Cloning:** Unser lokales
qwen3-tts nutzt den Modus `with_transcript` (das `<Name>.txt` neben der WAV) und
kann damit **Prosodie/Stil** aus der Referenz übernehmen — die Cloud kann das per
Design nicht und überträgt nur die Klangfarbe.

### Keine Sampling-/Stil-Parameter für geklonte Stimmen

- `temperature`, `top_p`, `top_k`, `repetition_penalty`, `seed`: für **kein**
  Qwen3-TTS-Modell dokumentiert/unterstützt. Das `**kwargs` von
  `MultiModalConversation.call` reicht sie zwar durch, die TTS-API wertet sie
  aber nicht aus.
- Der einzige dokumentierte Stil-Hebel ist `input.instructions`
  (+ `optimize_instructions`) — natürlichsprachliche Steuerung von Emotion/Tempo —
  und der wird **nur von `qwen3-tts-instruct-flash`** unterstützt, **nicht** vom
  Voice-Clone-Modell. Cloning und Stil-Steuerung schließen sich in der Cloud
  aktuell gegenseitig aus (verschiedene Modelle).

### Modellstand & SDK

- `qwen3-tts-vc-2026-01-22` ist der **aktuell neueste** VC-Snapshot (Stand 06/2026).
- SDK-Update bringt für unseren VC-Use-Case **nichts**: die `instruct`-Unterstützung
  kam schon vor unserer installierten Version 1.25.12; spätere Patch-Releases
  (bis 1.25.24) enthalten nur CosyVoice-/ASR-/generische Fixes, keine
  qwen3-tts-vc-Neuerungen. Darum bleibt das SDK bewusst auf Stand.

### Einziger realer Qualitätshebel

Bei der Cloud bleibt nur die **Referenz-Audio-Qualität**: sauber, rauschfrei, kein
Gesang, ≥ 24 kHz, 10–20 s (max. 60). Wer echte Prosodie-/Stil-Kontrolle (Referenztext
+ Sampling) braucht, kommt an **lokalem Cloning** nicht vorbei (siehe
[tts-comparison.md](../models/tts-comparison.md)).

## Quellen

- https://www.alibabacloud.com/help/en/model-studio/qwen-tts-voice-cloning
- https://www.alibabacloud.com/help/en/model-studio/qwen-tts-api
- https://www.alibabacloud.com/help/en/model-studio/qwen-tts
- https://qwen.ai/blog?id=qwen3-tts-vc-voicedesign
- https://github.com/dashscope/dashscope-sdk-python
