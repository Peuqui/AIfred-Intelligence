# Arbeitspaket-Skizze: RPC-fähige KI-Kalibrierung

> Skizze für die Erweiterung des KI-Kalibrierers (`ai_agent.py`) um
> verteilte Inferenz via llama.cpp RPC (Mini + Aragon). Status: geplant,
> wartet auf reaktiviertes RPC-Setup (Kabel + Aragon).
> Kontext: RPC-Grundlagen und Benchmarks siehe
> [showcase-notes.md](../benchmarks/showcase-notes.md) (Update 2026-02-28).

## Einordnung

RPC-Betrieb ist ein **Forschungssetup, kein Everyday-Modus**. Deshalb:

- RPC-Profile bleiben vom llama-swap-Autoscan ausgenommen
  (`scripts/llama-swap-autoscan.py` überspringt `--rpc`-Profile bewusst).
- Der deterministische Algorithmus (`flow.py`) wird NICHT RPC-fähig gemacht —
  er setzt exakte Per-GPU-Telemetrie via nvidia-smi voraus, die es für
  Remote-Karten nicht gibt.
- Stattdessen: der **KI-Kalibrierer** übernimmt RPC-Profile. Seine
  Trial-and-Error-Schleife mit OOM-Recovery kommt mit der Unschärfe
  „Remote-VRAM nicht direkt messbar" strukturell zurecht.

## Verifizierter Ist-Zustand (Stand 2026-08-06)

| Baustein | Status |
|----------|--------|
| `--rpc` wird an fit-params durchgereicht | ✅ vorhanden (`_GPU_FLAGS` in `projection.py`) |
| fit-params plant RPC-Device korrekt | ✅ live getestet: Ausgabezeile `RPC0 <model> <ctx> <compute>` |
| Parser `_parse_fit_output` | ❌ Regex `^CUDA(\d+)` verwirft `RPC0`-Zeilen stillschweigend |
| GPU-Enumeration (`gpu.py`) | ❌ nur lokales nvidia-smi, Remote-Karte hat keinen Budget-Eintrag |
| RPC-Connectivity-Precheck | ✅ vorhanden im Backend (`backends/llamacpp.py`, Regex auf cmd) |
| llama-server `--rpc`-Flag | ✅ beide Builds mit `GGML_RPC=ON` gebaut (2026-08-06) |
| Worker-Binary | ✅ heißt jetzt `ggml-rpc-server` (umbenannt, alte Doku sagt `rpc-server`) |

## Design-Entscheidung: Detection statt Toggle

**Kein neuer Config-Schalter.** Die SSOT für „dieses Profil nutzt RPC" ist
das `--rpc host:port` im cmd des llama-swap-Profils. Der Kalibrierer
detektiert das Flag (gleiche Regex wie `backends/llamacpp.py` —
als gemeinsamen Helper extrahieren). Der User-„Schalter" ist die
Profilwahl selbst (Zwei-Profil-Muster: `Modell` vs. `Modell-rpc`).

## Arbeitspaket (5 Teilschritte)

1. **Parser erweitern** (`projection.py::_parse_fit_output`):
   `(CUDA|RPC)(\d+)`-Zeilen matchen, Device-Typ mitführen.
   Kleinster Eingriff, größter Hebel — fit-params rechnet bereits richtig.
2. **`--rpc`-Detection + Precheck** im Kalibrier-Einstieg:
   TCP-Check auf alle Endpoints VOR dem ersten estimate (Helper aus
   Backend wiederverwenden). Aragon aus → sauberer Abbruch mit klarer
   Meldung statt kryptischem fit-params-Fehler.
3. **Hardware-Block im System-Prompt** (`ai_agent.py::_hardware_block`):
   synthetischen Eintrag für das RPC-Device ergänzen:
   VRAM-Total (meldet der rpc-server beim Connect, aus der
   fit-params-Device-Liste abgreifen — nicht konfigurieren),
   Free-VRAM „nicht messbar", OOM „zeigt sich nur als Probe-Failure",
   Hinweis „netzwerkgebunden — im Zweifel zuletzt befüllen".
4. **Split-Länge**: `tensor_split`-Validierung auf
   n_lokale_GPUs + n_RPC-Devices erweitern.
5. **Probe-Feedback**: für Remote-Devices „unknown" statt Free-MB.
   Späteres Upgrade (optional): Per-Device-Allokationen aus dem
   llama-server-Ladelog parsen.

## Randbedingungen

- Aragon muss während der Kalibrierung laufen (fit-params verbindet sich
  wirklich mit dem rpc-server — verifiziert).
- Aragon-Seite: llama.cpp mit `-DGGML_CUDA=ON -DGGML_RPC=ON` (Arch 86),
  `ggml-rpc-server -H 0.0.0.0 -p 50052`, statische IP 10.0.0.2/30 auf dem
  USB4-Adapter. Checkliste siehe showcase-notes.md §Einrichtung
  (Binary-Name dort veraltet).
- Mini-Seite: NetworkManager-Profil `rpc-direct` (enp4s0, 10.0.0.1/30)
  existiert bereits.
- Das Kalibrier-LLM kommt wie gehabt aus dem Agent-Editor
  (System-Agent „Calibration" in `data/agents.json`) — keine Kopplung
  an dieses Arbeitspaket.

## Offene Policy-Fragen (vor Implementierung klären)

1. **Fill-Order**: Die 3090 Ti ist die schnellste Karte im Verbund
   (Compute 8.6), hängt aber hinter dem Netzwerk. Nach Speed-Class-Logik
   würde sie zuerst befüllt — sinnvoll ist vermutlich zuletzt.
   Mit Messdaten aus dem realen Betrieb entscheiden.
2. **Sicherheitsmarge Remote**: Annahme „Worker ist dediziert, Karte leer"
   plus welcher Puffer? (Windows/Desktop auf Aragon belegt VRAM.)
3. **Verifier**: Remote-Karte als „unverifizierbar" akzeptieren oder
   Ladelog-Parsing (Teilschritt 5, Upgrade) gleich mitnehmen?
