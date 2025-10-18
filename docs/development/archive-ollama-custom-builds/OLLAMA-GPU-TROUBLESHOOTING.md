# Ollama GPU-Beschleunigung - Troubleshooting Guide

**Ziel:** Systematische Fehleranalyse und Lösungen für häufige Probleme
**Kontext:** AMD Radeon 780M (gfx1103) auf Ubuntu 22.04

Dieser Guide dokumentiert **alle Fehler**, die während der Entwicklung auftraten, mit detaillierten Lösungen.

---

## Inhaltsverzeichnis

1. [GPU wird erkannt, aber 0 VRAM gemeldet](#gpu-wird-erkannt-aber-0-vram-gemeldet)
2. [ROCm Installation Konflikte](#rocm-installation-konflikte)
3. [Falsche HSA_OVERRIDE_GFX_VERSION](#falsche-hsa_override_gfx_version)
4. [Environment Variable Konflikte](#environment-variable-konflikte)
5. [Ollama Version Probleme](#ollama-version-probleme)
6. [Library Version Inkompatibilität](#library-version-inkompatibilität)
7. [Layer Offloading schlägt fehl](#layer-offloading-schlägt-fehl)
8. [Performance schlechter als erwartet](#performance-schlechter-als-erwartet)
9. [Service startet nicht](#service-startet-nicht)
10. [Debugging-Werkzeuge](#debugging-werkzeuge)

---

## GPU wird erkannt, aber 0 VRAM gemeldet

### Symptome

```
level=DEBUG msg="verifying GPU is supported" compute=gfx1100
level=INFO msg="inference compute" total="15.3 GiB" available="0 B"
level=INFO msg="entering low vram mode" "total vram"="0 B"
```

GPU wird gefunden, aber VRAM ist 0 B statt 15.3 GiB.

### Root Cause

Dieses Problem hat **drei mögliche Ursachen**:

#### Ursache 1: Inkompatible Ollama Version

**Problem:** Ollama v0.12.3 und früher blockieren integrierte GPUs (iGPUs) komplett.

**Lösung:**
```bash
# Version überprüfen
/usr/local/bin/ollama --version

# Falls v0.12.3 oder älter:
# Upgrade auf v0.12.5 (aus Docker extrahieren, siehe Installation Guide)
```

#### Ursache 2: VRAM-Detection Bug in v0.12.6-rc0

**Problem:** Release Candidate v0.12.6-rc0 hat einen Bug in der VRAM-Erkennung für iGPUs.

**Symptom:**
```bash
ollama --version
# ollama version is 0.12.6-rc0

# Logs zeigen:
# available="0 B"  # Trotz erkannter GPU!
```

**Lösung:**
```bash
# Downgrade auf v0.12.5
# Extrahiere Binary aus Docker (siehe Installation Guide)
docker cp ollama-extract:/bin/ollama /tmp/ollama-v0.12.5
sudo cp /tmp/ollama-v0.12.5 /usr/local/bin/ollama
sudo systemctl restart ollama
```

#### Ursache 3: Inkompatible Bibliotheken (Hauptursache!)

**Problem:** Host-System hat falsche ROCm-Bibliotheksversionen.

**Symptom:**
```bash
# Binary funktioniert in Docker:
docker run --rm --device /dev/kfd --device /dev/dri ollama/ollama:rocm \
    bash -c "ollama serve & sleep 10 && ollama run qwen2.5:0.5b 'test'"
# ✅ GPU erkannt: total="15.3 GiB" available="15.3 GiB"

# Gleiche Binary auf Host:
/usr/local/bin/ollama serve
# ❌ GPU erkannt: total="15.3 GiB" available="0 B"
```

**Diagnose:**
```bash
# Bibliotheken vergleichen
ls -lh /usr/local/lib/ollama/rocm/
# Falls Dateien fehlen oder andere Versionen haben → Problem!

# Docker-Container Bibliotheken:
docker run --rm ollama/ollama:rocm ls -lh /usr/lib/ollama/rocm/
```

**Lösung:**
```bash
# Komplette Bibliotheksumgebung aus Docker extrahieren
docker create --name ollama-libs ollama/ollama:rocm
docker cp ollama-libs:/usr/lib/ollama /tmp/ollama-libs
docker rm ollama-libs

# Alte Bibliotheken sichern
sudo mv /usr/local/lib/ollama /usr/local/lib/ollama.backup

# Docker-Bibliotheken installieren
sudo cp -r /tmp/ollama-libs /usr/local/lib/ollama
sudo chown -R root:root /usr/local/lib/ollama

# Service neu starten
sudo systemctl restart ollama

# Verifikation
sudo journalctl -u ollama -n 50 | grep "inference compute"
# Erwartung: available="15.3 GiB" ✅
```

---

## ROCm Installation Konflikte

### Symptome

```bash
sudo apt install hip-dev
# Fehler:
# rocm-hip-runtime : Hängt ab von: rocminfo (= 1.0.0.60300-39~22.04)
#                    aber 5.7.1-3build1 soll installiert werden
```

### Root Cause

Mix aus Ubuntu-System-Paketen und ROCm-Repository-Paketen.

### Lösung

```bash
# Alte/konflikterende Pakete entfernen
sudo apt remove hipcc rocminfo

# ROCm 6.3 Pakete mit spezifischen Versionen
sudo apt install \
    hipcc6.3.0 \
    hip-dev \
    rocm-hip-runtime6.3.0 \
    rocm-device-libs6.3.0

# Verifikation
/opt/rocm-6.3.0/bin/hipcc --version
# HIP version: 6.3.60303
```

### hipcc nicht gefunden

```bash
# Symptom
which hipcc
# (keine Ausgabe)

# Lösung
sudo apt install hipcc6.3.0

# Verifikation
/opt/rocm-6.3.0/bin/hipcc --version
```

---

## Falsche HSA_OVERRIDE_GFX_VERSION

### Symptome

GPU wird nicht erkannt oder falsch initialisiert.

### Root Cause

AMD Radeon 780M ist **gfx1103**, aber ROCm braucht **gfx1100** Override.

### Getestete Werte

| Wert | Ergebnis |
|------|----------|
| `11.0.3` | ❌ GPU teilweise erkannt, aber instabil |
| `11.0.2` | ❌ VRAM-Detection Probleme |
| `11.0.0` | ✅ **FUNKTIONIERT!** |

### Lösung

```bash
# In /etc/systemd/system/ollama.service
Environment="HSA_OVERRIDE_GFX_VERSION=11.0.0"

# NICHT:
# Environment="HSA_OVERRIDE_GFX_VERSION=11.0.2"  # Falsch!
# Environment="HSA_OVERRIDE_GFX_VERSION=11.0.3"  # Falsch!

# Service neu laden
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

### Verifikation

```bash
# GPU sollte als gfx1100 erkannt werden
export HSA_OVERRIDE_GFX_VERSION=11.0.0
/opt/rocm-6.3.0/bin/rocminfo | grep -A5 "Marketing Name"

# Erwartung:
#   Marketing Name:          AMD Radeon Graphics
#   Name:                    gfx1100
```

---

## Environment Variable Konflikte

### Symptome

GPU wird erkannt, aber Modelle laufen nicht auf GPU oder stürzen ab.

### Problematische Variablen

Diese Variablen **verursachten Probleme** und wurden entfernt:

```bash
# ❌ NICHT verwenden:
Environment="ROC_ENABLE_PRE_VEGA=1"      # Für alte GPUs, stört moderne
Environment="GPU_DEVICE_ORDINAL=0"       # Verursacht Device-Konflikte
Environment="HIP_VISIBLE_DEVICES=0"      # Redundant, stört Auto-Detection
```

### Funktionierende Konfiguration

```bash
# ✅ Minimale, funktionierende Konfiguration:
Environment="HSA_OVERRIDE_GFX_VERSION=11.0.0"  # Einzige GPU-Variable!
Environment="OLLAMA_DEBUG=1"                   # Optional für Logging
Environment="OLLAMA_KEEP_ALIVE=15m"            # Optional für Performance
```

### Lösung

```bash
# Service-Datei editieren
sudo nano /etc/systemd/system/ollama.service

# Alle GPU-Variablen außer HSA_OVERRIDE_GFX_VERSION entfernen

# Service neu laden
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

---

## Ollama Version Probleme

### Version History und Probleme

| Version | Status | Problem |
|---------|--------|---------|
| v0.12.3 | ❌ Blockiert iGPUs | Hardcoded iGPU-Filter |
| v0.12.4 | ⚠️ Ungetestet | Keine Docker-Images verfügbar |
| **v0.12.5** | ✅ **Funktioniert** | Stabile Version mit iGPU-Support |
| v0.12.6-rc0 | ❌ VRAM Bug | Meldet 0 VRAM für iGPUs |
| v0.12.6 (final) | ⚠️ Ungetestet | Erscheint nach unserem Test |

### Empfehlung

```bash
# ✅ Verwende v0.12.5 aus Docker
docker pull ollama/ollama:rocm
docker create --name ollama-extract ollama/ollama:rocm
docker cp ollama-extract:/bin/ollama /tmp/ollama-v0.12.5
docker rm ollama-extract

sudo cp /tmp/ollama-v0.12.5 /usr/local/bin/ollama
sudo chmod +x /usr/local/bin/ollama

# Verifikation
/usr/local/bin/ollama --version
# ollama version is 0.12.5
```

### Version überprüfen

```bash
# Aktuelle Version
/usr/local/bin/ollama --version

# Logs nach Version durchsuchen
sudo journalctl -u ollama -n 200 | grep version
```

---

## Library Version Inkompatibilität

### Symptome

```bash
# Binary läuft, aber GPU wird nicht genutzt
# ODER
# Segmentation Fault beim Model-Load
# ODER
# "0 B" VRAM trotz erkannter GPU
```

### Root Cause

**Das war das Kernproblem!** Ollama-Binary erwartet exakte Bibliotheksversionen.

### Diagnose

```bash
# Custom-kompilierte Bibliotheken
ls -lh /usr/local/lib/ollama/rocm/libggml-hip.so
# -rwxr-xr-x 1 root root 245M Okt 15 libggml-hip.so  # Custom build

# vs. Docker-Bibliotheken
docker run --rm ollama/ollama:rocm ls -lh /usr/lib/ollama/rocm/libggml-hip.so
# -rwxr-xr-x 1 root root 448M Sep 28 libggml-hip.so  # Docker build

# Größenunterschied → unterschiedliche Builds!
```

### Library Dependency Check

```bash
# ROCm-Bibliotheken auflisten
ldd /usr/local/bin/ollama

# Fehlende oder falsche Versionen identifizieren
ldd /usr/local/lib/ollama/rocm/libggml-hip.so
```

### Lösung: Komplette Docker-Umgebung

```bash
# Alle Bibliotheken aus Docker extrahieren
docker create --name ollama-libs ollama/ollama:rocm
docker cp ollama-libs:/usr/lib/ollama /tmp/ollama-libs-complete
docker rm ollama-libs

# Verzeichnisstruktur überprüfen
du -sh /tmp/ollama-libs-complete
# Erwartung: ~2.3 GB

ls -lh /tmp/ollama-libs-complete/rocm/ | head -20
# Sollte ~60+ Dateien enthalten

# Installation
sudo rm -rf /usr/local/lib/ollama  # Alte Bibliotheken entfernen
sudo cp -r /tmp/ollama-libs-complete /usr/local/lib/ollama
sudo chown -R root:root /usr/local/lib/ollama

# Service neu starten
sudo systemctl restart ollama

# Verifikation
sudo journalctl -u ollama -n 50 | grep "15.3 GiB"
# Sollte available="15.3 GiB" zeigen
```

### Kritische Bibliotheken

Diese Bibliotheken **müssen** aus Docker stammen:

```
libggml-hip.so              # 448 MB - GPU Compute Kernel
libamdhip64.so.6.3.60303    # 22 MB  - HIP Runtime
libhsa-runtime64.so         # 3.2 MB - HSA Runtime
librocblas.so.4.3.60303     # 72 MB  - BLAS für GPU
librocsolver.so.0.3.60303   # 1.6 GB - Solver Library
libhipblas.so.2.3.60303     # 1.1 MB
libhipblaslt.so.0.10.60303  # 7.2 MB
```

---

## Layer Offloading schlägt fehl

### Symptome

```bash
# Logs zeigen:
load_tensors: offloaded 0/25 layers to GPU
# Statt:
load_tensors: offloaded 25/25 layers to GPU
```

### Root Causes und Lösungen

#### Ursache 1: VRAM nicht erkannt

```bash
# Überprüfen
sudo journalctl -u ollama -n 50 | grep "inference compute"

# Falls available="0 B":
# Siehe Abschnitt "GPU wird erkannt, aber 0 VRAM gemeldet"
```

#### Ursache 2: Modell zu groß

```bash
# Modellgröße überprüfen
ollama show qwen3:32b --modelfile

# Falls Modell größer als verfügbarer VRAM (15.3 GiB):
# Verwende kleineres Modell oder Quantisierung

# Beispiel: 32B-Modell → 8B-Modell
ollama pull qwen3:8b
```

#### Ursache 3: GPU-Bibliotheken fehlen

```bash
# Überprüfen, ob rocm-Verzeichnis existiert
ls -la /usr/local/lib/ollama/rocm/

# Falls nicht vorhanden oder leer:
# Siehe "Library Version Inkompatibilität" Abschnitt
```

---

## Performance schlechter als erwartet

### Symptome

GPU ist langsamer oder nur minimal schneller als CPU.

### Analyse

#### Erwartungen vs. Realität

| Modell-Größe | Erwarteter GPU-Speedup | Realer Speedup (780M) |
|--------------|------------------------|----------------------|
| < 1B Parameter | 2-5x | **3.1x** ✅ |
| 7-8B Parameter | 1.5-3x | **1.1x** ⚠️ |
| > 30B Parameter | 3-10x | Nicht getestet |

#### Ursache 1: iGPU Shared Memory Bottleneck

**Problem:** AMD 780M teilt sich RAM mit CPU (keine dedizierten VRAM-Chips).

**Erklärung:**
- Daten müssen zwischen CPU und GPU über gemeinsamen Speicher-Bus
- Kein dedizierter GPU-Memory-Bus wie bei diskreten GPUs
- Memory-Bandwidth-Sharing reduziert Vorteil

**Lösung:** Keine direkte Lösung, aber:
```bash
# Verwende kleinere Modelle für besseren Speedup
ollama pull qwen2.5:0.5b   # Besser für iGPU
ollama pull qwen3:1.5b      # Noch gut

# Vermeide riesige Modelle
# ollama pull qwen3:70b  # Zu groß, kein Vorteil
```

#### Ursache 2: CPU ist sehr schnell

**Problem:** Moderne CPUs (Ryzen 7000+) sind sehr leistungsfähig.

**Benchmark-Vergleich:**
```bash
# qwen3:8b auf AMD Ryzen 7 7840HS:
# CPU: ~23 Sekunden
# GPU (780M): ~21 Sekunden
# Speedup: Nur 1.1x

# Zum Vergleich: Auf Intel i5 (älter):
# CPU: ~45 Sekunden (geschätzt)
# GPU (780M): ~21 Sekunden (gleich)
# Speedup: Würde 2.1x sein
```

#### Ursache 3: Modell-Load Overhead

**Problem:** Kleiner Prompt → Overhead dominiert.

**Test:**
```bash
# Kurzer Prompt (10 Tokens)
time ollama run qwen3:8b "test"
# CPU: 1.2s, GPU: 1.1s → Kaum Unterschied

# Langer Prompt (500 Tokens)
time ollama run qwen3:8b "$(cat long-text.txt)"
# CPU: 45s, GPU: 25s → Deutlicher Unterschied
```

**Lösung:**
```bash
# Für kurze Prompts: CPU reicht oft
# Für lange Texte, Batch-Processing: GPU lohnt sich

# Modell pre-loaden für bessere Messungen
ollama run qwen3:8b "warmup" > /dev/null
time ollama run qwen3:8b "eigentlicher prompt"
```

### Monitoring zur Diagnose

```bash
# GPU-Auslastung während Inference
watch -n 0.5 'HSA_OVERRIDE_GFX_VERSION=11.0.0 /opt/rocm-6.3.0/bin/rocm-smi'

# Sollte zeigen:
# GPU[0] : Temperature: 60C
# GPU[0] : GPU use (%): 95-100%  # Bei aktiver Inference
```

---

## Service startet nicht

### Symptome

```bash
sudo systemctl status ollama
# ● ollama.service - Ollama Service
#      Active: failed (Result: exit-code)
```

### Diagnose

```bash
# Detaillierte Logs
sudo journalctl -u ollama -n 100 --no-pager

# Häufige Fehler:
```

#### Fehler 1: Binary nicht gefunden

```
ExecStart=/usr/local/bin/ollama: No such file or directory
```

**Lösung:**
```bash
ls -la /usr/local/bin/ollama
# Falls nicht vorhanden:
# Siehe Installation Guide für Binary-Extraktion
```

#### Fehler 2: Fehlende Bibliotheken

```
error while loading shared libraries: libamdhip64.so.6: cannot open shared object file
```

**Lösung:**
```bash
# Bibliotheken aus Docker extrahieren
# Siehe "Library Version Inkompatibilität" Abschnitt
```

#### Fehler 3: User "ollama" existiert nicht

```
Failed to determine user credentials: No such process
```

**Lösung:**
```bash
sudo useradd -r -s /bin/false -d /usr/share/ollama ollama
sudo systemctl restart ollama
```

#### Fehler 4: Port bereits belegt

```
listen tcp 127.0.0.1:11434: bind: address already in use
```

**Lösung:**
```bash
# Anderen Ollama-Prozess finden
sudo lsof -i :11434

# Prozess beenden
sudo kill <PID>

# Oder anderen Port verwenden
# In /etc/systemd/system/ollama.service:
Environment="OLLAMA_HOST=127.0.0.1:11435"
```

---

## Debugging-Werkzeuge

### ROCm System Info

```bash
# GPU-Informationen
export HSA_OVERRIDE_GFX_VERSION=11.0.0
/opt/rocm-6.3.0/bin/rocminfo

# Nach gfx-Version filtern
/opt/rocm-6.3.0/bin/rocminfo | grep -i gfx
# Erwartung: gfx1100

# GPU-Auslastung
/opt/rocm-6.3.0/bin/rocm-smi
```

### Ollama Debug-Logs

```bash
# Debug-Modus aktivieren (in Service-Datei)
Environment="OLLAMA_DEBUG=1"

# Live-Logs
sudo journalctl -u ollama -f

# Nach Fehlern suchen
sudo journalctl -u ollama -n 500 | grep -i error

# GPU-spezifische Logs
sudo journalctl -u ollama -n 200 | grep -i "gpu\|vram\|offload"
```

### Library Verification

```bash
# Alle .so Dateien auflisten
find /usr/local/lib/ollama -name "*.so*" -exec ls -lh {} \;

# Größe des rocm-Verzeichnisses
du -sh /usr/local/lib/ollama/rocm/
# Erwartung: ~2.3 GB

# Dependencies überprüfen
ldd /usr/local/bin/ollama | grep "not found"
# Sollte leer sein
```

### Performance Profiling

```bash
# Detaillierte Zeitmessung
time -v ollama run qwen3:8b "test"

# System-Ressourcen während Inference
htop  # In separatem Terminal
# Oder:
watch -n 1 'ps aux | grep ollama'
```

### Docker Debugging

```bash
# Funktioniert GPU in Docker?
docker run --rm \
    --device /dev/kfd \
    --device /dev/dri \
    -e HSA_OVERRIDE_GFX_VERSION=11.0.0 \
    ollama/ollama:rocm \
    bash -c "ollama serve & sleep 10 && ollama run qwen2.5:0.5b 'test'"

# Falls in Docker funktioniert, aber nicht auf Host:
# → Library-Problem! Siehe "Library Version Inkompatibilität"
```

---

## Checkliste für Fehlersuche

Wenn GPU nicht funktioniert, arbeite diese Liste ab:

- [ ] **Version überprüfen:** Ollama v0.12.5?
  ```bash
  /usr/local/bin/ollama --version
  ```

- [ ] **HSA Override gesetzt:** `HSA_OVERRIDE_GFX_VERSION=11.0.0`?
  ```bash
  grep HSA /etc/systemd/system/ollama.service
  ```

- [ ] **Bibliotheken vorhanden:** rocm-Verzeichnis existiert?
  ```bash
  ls -lh /usr/local/lib/ollama/rocm/libggml-hip.so
  ```

- [ ] **Bibliotheken aus Docker:** Größe ~448 MB?
  ```bash
  du -h /usr/local/lib/ollama/rocm/libggml-hip.so
  ```

- [ ] **Service läuft:** Active (running)?
  ```bash
  systemctl is-active ollama
  ```

- [ ] **GPU erkannt:** Logs zeigen gfx1100?
  ```bash
  sudo journalctl -u ollama -n 50 | grep gfx1100
  ```

- [ ] **VRAM verfügbar:** available="15.3 GiB"?
  ```bash
  sudo journalctl -u ollama -n 50 | grep "15.3 GiB"
  ```

- [ ] **Docker-Test:** Funktioniert in Docker?
  ```bash
  docker run --rm --device /dev/kfd --device /dev/dri ollama/ollama:rocm ...
  ```

---

## Erweiterte Fehlersuche

### System-Logs analysieren

```bash
# Kernel-Meldungen für GPU
dmesg | grep -i amdgpu

# HSA Device Errors
dmesg | grep -i hsa

# ROCm Errors
dmesg | grep -i rocm
```

### Netzwerk-Debugging

```bash
# Ollama API testen
curl http://localhost:11434/api/version

# Modell-Liste
curl http://localhost:11434/api/tags
```

### Speicherplatz

```bash
# Modelle können groß sein
df -h ~/.ollama

# Bibliotheken
du -sh /usr/local/lib/ollama
# Erwartung: ~2.5 GB
```

---

## Bekannte Einschränkungen

### iGPU Shared Memory

- **Bottleneck:** Geteilter RAM-Bus mit CPU
- **Impact:** Geringerer Speedup als bei dedizierten GPUs
- **Workaround:** Kleinere Modelle bevorzugen

### ROCm gfx1103 Support

- **Requires:** ROCm 6.3+ (ältere Versionen funktionieren nicht)
- **Override:** Muss als gfx1100 laufen (via HSA_OVERRIDE)

### Modell-Limits

- **Max VRAM:** ~15 GiB (von 32 GB System-RAM)
- **Größte getestete Modelle:** 8B Parameter
- **70B+ Modelle:** Vermutlich zu groß oder sehr langsam

---

## Kontakt und Support

Bei anhaltenden Problemen:

1. **Logs sammeln:**
   ```bash
   sudo journalctl -u ollama -n 500 > ~/ollama-debug.log
   ```

2. **System-Info:**
   ```bash
   uname -a > ~/system-info.txt
   lspci | grep VGA >> ~/system-info.txt
   /usr/local/bin/ollama --version >> ~/system-info.txt
   ```

3. **Bibliotheks-Info:**
   ```bash
   ls -lh /usr/local/lib/ollama/rocm/ > ~/library-info.txt
   ```

4. **Community:**
   - Ollama GitHub: https://github.com/ollama/ollama/issues
   - ROCm GitHub: https://github.com/RadeonOpenCompute/ROCm/issues

---

**Letzte Aktualisierung:** 16. Oktober 2025
**Basierend auf:** Realen Debugging-Erfahrungen
**Status:** Umfassend getestet ✅
