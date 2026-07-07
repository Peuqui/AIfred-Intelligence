# Vision-Routing: Swap vs. No Swap

Wie eine Bildanfrage auf ein Backend verteilt wird und wann sie das
geladene Chat-Modell aus dem VRAM verdrängt.

## Kernidee

Das Chat-LLM (z.B. ein großes Qwen3.5/3.6) läuft über **llama-swap** und
belegt den Großteil des VRAM. Eine Bildanalyse braucht ein Vision-Modell.
Es gibt zwei Wege, und der Unterschied ist im Alltag spürbar:

- **No Swap (parallel):** Der Vision-Call läuft über die **Ollama**-
  Side-Channel-Instanz auf einer reservierten GPU. Das Chat-Modell bleibt
  komplett geladen — kein Reload, kein Lag.
- **Swap (Verdrängung):** llama-swap muss das Chat-Modell aus dem VRAM
  werfen, das Vision-Modell laden, die Bildanalyse machen und danach das
  Chat-Modell zurückladen. Kostet je nach Modellgröße Sekunden bis Minuten.

Das Dropdown „Vision-LLM" kennzeichnet jeden Eintrag entsprechend:
`⚡ No Swap` (grün) oder `🔄 Swap` (amber).

## Die drei Fälle

**1. Vision = Qwen3VL (Ollama-Pendant) + `-vlm`-Profil kalibriert → No Swap.**
Der Idealfall. `maybe_route_to_ollama`
([`aifred/lib/vision_routing.py`](../../../aifred/lib/vision_routing.py))
findet zum gewählten Modell (`Qwen3VL-4B-Instruct-Q8_0`) das Ollama-Pendant
(`qwen3-vl:4b-instruct-q8_0`) und leitet den Call dorthin um. Voraussetzung
für den parallelen Betrieb ist, dass AIfred das llama-swap-Profil
`<base>-vlm-<key>` geladen hat — es hält die VRAM-Reserve (z.B. 7,7 GB auf
der V100) frei, auf der die Ollama-Instanz läuft. Dieses Profil wird über
`resolve_variant_suffix`
([`aifred/lib/calibration/llamaswap_io.py`](../../../aifred/lib/calibration/llamaswap_io.py))
gewählt, sobald ein VLM aktiv ist.

**2. Vision = Qwen3.5/3.6 (kein Ollama-Pendant) → Swap.**
Das Vision-Modell ist selbst ein llama-swap-Modell. Die Gruppe `main` ist
`exclusive: true, swap: true`, also kann nur ein Modell aus der Gruppe
gleichzeitig geladen sein → das Chat-Modell wird verdrängt. Das VLM-Profil
hilft hier nicht (es reserviert Platz für den *Ollama*-Side-Channel, nicht
für ein llama-swap-internes Vision-Modell).

**3. Vision = Qwen3VL, aber `-vlm`-Profil NICHT kalibriert → Swap.**
Ohne kalibriertes `<base>-vlm-<key>`-Profil fällt AIfred auf das Base-Profil
zurück (voller Kontext, keine Reserve). Der Ollama-Qwen3VL findet dann
keinen freien VRAM → es käme zu OOM bzw. einem erzwungenen Profilwechsel
(auch ein Swap). Deshalb ist „No Swap" an **beide** Bedingungen gekoppelt:
Ollama-Pendant **und** kalibriertes `-vlm`-Profil für das aktuelle Chat-LLM.

## Badge-Logik im Code

`vision_swap_status` (Ollama-Pendant vorhanden?) und `vlm_key_for_model`
(Vision-Modell → Kalibrier-Key) leben in
[`aifred/lib/vision_routing.py`](../../../aifred/lib/vision_routing.py).
`_build_vision_rich`
([`aifred/state/_backend_mixin.py`](../../../aifred/state/_backend_mixin.py))
kombiniert beides mit einem Existenz-Check des `-vlm`-Profils in der
llama-swap-config und legt das fertige Badge (Name + `⚡ No Swap` /
`🔄 Swap` + Farbe) im State ab. Weil der Profil-Check vom aktuellen
Chat-LLM abhängt, werden die Badges beim LLM-Wechsel (`set_aifred_model`)
neu berechnet.

## Konsequenz für den Betrieb

Für swap-freien Bildbetrieb neben einem großen Chat-LLM: ein `Qwen3VL`-
Modell als Vision-LLM wählen **und** dessen `-vlm`-Kombo mit dem Chat-LLM
kalibrieren. Zeigt das Dropdown `🔄 Swap` an einem Qwen3VL, fehlt die
Kalibrierung — ein Lauf in der Kalibrier-Matrix (Zeile „Vigilantia 4B/8B")
macht daraus `⚡ No Swap`.
