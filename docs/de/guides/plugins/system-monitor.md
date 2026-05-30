# System Monitor Plugin

**Datei:** `aifred/plugins/tools/system_monitor/`

Gibt Auskunft über den aktuellen Systemzustand: CPU-Last, RAM/Swap, GPU-VRAM und
-Temperatur, Festplattenbelegung, Uptime und Sensor-Temperaturen. Read-only — es
führt nur Abfrage-Befehle aus (`uptime`, `free`, `df`, `nvidia-smi`, `sensors`)
und verändert nichts.

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `system_status` | Hardware-Status abfragen (CPU, RAM, GPU, Disk, Temperatur, Uptime) | READONLY |

## Parameter

`components` — Kommagetrennte Liste der abzufragenden Teile: `cpu`, `ram`
(Alias `memory`), `gpu`, `disk`, `temp`, `uptime`, oder `all` (Standard).

## Was die einzelnen Komponenten liefern

- **cpu / uptime** — Uptime-String, Anzahl Cores, Load-Average über 1/5/15 Minuten
- **ram / memory** — RAM total/belegt/frei/verfügbar plus Swap (aus `free -h --si`)
- **gpu** — pro GPU: Index, Name, VRAM total/belegt/frei (MB), Temperatur,
  Auslastung (aus `nvidia-smi`; meldet einen Fehler, wenn `nvidia-smi` fehlt)
- **disk** — Belegung der Mounts `/` und `/home` (Größe, belegt, verfügbar, Prozent)
- **temp** — wichtige Sensor-Temperaturen (aus `sensors -j`; wird stillschweigend
  übersprungen, wenn `sensors` nicht installiert ist)

## Beispiel-Nutzung

- „Wie viel VRAM ist frei?" → `system_status(components="gpu")`
- „Zeig CPU und RAM" → `system_status(components="cpu,ram")`
- „Kompletter Systemstatus" → `system_status(components="all")`

Die Ausgabe erfolgt als JSON; der Agent ist angewiesen, sie als kompakte Tabelle
mit Auslastung (belegt / gesamt) darzustellen, nicht nur als Gesamtwerte.
