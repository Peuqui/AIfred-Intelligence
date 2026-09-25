# Vision-Routing: Der llama.cpp-Describer-Pfad

> **English version:** [vision-routing.md](../../en/architecture/vision-routing.md)

Wie eine Bildbeschreibung (Vigilantia, Sandbox-Screenshot, Vision-Tool) auf
ein Vision-Modell verteilt wird, ohne das geladene Chat-LLM aus dem VRAM zu
verdrängen. Stand (2026-09-25): Vision-Umbau Paket 1 (Describer-Pfad,
2026-08-16) und Paket 2 (Vigilantia auf den Describer-Pfad migriert,
2026-08-16) sind umgesetzt; seitdem dazugekommen: vom Autoscan erzeugte
Describer-Profile, das Chat-LLM als eigener Describer und ein GPU-basierter
Beiwagen-Eviction-Guard. Der Side-Channel läuft primär über
llama.cpp/llama-swap; Ollama bleibt als Bestands-Pfad für Setups ohne
Describer-Profile.

Chat-Bild-Uploads (VL Direct, Symposion) nutzen diesen Pfad nicht: Dort wählt
`_vl_choice` ([`aifred/state/_agent_config_mixin.py`](../../../aifred/state/_agent_config_mixin.py))
ein vision-fähiges Hauptmodell selbst, sonst das Modell der Vision-Rolle.

## Kernidee

Das Chat-LLM läuft über **llama-swap** und belegt den Großteil des VRAM.
Für Bildanalysen gibt es dedizierte **Describer-Profile** (`<base>-visiond`
in der llama-swap-config): schlanke Instanzen desselben Vision-Modells mit
kleinem Kontext (`-c 24576` = `VLM_NUM_CTX` in
[`aifred/lib/config.py`](../../../aifred/lib/config.py)), ohne Draft-Modell,
per `CUDA_VISIBLE_DEVICES` auf die Side-Channel-Karte gepinnt. Sie laufen in
der llama-swap-Gruppe `vision` **parallel** zum Chat-LLM — kein Modell-Swap
für eine Bildbeschreibung.

Die Profile pflegt der Autoscan
([`scripts/llama-swap-autoscan.py`](../../../scripts/llama-swap-autoscan.py)):
`ensure_visiond_profiles` legt für jedes Modell mit passendem
`mmproj-*.gguf` ein `-visiond`-Profil an (GPU-Pin über `pick_vlm_gpu`,
Mitglied der `vision`-Gruppe), `enforce_visiond_ctx` zieht das `-c` aller
`-visiond`-Profile auf `VLM_NUM_CTX` (SSOT), und `cleanup_stale_vlm_variants`
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

## Routing-Präzedenz

SSOT ist die Modellwahl in `analyze_sequence`
([`aifred/lib/vision_analyzer.py`](../../../aifred/lib/vision_analyzer.py))
für alle ihre Caller (Vigilantia-Watcher, Türsteher, Event-Analyse, Bulk,
Sandbox, Vision-Tool), erster Treffer gewinnt:

1. **Das Chat-LLM beschreibt selbst** — `self_describer_profile`
   ([`aifred/lib/vision_routing.py`](../../../aifred/lib/vision_routing.py)):
   Das eingestellte Vision-Modell ist dasselbe Modell wie das Chat-LLM
   (`same_model`, namens-normalisiert und ohne Varianten-Suffixe), und dessen
   llama-swap-Profil lädt einen eigenen Vision-Encoder (`has_native_vision`:
   llama.cpp `--mmproj`, vLLM ohne `--language-model-only`). Zurück kommt das
   tatsächlich geladene Profil (z.B. eine `-speed`-Variante); läuft ein anderes
   Modell, greift dieser Weg nicht. Kein zweiter Load desselben Modells — die
   Beschreibung serialisiert mit dem Chat in dessen einzigem Slot (`-np 1`).
2. **`<vision-modell>-visiond`-Profil existiert → dorthin routen** —
   `visiond_profile_for`. Der bevorzugte Pfad: gleicher Backend, nur der
   Profilname wechselt. Varianten-Suffixe der Rolle werden vor dem Lookup
   gestrippt (`strip_variant_suffixes`), und auch Ollama-Schreibweisen
   (`qwen3-vl:4b-instruct-q8_0`) matchen namens-normalisiert — so laufen ALLE
   Vigilantia-Pfade über diese Auflösung, ohne dass ihre Plugin-Settings
   angefasst wurden.
   `prewarm_vlm` (vision_mode „live") lädt das Describer-Profil per
   llama-swap-Request; `check_vlm_fits` (Bulk-Worker) entfällt bei
   Describer-Pfad (Reserve-Slot ist die Garantie).
3. **Sonst** bleibt das Modell unverändert. Dispatch über `has_native_vision`:
   Ein llama-swap-Modell mit eigenem Vision-Encoder beschreibt über llama-swap,
   alles andere geht an Ollama (`_analyze_via_ollama`, Bestands-Pfad für Setups
   ohne `-visiond`-Profile).

Sandbox-Screenshots (`describe_sandbox_screenshots`,
[`aifred/lib/sandbox.py`](../../../aifred/lib/sandbox.py)) bevorzugen die
konfigurierte Vision-Rolle (deren `-visiond`-Profil); ein vision-fähiges
Hauptmodell (`is_vision_model_sync`,
[`aifred/lib/vision_utils.py`](../../../aifred/lib/vision_utils.py))
beschreibt nur, wenn keine Vision-Rolle konfiguriert ist — sonst überschriebe
jeder Describe-Call den Chat-Kontext im einzigen Slot. `is_vision_model_sync`
entscheidet bei llama-swap-Einträgen danach, was ihr cmd lädt
(`has_native_vision`), bei anderen Ids per Namensmuster auf der Basis-Id
(`strip_variant_suffixes` — die Suffixe `-vlm-qwen3vl4b` und `-vllm` enthalten
„vl" und würden sonst Textmodelle fälschlich als vision-fähig einstufen).

`maybe_route_to_ollama` (`vision_routing.py`) bildet dieselbe Reihenfolge
(`-visiond`-Profil vor Ollama-Side-Channel) für ein
`(backend_url, backend_type, model)`-Tupel ab; im Produktivcode hat es derzeit
keinen Aufrufer, nur Tests.

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
räumt AIfred selbst: `_free_gpus_for_load`
([`aifred/backends/base.py`](../../../aifred/backends/base.py))
läuft im `_pre_request_check` der llama.cpp- und vLLM-Backends bei jeder
Anfrage und liest llama-swaps `/running`. Läuft das Ziel-Modell bereits, wird
nichts angerührt. Sonst löst die Anfrage einen Load aus (auch das Neuladen
desselben Modells nach seiner ttl), und der Guard gibt zuerst Whispers
GPU-Worker frei (`release_whisper_gpu`; eine laufende Transkription bekommt eine
Gnadenfrist), dann kümmert er sich um die Beiwagen. Profile mit `-vlm-`-Marker
(Reserve per Kalibrierung) und die Beiwagen selbst räumen keine Beiwagen; ist
kein Beiwagen (`-visiond`, `-embed`) geladen, ist nichts weiter zu tun. Danach
vergleicht er die GPUs des Ziel-Eintrags und jedes laufenden Beiwagens
(`CUDA_VISIBLE_DEVICES` aus der llama-swap-config, `entry_gpu_uuids`) und
entlädt jeden Beiwagen mit gemeinsamer GPU via
`POST /api/models/unload/<beiwagen>`, bevor der Load startet. Lassen sich die
GPUs nicht bestimmen, entlädt er alle Beiwagen. llama-swap kennt keine
VRAM-/Profil-Semantik — die Entscheidung trifft die Instanz, die die Profile
versteht.

## Badge-Logik

`_build_vision_rich`
([`aifred/state/_backend_mixin.py`](../../../aifred/state/_backend_mixin.py))
zeigt `🧠 Chat-LLM`, wenn das Vision-Modell das Chat-LLM selbst mit eigenem
Vision-Encoder ist (Selbst-Describer, zuerst geprüft); sonst `⚡ No Swap`, wenn
(a) ein `-visiond`-Profil existiert (Gruppen-Semantik garantiert Parallelität)
**oder** (b) der Ollama-Side-Channel greift und das `-vlm-<key>`-Reserve-Profil
(oder dessen `-speed`-Variante) für das aktuelle Chat-LLM kalibriert ist; sonst
`🔄 Swap`. `vlm_key_for_model` matcht
namens-normalisiert — auch die llama-swap-Schreibweise
(`Qwen3VL-4B-Instruct-Q8_0`) aktiviert die Reserve-Automatik.

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
- Dynamische Platzierung (Describer auf die Karte mit dem meisten freien
  VRAM statt festem Pin) via generierter Platzierungs-Varianten.
- Ollama-Dienste stilllegen (sudo, User-Entscheid) — seit Paket 2 ruft
  kein Vision-Pfad mehr Ollama, solange Describer-Profile existieren.
