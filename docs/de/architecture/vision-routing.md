# Vision-Routing: Der llama.cpp-Describer-Pfad

Wie eine Bildanfrage (Chat-Bild-Upload, Sandbox-Screenshot) auf ein
Vision-Modell verteilt wird, ohne das geladene Chat-LLM aus dem VRAM zu
verdrängen. Stand: Vision-Umbau Paket 1 (2026-08-16) — der Side-Channel
läuft primär über llama.cpp/llama-swap; Ollama bleibt als Bestands-Pfad
für Setups ohne Describer-Profile.

## Kernidee

Das Chat-LLM läuft über **llama-swap** und belegt den Großteil des VRAM.
Für Bildanalysen gibt es dedizierte **Describer-Profile** (`<base>-visiond`
in der llama-swap-config): schlanke Instanzen desselben Vision-Modells mit
kleinem Kontext (`-c 9216` = `VLM_NUM_CTX`), ohne Draft-Flags, gepinnt auf
die VRAM-Reserve-Karte. Sie laufen in der llama-swap-Gruppe `vision`
**parallel** zum Chat-LLM — kein Modell-Swap für eine Bildbeschreibung.

Die Reserve stellt die Kalibrierung: Sobald ein Vision-Modell aktiv ist,
wählt `resolve_variant_suffix`
([`aifred/lib/calibration/llamaswap_io.py`](../../../aifred/lib/calibration/llamaswap_io.py))
für das Chat-LLM das `<base>-vlm-<key>`-Profil, das den Reserve-Slot
(z.B. ~8 GB auf einer V100) freilässt.

## Routing-Präzedenz

`maybe_route_to_ollama` / `visiond_profile_for`
([`aifred/lib/vision_routing.py`](../../../aifred/lib/vision_routing.py)),
erster Treffer gewinnt:

1. **Vision-fähiges Hauptmodell beschreibt selbst** (nur Sandbox-Pfad,
   `describe_sandbox_screenshots`): Das Modell ist schon geladen — kein
   zweiter Load nötig. Erkennung über `is_vision_model_sync`
   ([`aifred/lib/vision_utils.py`](../../../aifred/lib/vision_utils.py)):
   mmproj-Check auf der vollen Profil-Id, Namens-Heuristik auf der
   Basis-Id (`strip_variant_suffixes` — das Varianten-Suffix
   `-vlm-qwen3vl4b` enthält „vl" und würde sonst z.B. DeepSeek-Varianten
   fälschlich als vision-fähig einstufen).
2. **`<vision-modell>-visiond`-Profil existiert → dorthin routen.** Der
   bevorzugte Pfad: gleicher Backend, nur der Profilname wechselt.
   Varianten-Suffixe der Rolle werden vor dem Lookup gestrippt.
3. **Ollama-Pendant existiert → Ollama-Side-Channel** (Bestands-Pfad,
   z.B. für Setups ohne `-visiond`-Profile; Vigilantia-Watcher nutzt bis
   Paket 2 weiterhin diesen Weg).
4. **Sonst:** unverändert durchreichen — klassischer llama-swap-Pfad mit
   Swap.

## Entlade-Semantik (llama-swap-Gruppen)

```yaml
groups:
  vision:
    exclusive: false   # Describer-Load verdrängt das Chat-LLM nicht
    swap: true         # Describer verdrängen sich gegenseitig (1 Slot)
    persistent: true   # llama-swap entlädt Describer NIE von sich aus
  main:
    exclusive: true    # Chat-Modelle verdrängen sich wie gehabt
    swap: true
```

Wichtig: llama-swaps `exclusive` wirkt **request-basiert** — ohne
`persistent` würde jeder Request an das (bereits laufende!) Chat-LLM den
Describer entladen (empirisch verifiziert 2026-08-16). Mit `persistent`
gilt: **Passende Profilkombinationen koexistieren unbegrenzt, aufgeräumt
wird nur per `ttl`** (900 s idle).

Den einzigen echten Konflikt-Fall — Wechsel auf ein Chat-Profil **ohne**
`-vlm-`-Reserve, das die volle Karte brauchen kann — räumt AIfred selbst:
`_evict_visiond_if_conflicting`
([`aifred/backends/llamacpp.py`](../../../aifred/backends/llamacpp.py))
läuft im `_pre_request_check` (nur bei Modellwechsel, gecacht), prüft
llama-swaps `/running` und entlädt die Describer via
`POST /api/models/unload`, bevor der Load startet. Läuft das Ziel-Modell
bereits, wird nichts angerührt. llama-swap kennt keine VRAM-/Profil-
Semantik — die Entscheidung trifft die Instanz, die die Profile versteht.

## Badge-Logik

`_build_vision_rich`
([`aifred/state/_backend_mixin.py`](../../../aifred/state/_backend_mixin.py))
zeigt `⚡ No Swap`, wenn (a) ein `-visiond`-Profil existiert
(Gruppen-Semantik garantiert Parallelität) **oder** (b) der
Ollama-Side-Channel greift und das `-vlm-<key>`-Reserve-Profil für das
aktuelle Chat-LLM kalibriert ist. `vlm_key_for_model` matcht
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

## Offene Punkte (Paket 2/3)

- Vigilantia-Watcher auf `-visiond`-Profile migrieren (nutzt noch Ollama).
- Dynamische Platzierung (Describer auf die Karte mit dem meisten freien
  VRAM statt fester Reserve-Karte) via generierter Platzierungs-Varianten.
- `-visiond`-Profile aus der Kalibrierung generieren statt handgepflegt;
  `-vlm-`-Reserve-Varianten nur noch anlegen, wenn das Chat-LLM sie
  wirklich braucht.
- Ollama-Stilllegung (inkl. Qwen3VL-4B-Altmodell), sobald der Watcher
  migriert ist.
