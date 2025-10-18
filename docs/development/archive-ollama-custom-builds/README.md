# Ollama GPU-Beschleunigung Dokumentation

**AMD Radeon 780M (gfx1103) mit ROCm 6.3 auf Ubuntu 22.04**

## Status: ✅ Erfolgreich implementiert

GPU-Beschleunigung für Ollama wurde erfolgreich auf einem System mit AMD Radeon 780M integrierter Grafik implementiert und getestet.

## Schnellübersicht

- **GPU:** AMD Radeon 780M (gfx1103, RDNA 3 iGPU)
- **VRAM:** 15.3 GiB (shared mit System-RAM)
- **ROCm:** Version 6.3.0
- **Ollama:** v0.12.5 (aus Docker extrahiert)
- **Performance:** 3x schneller bei kleinen Modellen, ~1.1x bei mittleren

## Dokumentations-Struktur

### 1. [OLLAMA-GPU-ACCELERATION.md](OLLAMA-GPU-ACCELERATION.md)
**Haupt-Dokumentation**

Enthält:
- ✅ Zusammenfassung der erreichten Ergebnisse
- 📊 Performance-Benchmarks (CPU vs. GPU)
- 🏗️ Architektur-Übersicht
- 🔑 Kritische Erfolgsfaktoren
- 📚 Lessons Learned
- 🎯 Best Practices

**Lies dies zuerst** für einen vollständigen Überblick über das Projekt.

### 2. [OLLAMA-GPU-INSTALLATION.md](OLLAMA-GPU-INSTALLATION.md)
**Schritt-für-Schritt Installationsanleitung**

Enthält:
- 📋 Voraussetzungen und System-Checks
- 🔧 ROCm 6.3 Installation
- 📦 Ollama Binary-Extraktion aus Docker
- 📚 Bibliotheken-Extraktion (2.3 GB)
- ⚙️ Systemd Service-Konfiguration
- ✔️ Verifikation und Tests
- 📈 Performance-Benchmarking

**Folge dieser Anleitung** für eine Neuinstallation.

### 3. [OLLAMA-GPU-TROUBLESHOOTING.md](OLLAMA-GPU-TROUBLESHOOTING.md)
**Umfassender Troubleshooting-Guide**

Enthält:
- 🐛 Alle aufgetretenen Fehler mit Lösungen
- 🔍 Debugging-Werkzeuge und -Techniken
- ⚠️ Häufige Probleme und Fixes
- 📝 Checkliste für Fehlersuche
- 💡 Erweiterte Diagnose-Methoden

**Konsultiere diesen Guide** bei Problemen oder Fehlern.

## Wichtigste Erkenntnisse

### Der entscheidende Durchbruch

Das Kernproblem war **Library-Inkompatibilität**, nicht Hardware oder Code:

```
❌ Custom-Built Libraries → 0 VRAM erkannt
✅ Docker-Image Libraries → 15.3 GiB erkannt
```

**Lösung:** Komplette Bibliotheksumgebung aus `ollama/ollama:rocm` Docker-Image extrahieren.

### Kritische Konfiguration

```bash
# /etc/systemd/system/ollama.service
Environment="HSA_OVERRIDE_GFX_VERSION=11.0.0"  # KRITISCH!

# NICHT 11.0.2 oder 11.0.3!
```

### Warum Docker-Bibliotheken?

1. **Getestet und kompatibel** - Exakte Version-Matches
2. **Vollständig** - Alle Dependencies (2.3 GB)
3. **Konfliktfrei** - Unabhängig von System-Paketen
4. **Funktioniert garantiert** - Offizielle Ollama-Builds

## Performance-Ergebnisse

| Modell | Größe | CPU | GPU | Speedup |
|--------|-------|-----|-----|---------|
| qwen2.5:0.5b | 397 MB | 1.18s | 0.38s | **3.1x** ✅ |
| qwen3:8b | 5.2 GB | 23.0s | 20.7s | **1.1x** ⚠️ |

**Interpretation:**
- Kleine Modelle (< 1B): Starker GPU-Vorteil
- Mittlere Modelle (7-8B): Geringer Vorteil (iGPU shared memory Bottleneck)
- Große Modelle (> 30B): Noch nicht getestet

## Quick-Start für Verifizierung

```bash
# 1. Service-Status
systemctl status ollama

# 2. GPU-Erkennung
journalctl -u ollama -n 50 --no-pager | grep "15.3 GiB"
# Erwartung: available="15.3 GiB" ✅

# 3. Modell-Test
ollama run qwen2.5:0.5b "test"

# 4. Layer-Offloading
journalctl -u ollama -n 100 --no-pager | grep offload
# Erwartung: offloaded 25/25 layers to GPU ✅
```

## Wichtige Dateien auf diesem System

| Datei/Verzeichnis | Zweck | Größe |
|-------------------|-------|-------|
| `/usr/local/bin/ollama` | Ollama v0.12.5 Binary | 33 MB |
| `/usr/local/lib/ollama/rocm/` | ROCm 6.3 Bibliotheken | 2.3 GB |
| `/etc/systemd/system/ollama.service` | Service-Konfiguration | 1 KB |
| `/opt/rocm-6.3.0/` | ROCm 6.3 Installation | ~21 GB |

## Anwendungen, die GPU nutzen

### AIfred-Intelligence
- **Pfad:** `~/Projekte/AIfred-Intelligence/`
- **Modelle:** qwen3:1.7b (Haupt), qwen3:4b (Automatik)
- **Status:** Läuft mit GPU-Beschleunigung ✅

Weitere Projekte können Ollama nutzen - alle profitieren automatisch von GPU.

## Wartung

### Logs überprüfen
```bash
journalctl -u ollama -f  # Live-Logs
journalctl -u ollama -n 100 --no-pager  # Letzte 100 Zeilen
```

### Service neu starten
```bash
sudo systemctl restart ollama
```

### GPU-Monitoring
```bash
export HSA_OVERRIDE_GFX_VERSION=11.0.0
watch -n 1 '/opt/rocm-6.3.0/bin/rocm-smi'
```

### GPU deaktivieren (CPU-Only Modus)
```bash
# Einfachste Methode: Environment-Variable
OLLAMA_NUM_GPU=0 ollama run qwen3:8b "test"

# Für Benchmarks:
OLLAMA_NUM_GPU=0 time ollama run qwen3:8b "sage hallo"  # CPU
time ollama run qwen3:8b "sage hallo"                    # GPU
```

Siehe [OLLAMA-GPU-INSTALLATION.md](OLLAMA-GPU-INSTALLATION.md#cpu-only-modus-gpu-deaktivieren) für weitere Methoden.

## Backup-Strategie

### Was sichern?
1. ✅ Binary: `/usr/local/bin/ollama`
2. ✅ Service: `/etc/systemd/system/ollama.service`
3. 📝 Dokumentation: Diese Anleitungen

### Was NICHT sichern?
- ❌ Bibliotheken (2.3 GB) - Bei Bedarf aus Docker neu extrahieren
- ❌ Modelle - Können jederzeit neu heruntergeladen werden

## Weiterführende Ressourcen

- **Ollama Dokumentation:** https://github.com/ollama/ollama
- **ROCm Dokumentation:** https://rocm.docs.amd.com/
- **Docker Hub:** https://hub.docker.com/r/ollama/ollama

## Timeline der Entwicklung

1. **Tag 1-2:** ROCm 6.2.4 Installation, custom Builds → 0 VRAM
2. **Tag 3:** Upgrade auf ROCm 6.3, v0.12.6-rc0 Test → Immer noch 0 VRAM
3. **Tag 4:** Docker-Test → **Durchbruch!** GPU funktioniert in Docker
4. **Tag 5:** Library-Extraktion → ✅ **Erfolg!** 15.3 GiB VRAM
5. **Tag 6:** Benchmarking und Dokumentation

## Autoren und Danksagung

**Entwickelt:** 11.-16. Oktober 2025
**Dokumentiert:** 16. Oktober 2025

Besonderer Dank an:
- Ollama Team für Docker-Images mit funktionierenden Bibliotheken
- AMD ROCm Team für GPU-Support
- Community für Hinweise auf iGPU-Probleme

## Support

Bei Fragen oder Problemen:
1. 📖 Konsultiere [TROUBLESHOOTING.md](OLLAMA-GPU-TROUBLESHOOTING.md)
2. 🔍 Überprüfe Logs: `journalctl -u ollama -n 200`
3. ✅ Nutze Checkliste im Troubleshooting-Guide

---

**Status:** Produktionsbereit ✅
**Letzte Aktualisierung:** 16. Oktober 2025
**Version:** 1.0
