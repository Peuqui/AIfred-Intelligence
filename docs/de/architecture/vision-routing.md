# Vision-Routing: Der llama.cpp-Describer-Pfad

> **English version:** [vision-routing.md](../../en/architecture/vision-routing.md)

Wer ein Bild ansieht — Chat-Upload, Symposion, Sandbox-Screenshot,
Vision-Tool, Vigilantia — und wie das Vision-Modell neben das geladene
Chat-LLM kommt, ohne es aus dem VRAM zu verdrängen. Stand (2026-10-09): zwei
Wahlregeln in [`aifred/lib/vision_routing.py`](../../../aifred/lib/vision_routing.py)
ersetzen die frühere Vier-Stufen-Präzedenz; ob ein Describer daneben passt,
entscheidet der gemessene Burn-in-Peak, nicht mehr allein ein reserviertes
Profil. Der Side-Channel läuft primär über llama.cpp/llama-swap; Ollama bleibt
als Pfad für Setups ohne Describer-Profile.

## Kernidee

Das Chat-LLM läuft über **llama-swap** und belegt den Großteil des VRAM.
Für Bildanalysen gibt es dedizierte **Describer-Profile** (`<base>-visiond`
in der llama-swap-config): schlanke Instanzen desselben Vision-Modells mit
kleinem Kontext (`-c 24576` = `VLM_NUM_CTX` in
[`aifred/lib/config.py`](../../../aifred/lib/config.py), genug für Bursts von
10 Keyframes) und quantisiertem KV-Cache (`-ctk/-ctv q8_0` =
`VLM_KV_CACHE_TYPE`; beim 4B 7.396 statt 8.988 MiB Spitze), ohne
Draft-Modell. Das Heimat-Profil `<base>-visiond` ist per
`CUDA_VISIBLE_DEVICES` auf die Side-Channel-Karte gepinnt; dazu kommt je
weitere Karte eine **Platzierungs-Variante** `<base>-visiond-gpu<N>` mit
derselben Befehlszeile. `fitting_visiond`
([`aifred/lib/vision_routing.py`](../../../aifred/lib/vision_routing.py))
wählt beim Beschreiben eine schon geladene Platzierung, sonst die Heimat,
wenn sie passt, sonst die passende Karte mit dem meisten freien VRAM — so
läuft der Describer auch neben einem Hauptmodell, das alle fünf Karten
belegt (DeepSeek-V4-Flash: 4B auf GPU 0). Alle laufen in der llama-swap-
Gruppe `vision` **parallel** zum Chat-LLM — kein Modell-Swap für eine
Bildbeschreibung; die Gruppe hält genau einen Describer.

Die Profile pflegt der Autoscan
([`scripts/llama-swap-autoscan.py`](../../../scripts/llama-swap-autoscan.py)):
`ensure_visiond_profiles` legt für jedes Modell mit passendem
`mmproj-*.gguf` ein `-visiond`-Profil an (GPU-Pin über `pick_vlm_gpu`,
Mitglied der `vision`-Gruppe), `enforce_visiond_flags` zieht `-c` und KV-Typ
der Heimat-Profile auf `VLM_NUM_CTX`/`VLM_KV_CACHE_TYPE` (SSOT),
`sync_visiond_placements` leitet die Platzierungs-Varianten aus ihrer Heimat
ab (abweichende neu geschrieben, Varianten ohne Heimat oder Karte entfernt),
und `cleanup_stale_vlm_variants`
entfernt `-vlm-`-Varianten, deren Describer kein Profil mehr hat. Die
Kalibrierung entdeckt ihre Describer-Auswahl aus genau diesen Profilen
(`vlm_calibration_choices`).

Die Reserve stellt die Kalibrierung: Sobald ein Vision-Modell aktiv ist,
wählt `resolve_variant_suffix`
([`aifred/lib/calibration/llamaswap_io.py`](../../../aifred/lib/calibration/llamaswap_io.py))
für das Chat-LLM das `<base>-vlm-<key>`-Profil, das den Reserve-Slot
(z.B. ~8 GB auf einer V100) freilässt. Ausnahme (`_is_self_describer`): Ist
das Vision-Modell das Chat-Modell selbst, gibt es nichts zu reservieren — die
VLM-Stufen entfallen, das Basis-Profil mit vollem Kontext gewinnt.

## Wahlregeln

Beide Regeln liefern einen `Describer(model, evicts_chat_model)`; verdrängt er
das Chat-LLM, kündigt `eviction_notice` das an (das Neuladen kann bis zu
mehreren Minuten dauern).

**Die Beschreiber-Liste** ([`aifred/lib/describer_list.py`](../../../aifred/lib/describer_list.py)),
in den Vigilantia-Einstellungen bearbeitet wie die TTS-Liste (Pfeile, Schalter): Einträge sind ein Modell
eines anderen Rechners (`<rechner>/<modell>`), dieser Rechner (das VLM der Vigilantia-Einstellungen über
sein `-visiond`-Profil) und das Hauptmodell, wenn es selbst sieht. Der erste eingeschaltete Eintrag, der
jetzt kann, beschreibt. Die angezeigte und gespeicherte Liste ist immer der Abgleich mit dem, was da ist:
Neues kommt ausgeschaltet ans Ende, ein Modell, das ein antwortender Rechner nicht mehr hat, fällt raus,
die Einträge eines Rechners, der nicht antwortet, bleiben. Ohne gespeicherte Liste: dieser Rechner, dann das
Hauptmodell. Zwei Regeln sind fest und keine Einträge (Begründung unten).

**Regel A — `chat_describer(main_model)`** (Chat-Uploads, Symposion,
Sandbox-Screenshots, hochgeladene Bilder im Vision-Tool):

1. Sieht das Hauptmodell selbst (`has_native_vision`), beschreibt es selbst und wird nie
   verdrängt. Fest: Es beantwortet den Turn ohnehin, sieht es, bekommt es das Bild direkt mit Verlauf und
   Werkzeugen; jeder andere Beschreiber gäbe ihm nur eine Textbeschreibung, und Umladen kostet mehr.
2. Sonst der erste eingeschaltete Eintrag der Liste, der jetzt kann (der Eintrag „Hauptmodell“ zählt hier
   nicht; „dieser Rechner“ nur über ein `-visiond`-Profil).
3. Sonst das Vision-LLM der Haupteinstellungen über sein `-visiond`-Profil,
   wenn es daneben passt (`check_visiond_fits`, siehe unten).
4. Sonst die effektive Variante des Vision-LLM — sie verdrängt das Chat-LLM.
5. Ist kein Vision-LLM eingestellt: `NoVisionModelError`.

**Regel B — `camera_describer(vlm_model, explicit=…)`** (Vigilantia-Watcher,
Event-Analyse, Bulk, Vision-Tool mit Kamera-Quelle):

1. Der erste eingeschaltete Eintrag der Liste, der jetzt kann: ein Modell eines anderen Rechners (antwortet,
   nicht freigehalten, passt dort), dieser Rechner (das Kamera-VLM, z.B. das 4B, passt neben das Geladene
   oder läuft schon → sein `-visiond`-Profil; ein Ollama-VLM über den Seitenkanal) oder das geladene
   Modell, das selbst sieht.
2. Sonst nur bei ausdrücklicher Anfrage (`explicit=True`: Casus-Knopf „Analysieren“, der Nutzer fragt
   AIfred nach einem Kamerabild) das Vision-LLM, verdrängend. Keine Rückfrage: Der Klick oder die Frage ist
   die Zustimmung, die Debug-Konsole sagt es vorher an.
3. Sonst `NoVisionModelError`: Automatische Pfade (Watcher, Alarme, Live, Nachtlauf) verdrängen nie; das
   Bild geht ohne Beschreibung raus und lässt sich nachträglich beschreiben. Fest: Ein Bewegungsereignis
   würde sonst das Hauptmodell entladen, und sein Neuladen dauert Minuten.

Ein Ollama-Modell (kein llama-swap-Eintrag) bleibt unverändert. Den Versand
übernimmt `analyze_sequence`
([`aifred/lib/vision_analyzer.py`](../../../aifred/lib/vision_analyzer.py)) nur
noch per Dispatch: `has_native_vision` → llama-swap, sonst Ollama.

**Sieht ein Eintrag?** `has_native_vision`
([`aifred/lib/vision_utils.py`](../../../aifred/lib/vision_utils.py)) liest den
Startbefehl des llama-swap-Eintrags: llama.cpp mit `--mmproj`; vLLM, wenn das
Checkpoint einen Bildkodierer trägt und weder `--language-model-only` noch ein
Bildlimit 0 in `--limit-mm-per-prompt` ihn abschaltet (dann weist vLLM jedes
Bild ab). Der Name des Modells zählt nicht.

**Passt das daneben?** `check_visiond_fits`
([`aifred/lib/vision_vram_check.py`](../../../aifred/lib/vision_vram_check.py)):
ein schon geladenes Profil passt immer; sonst der Burn-in-Peak aus
`data/vlm_vram_cache.json` (Modell × Kontext) plus `VLM_HEADROOM_MIB`
gegen den freien VRAM der Karten, auf die das Profil per
`CUDA_VISIBLE_DEVICES` gepinnt ist (ungepinnt: Summe aller Karten). Fehlt
die Messung oder die Karte, passt es nicht.

**Solange ein großes Modell lädt, kommt kein Describer dazu.** `load_in_progress`
([`aifred/lib/vision_routing.py`](../../../aifred/lib/vision_routing.py)) fragt die Ampel von
GPU-Orc (`GET /loading`). Während des Ladens zeigt nvidia-smi Karten, die gleich
voll sind; ein Describer, der nach dem freien Speicher geht, nähme dem Modell den Platz, und es
stürbe mit OOM. Ein schon geladener Describer arbeitet weiter. Sonst gibt es keinen: Automatische
Pfade schicken das Bild ohne Beschreibung, die sich nachträglich erzeugen lässt, und auch ein
ausdrücklicher Auftrag verdrängt kein ladendes Modell (`NoVisionModelError`).

### Describer auf anderen Rechnern

[`aifred/lib/remote_describers.py`](../../../aifred/lib/remote_describers.py). Ein anderer
Rechner (z.B. ein Arbeitsplatz mit eigener Karte) betreibt seinen eigenen llama-swap und seinen
eigenen GPU-Orc. AIfred kennt ihn genau einmal, in `VISION_REMOTE_HOSTS` (`.env`, JSON-Liste mit
`name`, `llamaswap_url`, `gpu_orc_url`, `gpu_orc_token_file`), ohne Modelle: Welche Modelle es dort
gibt, welche sehen (`capabilities.vision`), mit welchem Kontext und ob sie geladen sind, meldet
sein `/v1/models`. Jedes Modell dort, das sieht, ist ein Eintrag der Beschreiber-Liste; ein neues
Profil dort erscheint von selbst, ausgeschaltet am Ende.

- **Adresse:** Ein Modell dort heißt `<rechner>/<modell>`, wie llama-swap die Modelle seiner
  Peers nennt. Die Aufrufer reichen die Id weiter wie jede andere; `analyze_sequence` und
  `_describe_images` schicken an den llama-swap des Rechners, mit der Id dort. Ein `peers:`-Eintrag
  in der eigenen llama-swap-YAML ist nicht nötig (er bräuchte eine feste Modellliste).
- **Erreichbar?** Vor jeder Wahl ein `GET /health` mit 0,5 s Frist
  (`VISION_REMOTE_HEALTH_TIMEOUT_S`). Ein Rechner, der aus ist oder schläft, ist der Normalfall:
  Die Wahl geht ohne Warnung zur nächsten Stufe, die Logdatei sagt warum.
- **Passt es?** Geladen passt; solange dort ein großes Modell lädt (Ampel des GPU-Orc dort), kommt
  nichts dazu; sonst der gemessene Bedarf plus `VLM_HEADROOM_MIB` gegen den freien Speicher der
  Karten dort (`/status` des GPU-Orc dort, was er vor dem Laden freiräumt, zählt als frei). Ein nie
  vermessenes Modell passt nicht.
- **Vermessen:** Knopf in der Auswahl (`stress_prewarm_remote` in
  [`aifred/lib/vlm_stress_prewarm.py`](../../../aifred/lib/vlm_stress_prewarm.py)): llama-swap
  dort entlädt alles, lädt das Modell mit dem Stressbild des Burn-ins, gespeichert wird die Spitze
  der Karten minus die Belegung davor, unter `<rechner>/<modell>` und dem Kontext, den der Rechner
  meldet.
- **Freiräumen:** Vor dem Laden fragt AIfred den GPU-Orc des Rechners, auf dem das Modell lädt
  (Bildweg `release_side_channels_for_describer`, Chat-Weg `_free_gpus_for_load`).
- **Freihalten:** Die Statusleiste zeigt je Rechner eine Zeile (GPU-Orc dort: erreichbar,
  freigehalten, Belegung, was auf der Karte liegt) mit dem Knopf „Karte freihalten“ / „Karte
  freigeben“ (`/api/service-status/hosts`, `/api/service-status/hold`). Freihalten heißt
  `POST /hold` beim GPU-Orc dort: Er räumt sofort alle Karten und meldet den Halt auf seiner
  Ampel, bis `DELETE /hold`; der Halt liegt dort in einer Datei und übersteht einen Neustart. Solange
  passt dort nichts (`check_fits`), der Burn-in verweigert, und die TTS-Suite startet dort keine
  Container (sie fragt die Ampel des Rechners vor jedem Start). Dieselbe Ampel bremst auch die
  Describer dieses Rechners (`load_in_progress`), wenn jemand dessen Karten freihält.
- **Ausfall mitten in der Anfrage:** `describe_with_second_attempt` (alle Bildwege). Antwortet der
  Rechner danach nicht mehr, meldet die Debug-Konsole das laut, und dieselbe Wahl läuft genau ein
  zweites Mal: Sie findet den Rechner nicht mehr und nimmt die nächste Stufe. Antwortet er noch,
  liegt der Fehler in Anfrage oder Modell und geht unverändert an den Aufrufer. Im Chat-Weg
  wiederholt das Backend Verbindungsfehler zuerst selbst (`worth_retrying`, Fenster
  `STREAM_RETRY_WINDOW_S`), erst danach greift der zweite Anlauf.

### Chat-Bilder: VL Direct oder zwei Schritte

`send_message` ([`aifred/state/_chat_mixin.py`](../../../aifred/state/_chat_mixin.py))
fragt `_image_describer` ([`aifred/state/_agent_config_mixin.py`](../../../aifred/state/_agent_config_mixin.py)),
die Regel A auf das effektive Hauptmodell anwendet:

- **Hauptmodell sieht, ein Agent:** VL Direct — das Hauptmodell bekommt die
  Bilder und antwortet als Agent (`_process_vision_request`).
- **Sonst (oder Symposion mit ≥2 Agenten):** `_describe_images` beschreibt die
  Bilder einmal neutral (`vision/task_instruction_handoff`, mit Nutzerfrage `…_handoff_question`), die Beschreibung
  wird als `[Bildinhalt: …]` an den Nutzer-Turn gehängt, und die normale
  Text-Pipeline antwortet — das Hauptmodell mit vollem Prompt, Werkzeugen und
  Verlauf, bzw. alle Symposion-Agenten auf derselben Grundlage. Folgefragen
  beantwortet der Agent bei Bedarf mit einem neuen Blick per `vision_analyze`
  (die `/_upload/`-URL steht im Turn).
- **Kein Vision-LLM:** Das Bild geht ohne Beschreibung weiter, mit Hinweis in
  der Debug-Konsole.

**Denken beim Beschreiben:** Jede Beschreibung (`_describe_images`, `analyze_sequence` →
`_analyze_via_llamacpp`) ist ein Auftrag an den Agenten „Vision“. Sie denkt, wie die
Vision-Zeile es für das beschreibende Modell sagt (`agent_settings.thinking_mode`, je Modell
gespeichert), und schickt die Stufe wie die Chat-Backends in `chat_template_kwargs`. Der
4B bleibt so ohne Denken, GLM als Hauptmodell beschreibt mit der Stufe, die für GLM in der
Vision-Zeile steht. VL Direct folgt der Zeile des antwortenden Agenten.

## Entlade-Semantik (llama-swap-Gruppen)

```yaml
groups:
  main:
    exclusive: true    # Chat-Modelle verdrängen sich wie gehabt
    swap: true
  embed:
    exclusive: false   # CPU-only -embed-Server, nie Teil des Swaps
    swap: true
    persistent: true
  vision:
    exclusive: false   # Describer-Load verdrängt das Chat-LLM nicht
    swap: true         # Describer verdrängen sich gegenseitig (1 Slot)
    persistent: true   # llama-swap entlädt Describer NIE von sich aus
```

Diese drei Gruppen schreibt der Autoscan (`update_groups_in_yaml`).

Wichtig: llama-swaps `exclusive` wirkt **request-basiert** — ohne
`persistent` würde jeder Request an das (bereits laufende!) Chat-LLM den
Describer entladen (empirisch verifiziert 2026-08-16). Mit `persistent`
gilt: **Passende Profilkombinationen koexistieren unbegrenzt, aufgeräumt
wird nur per `ttl` des Profils** (z.B. 900 s idle bei
`Qwen3VL-4B-Instruct-Q8_0-visiond`; neue Profile bekommen ihre ttl aus
`ttl_for_model_size`).

Den Konflikt-Fall — ein Hauptmodell will eine Karte, die ein Beiwagen belegt —
räumt [GPU-Orc](https://github.com/Peuqui/GPU-Orc), ein eigener Dienst je Rechner:
`_free_gpus_for_load` ([`aifred/backends/base.py`](../../../aifred/backends/base.py))
läuft im `_pre_request_check` der llama.cpp- und vLLM-Backends bei jeder
Anfrage und liest llama-swaps `/running`. Läuft das Ziel-Modell bereits, wird
nichts angerührt. Sonst löst die Anfrage einen Load aus (auch das Neuladen
desselben Modells nach seiner ttl), und AIfred bittet GPU-Orc vorher, die Karten
des Modells freizuräumen (`POST /free {"model": …}`, `aifred/lib/gpu_orc_client.py`).
GPU-Orc liest die Karten des Eintrags aus der llama-swap-config, gibt dort die
GPU-Worker von STT- und TTS-Suite frei und entlädt die dauerhaften Modelle der
Gruppen mit `persistent: true` (Beiwagen `-visiond`, `-embed`). Profile mit
`-vlm-`-Marker (Reserve per Kalibrierung) und die Beiwagen selbst verdrängen
keine Beiwagen (`no_eviction_models` in GPU-Orcs Config). Was GPU-Orc nicht
freibekommt oder ob er gar nicht antwortet, steht laut im Log; geladen wird
trotzdem. Den Describer räumt derselbe Aufruf mit dem `-visiond`-Profil frei
(`release_side_channels_for_describer`), und die Platzprüfung
(`check_visiond_fits`) zählt, was GPU-Orcs `GET /status` an gemessenem Speicher
von STT und TTS auf der Karte meldet, als frei.

## Badge-Logik

`_build_vision_rich`
([`aifred/state/_backend_mixin.py`](../../../aifred/state/_backend_mixin.py))
folgt der Laufzeitwahl: `🧠 Chat-LLM`, wenn das Vision-Modell das Hauptmodell
selbst mit eigenem Vision-Encoder ist; `⚡ No Swap`, wenn sein `-visiond`-Profil
gerade daneben passt (`check_visiond_fits`) oder es ohne `-visiond`-Profil
über den Ollama-Seitenkanal läuft; sonst `🔄 Swap`. Die Badges werden je
Sitzung neu berechnet, nicht aus dem globalen Startzustand übernommen.

## Verifizierte Szenarien (2026-08-16)

| Szenario | Verhalten |
|---|---|
| Request ans warme Chat-LLM | Describer bleibt geladen |
| Chat-LLM-Wechsel auf anderes `-vlm-`-Reserve-Profil | Describer überlebt den Swap |
| Chat-LLM-Wechsel auf Basis-Profil ohne Reserve | AIfred-Guard entlädt Describer vor dem Load |
| DeepSeek (alle 5 Karten) + Describer | Reserve hält: 8,3 GB frei auf der V100, Describer (6,7 GB) passt, Chat-LLM antwortet danach in ~1 s |
| 15 min ohne Bildanfrage | `ttl` entlädt den Describer |

## Offene Punkte

- Describer-Qualitätsvergleich Qwen3.5-4B vs. Qwen3VL-4B; danach
  Qwen3VL-4B-Altmodell (GGUF + Ollama-Store) entsorgen.
- `-vlm-`-Reserve-Varianten nur noch anlegen, wenn das Chat-LLM sie
  wirklich braucht (Schwellenlogik). (Das Erzeugen der `-visiond`-Profile ist
  erledigt: Autoscan, s.o.)
- Burn-in je Describer-VLM: Ohne Messung in `data/vlm_vram_cache.json`
  passt ein Profil nie (z.B. Qwen3VL-30B-A3B bislang ungemessen).
- Ollama-Dienste stilllegen (sudo, User-Entscheid) — seit Paket 2 ruft
  kein Vision-Pfad mehr Ollama, solange Describer-Profile existieren.
