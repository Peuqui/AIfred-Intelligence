# System Monitor Plugin

**Datei:** `aifred/plugins/tools/system_monitor/`

Gibt Auskunft über den aktuellen Systemzustand: CPU, RAM, GPU, Festplatte, Temperatur.

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `system_status` | Hardware-Status abfragen (CPU, RAM, GPU, Disk, Temperatur) | READONLY |

## Parameter

`components` — Kommagetrennt: `cpu`, `ram`, `gpu`, `disk`, `temp`, `uptime`, oder `all` (Standard).

## Beispielausgabe

- CPU: 16 Cores, Load 1.28/0.84/0.77
- RAM: 32 GB total, 9 GB used, 22 GB available
- GPUs: 4x (Tesla P40 × 3 + RTX 8000), VRAM, Temperatur, Auslastung
- Disk: Mount, Größe, Belegung in Prozent
- Sensoren: CPU-Temperatur, GPU-Temperatur, NVMe-Temperatur
