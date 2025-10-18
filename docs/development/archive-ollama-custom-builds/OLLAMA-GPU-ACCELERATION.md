# Ollama GPU-Beschleunigung mit AMD Radeon 780M

**Status:** ✅ Erfolgreich implementiert
**Datum:** 16. Oktober 2025
**GPU:** AMD Radeon 780M (gfx1103, RDNA 3 iGPU)
**System:** Ubuntu 22.04, Linux 6.14.0-33-generic

## Zusammenfassung

Dieses Dokument beschreibt die erfolgreiche Implementierung der GPU-Beschleunigung für Ollama auf einem System mit AMD Radeon 780M integrierter Grafik. Nach mehreren Tagen intensiver Arbeit wurde eine funktionsfähige Lösung entwickelt, die GPU-Beschleunigung mit 15.3 GiB VRAM ermöglicht.

## Erreichte Ergebnisse

### GPU-Erkennung und VRAM
```
level=INFO msg="inference compute" compute=gfx1100 total="15.3 GiB" available="15.3 GiB"
load_tensors: offloaded 25/25 layers to GPU
```

### Performance-Benchmarks

**Test mit qwen2.5:0.5b:**
- CPU: 1.184 Sekunden
- GPU: 0.378 Sekunden
- **Speedup: 3.1x schneller**

**Test mit qwen3:8b:**
- CPU: 22.995 Sekunden
- GPU: 20.711 Sekunden
- **Speedup: 1.11x schneller**

### Technische Details

- **Ollama Version:** v0.12.5 (aus Docker Image extrahiert)
- **ROCm Version:** 6.3.0 (mit vollständigen Bibliotheken aus Docker)
- **GPU Architektur:** gfx1103 (als gfx1100 gemeldet via HSA Override)
- **Bibliotheksgröße:** 2.3 GB ROCm-Bibliotheken
- **Layer Offloading:** 25/25 Schichten auf GPU (100%)

## Architektur-Übersicht

```
┌─────────────────────────────────────────────────────┐
│              Ollama Service (systemd)               │
│  Environment: HSA_OVERRIDE_GFX_VERSION=11.0.0      │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────┐
│         /usr/local/bin/ollama (v0.12.5)            │
│              (aus Docker extrahiert)                │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────┐
│        /usr/local/lib/ollama/rocm/                 │
│                                                     │
│  ├── libggml-hip.so (448 MB)                      │
│  ├── libamdhip64.so.6.3.60303 (22 MB)             │
│  ├── libhsa-runtime64.so.1.14.60303 (3.2 MB)      │
│  ├── librocblas.so.4.3.60303 (72 MB)              │
│  ├── librocsolver.so.0.3.60303 (1.6 GB)           │
│  └── [weitere ROCm 6.3 Bibliotheken]              │
│                                                     │
│         (vollständig aus Docker kopiert)            │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────┐
│           AMD Radeon 780M (gfx1103)                │
│              15.3 GiB shared VRAM                  │
│            (aus 32 GB System-RAM)                  │
└─────────────────────────────────────────────────────┘
```

## Warum diese Lösung funktioniert

### Der entscheidende Durchbruch

Nach tagelangem Debugging stellte sich heraus, dass die **Library-Versionen** das Problem waren, nicht der Code oder die Hardware:

1. **Custom-Built Binaries** schlugen fehl (0 VRAM gemeldet)
2. **Docker Image Test** zeigte: Hardware funktioniert perfekt (15.3 GiB erkannt)
3. **Root Cause:** Host-System hatte inkompatible/unvollständige ROCm-Bibliotheken
4. **Lösung:** Komplette Bibliotheksumgebung aus Docker extrahieren und auf Host kopieren

### Warum Docker-Bibliotheken?

Das offizielle `ollama/ollama:rocm` Docker-Image enthält:
- Exakt getestete und kompatible Bibliotheksversionen
- Vollständige ROCm 6.3 Runtime-Umgebung (2.3 GB)
- Keine Abhängigkeitskonflikte mit System-Paketen
- Garantierte Kompatibilität zwischen Binary und Bibliotheken

## Kritische Erfolgsfaktoren

### 1. HSA_OVERRIDE_GFX_VERSION=11.0.0

Die AMD Radeon 780M ist **gfx1103**, aber ROCm arbeitet am besten mit:
```bash
HSA_OVERRIDE_GFX_VERSION=11.0.0  # Funktioniert ✅
# NICHT 11.0.2 oder 11.0.3!
```

### 2. Vollständige Bibliotheksumgebung

**Kritisch:** Alle Bibliotheken müssen aus der gleichen Quelle stammen!

```
/usr/local/lib/ollama/rocm/  (2.3 GB total)
├── libggml-hip.so                    # 448 MB - GPU Kernel
├── libamdhip64.so.6.3.60303          # 22 MB  - HIP Runtime
├── libhsa-runtime64.so.1.14.60303    # 3.2 MB - HSA Runtime
├── librocblas.so.4.3.60303           # 72 MB  - Linear Algebra
├── librocsolver.so.0.3.60303         # 1.6 GB - Solver
├── libhipblas.so.2.3.60303           # 1.1 MB
├── libhipblaslt.so.0.10.60303        # 7.2 MB
└── [60+ weitere Bibliotheken]
```

### 3. Systemd Service Konfiguration

Minimale, saubere Konfiguration ohne störende Variablen:

```ini
[Service]
ExecStart=/usr/local/bin/ollama serve
User=ollama
Group=ollama
Environment="PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="OLLAMA_KEEP_ALIVE=15m"
Environment="HSA_OVERRIDE_GFX_VERSION=11.0.0"
Environment="OLLAMA_DEBUG=1"
```

**Entfernte Variablen** (verursachten Probleme):
- `ROC_ENABLE_PRE_VEGA=1` - Unnötig für moderne GPU
- `GPU_DEVICE_ORDINAL=0` - Verursachte Konflikte
- `HIP_VISIBLE_DEVICES=0` - Redundant

## Installation und Konfiguration

Siehe separate Dokumente:
- [Detaillierte Installationsanleitung](OLLAMA-GPU-INSTALLATION.md)
- [Troubleshooting Guide](OLLAMA-GPU-TROUBLESHOOTING.md)

## Verifikation

### GPU-Status überprüfen

```bash
# Systemd Service Logs
sudo journalctl -u ollama -n 50 --no-pager

# Suche nach dieser Zeile:
# level=INFO msg="inference compute" compute=gfx1100 total="15.3 GiB" available="15.3 GiB"
```

### Test mit Modell

```bash
# Einfacher Test
ollama run qwen2.5:0.5b "test"

# In den Logs sollte erscheinen:
# load_tensors: offloaded 25/25 layers to GPU
```

### Performance-Test

```bash
# CPU-Test (GPU temporär deaktivieren)
sudo systemctl stop ollama
sudo mv /usr/local/lib/ollama/rocm /tmp/ollama-rocm-backup
sudo systemctl start ollama
time ollama run qwen3:8b "sage guten abend"

# GPU-Test (GPU wieder aktivieren)
sudo systemctl stop ollama
sudo mv /tmp/ollama-rocm-backup /usr/local/lib/ollama/rocm
sudo systemctl start ollama
time ollama run qwen3:8b "sage guten abend"
```

## Wichtige Dateien

| Datei | Zweck | Größe |
|-------|-------|-------|
| `/usr/local/bin/ollama` | Ollama v0.12.5 Binary | 33 MB |
| `/usr/local/lib/ollama/rocm/` | ROCm 6.3 Bibliotheken | 2.3 GB |
| `/etc/systemd/system/ollama.service` | Service-Konfiguration | 1 KB |
| `/usr/local/lib/ollama/libggml-hip.so` | GPU Compute Kernel | 448 MB |

## Lessons Learned

### 1. Docker als Bibliotheksquelle

**Problem:** System-ROCm-Pakete sind oft veraltet oder inkompatibel
**Lösung:** Docker-Images enthalten getestete, funktionierende Kombinationen
**Vorteil:** Keine Paketkonflikte, garantierte Kompatibilität

### 2. Version Matters

- **v0.12.3:** Blockiert iGPUs komplett
- **v0.12.6-rc0:** VRAM-Detection Bug (0 VRAM gemeldet)
- **v0.12.5:** Funktioniert perfekt mit korrekten Bibliotheken

### 3. Minimal ist besser

Weniger Environment-Variablen = weniger Fehlerquellen
Nur `HSA_OVERRIDE_GFX_VERSION=11.0.0` ist nötig!

### 4. Systematisches Testen

Der Durchbruch kam durch:
1. Test mit Docker-Image → Beweis, dass Hardware funktioniert
2. Vergleich Host vs. Docker → Identifikation: Bibliotheken sind unterschiedlich
3. Extraktion und Kopie → Problem gelöst!

## Performance-Charakteristiken

### Kleine Modelle (< 1B Parameter)
- **Starker GPU-Vorteil:** 3x schneller
- Layer-Overhead minimal
- Volle GPU-Auslastung möglich

### Mittlere Modelle (7-8B Parameter)
- **Geringer GPU-Vorteil:** ~1.1x schneller
- CPU ist konkurrenzfähig
- Shared Memory könnte Bottleneck sein

### Erwartungen für große Modelle (> 30B)
- GPU-Vorteil sollte wieder steigen
- CPU würde zu langsam werden
- GPU Memory-Management kritischer

## Nächste Schritte (Optional)

- [ ] Test mit noch größeren Modellen (32B, 70B)
- [ ] Performance-Tuning für spezifische Workloads
- [ ] Monitoring der GPU-Auslastung mit `rocm-smi`
- [ ] Integration mit bestehenden Monitoring-Dashboards

## Support und Debugging

Bei Problemen:
1. Logs überprüfen: `sudo journalctl -u ollama -n 100`
2. GPU-Status: `HSA_OVERRIDE_GFX_VERSION=11.0.0 rocminfo | grep gfx`
3. Bibliotheken: `ls -lh /usr/local/lib/ollama/rocm/`
4. Siehe [Troubleshooting Guide](OLLAMA-GPU-TROUBLESHOOTING.md)

## Autoren und Danksagung

Entwickelt im Oktober 2025 durch systematisches Debugging und Testing.

Besonderer Dank an:
- Ollama Docker Team für stabile Docker-Images
- AMD ROCm Team für GPU-Unterstützung
- Community für Hinweise auf iGPU-Limitierungen

---

**Letzte Aktualisierung:** 16. Oktober 2025
**Status:** Produktionsbereit ✅
