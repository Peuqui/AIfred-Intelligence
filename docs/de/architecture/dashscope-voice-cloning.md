# DashScope Qwen-Audio 3 — Cloud-TTS und Stimmen-Klonen

> **English version:** [dashscope-voice-cloning.md](../../en/architecture/dashscope-voice-cloning.md)

> Stand: 2026-10-10 | Region: international/Singapur (`dashscope-intl.aliyuncs.com`)
> Code: [`aifred/lib/tts_engines/dashscope_audio3/engine.py`](../../../aifred/lib/tts_engines/dashscope_audio3/engine.py),
> [`aifred/lib/dashscope_enroll.py`](../../../aifred/lib/dashscope_enroll.py),
> [`aifred/state/_tts_config_mixin.py`](../../../aifred/state/_tts_config_mixin.py)

DashScope Qwen-Audio 3 (Engine-Key `dashscope_audio3`) ist die **Cloud-TTS-Engine**:
keine lokale GPU, kein Container. Sie löst am 10.10.2026 die ältere Qwen3-TTS-Cloud-Engine
ab, deren geklonte Stimmen Alibaba abgeschaltet hat; diese Engine ist entfernt. Das Dokument
hält fest, wie Synthese und Stimmen-Klonen angesprochen werden.

## Engine

| Eigenschaft | Wert |
|-------------|------|
| Engine-Key | `dashscope_audio3` |
| Zugang | API-Key im Credential-Broker (`cloud_qwen` / `api_key`); ohne Key ist die Engine nicht verfügbar (`is_running()`) |
| Modelle | `qwen-audio-3.0-tts-flash` (Standard), `qwen-audio-3.0-tts-plus` |
| Systemstimmen | 14 (Tabelle unten) |
| Geklonte Stimmen | aus dem gemeinsamen Stimmenbaum, mit `★` vor dem Namen |
| Sprachen | `de`, `en`, `fr`, `es`, `it`, `pt`, `ru`, `ja`, `ko`, `zh` werden als `language_type` übergeben, jede andere als `Auto` |
| Standardstimme | `Mary` |
| Eskalationsliste | in der Standardliste (`in_default_escalation`), Anzeige-Reihenfolge 50 |
| Tempo/Tonhöhe | nicht von der API, sondern zentral per ffmpeg (`needs_speed_postprocess`) |

Die Synthese ist ein HTTP-Aufruf (`POST /services/audio/tts/SpeechSynthesizer`, WAV, 24 kHz).
Die Antwort enthält eine URL; die Engine lädt die WAV-Datei (die URL kommt als `http://`, die
Datei wird über `https://` geholt), legt sie im TTS-Audio-Ordner ab und gibt deren Pfad zurück.
Zeitlimit für Synthese und Download: je 60 s (`request_timeout_s`).

**Fehlerarten** (`TTSFailure`, siehe [tts-escalation.md](tts-escalation.md)):
Netzwerk-/Internetausfall = `unreachable`; alles andere, auch eine Fehlerantwort der API
(Authentifizierung, Kontingent, unbekannte Stimme) = `engine`.

### Systemstimmen

Anzeigename → Stimmen-ID bei Alibaba. Die Namen stehen in `_VOICES` der Engine; der Code
vermerkt, dass alle Stimmen verständlich Deutsch sprechen, obwohl die Alibaba-Doku nur
Chinesisch und Englisch nennt — die Stimme bringt den Akzent mit.

| Name | ID | Name | ID |
|------|----|------|----|
| Mary | `loongmary` | Xiao Xin | `longanxiaoxin` |
| Eva | `loongeva_v3.6` | Huan | `longanhuan_v3.6` |
| John | `loongjohn` | Li Dou | `longjielidou_v3.6` |
| Ling Xin | `longanlingxin` | Pao Pao | `longpaopao_v3.6` |
| Lu Feng | `longanlufeng` | Huo Huo | `longhuohuo_v3.6` |
| Feng Yue | `longanfengyue` | Chuan Shu | `longchuanshu_v3.6` |
| Yuan Fei | `longanyuanfei` | Ling Xi | `longanlingxi` |

`Ling Xin` und `Lu Feng` laufen nur mit dem **Plus**-Modell; die Engine wählt das Modell je
Stimme selbst. Alle anderen Stimmen, auch die geklonten, nutzen **Flash**.

## Geklonte Stimmen

Quelle ist der gemeinsame Stimmenbaum `TTS_VOICES_DIR` (`docker/tts/voices/<Name>/<Name>.wav`,
derselbe wie bei den Container-Engines, siehe [tts-container-conventions.md](tts-container-conventions.md)).
Jede Referenz-WAV wird **einmal** bei DashScope angelegt (enrolled). Die zurückgegebene
`voice_id` steht in der Zuordnungsdatei
[`data/tts/dashscope_audio3_voices.json`](../../../data/tts/); diese Datei ist die Wahrheit
darüber, ob eine Stimme angelegt ist (die Cloud-Liste taugt nicht als Prüfung).

Pro Stimmenname enthält sie:

| Feld | Inhalt |
|------|--------|
| `voice_id` | die von DashScope vergebene ID |
| `wav_sha256` | SHA-256 der Referenz-WAV |
| `target_model` | Modell, für das die Stimme angelegt wurde (`qwen-audio-3.0-tts-flash`) |

Eine geklonte Stimme ist an ihr `target_model` gebunden, die Synthese muss dasselbe Modell
nutzen — deshalb laufen geklonte Stimmen immer mit Flash.

### Anlegen der Stimmen

API (`dashscope_enroll.py`): `POST /api/v1/services/audio/tts/customization` mit
`model: "voice-enrollment"`, `input.action: "create_voice"`, `input.target_model`,
`input.prefix` (Anfang des Namens: nur Kleinbuchstaben und Ziffern, höchstens 9 Zeichen) und
der Referenz-WAV in **voller Länge** als base64-Data-URI in `input.url`. Die `voice_id` steht
in der Antwort unter `output.voice_id`. Zeitlimit 90 s je Stimme.

Angestoßen wird das Anlegen durch `prepare_voices()` der Engine. Der Aufruf kommt aus
`_apply_planned_tts` (`_tts_config_mixin.py`) beim Einschalten der Sprachausgabe und bei jeder
Änderung der Eskalationsliste, für jede Engine mit aktivem Eintrag einmal. Der Ablauf
(`enroll_progress()`):

1. Jeder Unterordner von `TTS_VOICES_DIR` mit einer `<Name>/<Name>.wav` wird betrachtet.
2. Steht der Name in der Zuordnung **und** stimmt der SHA-256 der WAV, wird übersprungen
   (kein Cloud-Aufruf).
3. Sonst — neue oder geänderte WAV — wird die Stimme angelegt und die Zuordnung gespeichert.
4. Der Lauf meldet Zeile für Zeile in der Debug-Konsole und endet **immer** mit einer
   Abschlusszeile („N new, M already current“), auch wenn nichts zu tun war.

Fehlt der API-Key oder der Stimmenordner, meldet `enroll_progress()` das und legt nichts an.
Schlägt das Anlegen einer Stimme fehl, steht sie nicht in der Zuordnung und ist nicht
verfügbar; es gibt keinen Ersatzweg.

**Neu anlegen erzwingen:** den Eintrag (oder die ganze Datei) aus
`dashscope_audio3_voices.json` löschen. Der Ordner wird zur Laufzeit nicht überwacht;
Auslöser ist das Einschalten der Sprachausgabe oder eine Änderung der Liste.

### Stimmenliste

Die geklonten Stimmen sind nicht im Code festgelegt: `_cloned_voices()` liest die Zuordnung bei
jedem Aufruf (`★ Name` → `voice_id`) und legt sie vor die Systemstimmen. Eine frisch angelegte
Stimme erscheint ohne Neustart in der Auswahl.

## Nicht aus dem Code belegt

Die frühere Fassung dieses Dokuments beschrieb Grenzen der alten Qwen3-TTS-Cloud-API (kein
Referenztext beim Anlegen, keine Sampling-Parameter, Stil-Steuerung nur mit einem anderen
Modell). Ob diese Aussagen für Qwen-Audio 3 gelten, ist im Code nicht belegt — die Engine
übergibt nur `text`, `voice`, `language_type`, `format` und `sample_rate`, und das Anlegen
sendet keinen Referenztext. Die offizielle Doku von Alibaba (Model Studio, „Qwen-Audio TTS“)
ist dafür die Quelle.
