# Ollama GPU-Beschleunigung - Detaillierte Installationsanleitung

**Ziel:** Ollama mit GPU-Beschleunigung auf AMD Radeon 780M installieren
**Geschätzte Zeit:** 1-2 Stunden (inkl. Downloads)
**Voraussetzungen:** Ubuntu 22.04, sudo-Rechte, ~5 GB freier Speicherplatz

## Inhaltsverzeichnis

1. [Voraussetzungen](#voraussetzungen)
2. [ROCm 6.3 Installation](#rocm-63-installation)
3. [Ollama Binary Extraktion](#ollama-binary-extraktion)
4. [Bibliotheken-Extraktion](#bibliotheken-extraktion)
5. [Systemd Service Konfiguration](#systemd-service-konfiguration)
6. [Verifikation](#verifikation)
7. [Performance-Tests](#performance-tests)
8. [CPU-Only Modus (GPU deaktivieren)](#cpu-only-modus-gpu-deaktivieren)

---

## Voraussetzungen

### System-Anforderungen

```bash
# System-Info überprüfen
uname -a
# Erwartung: Linux 6.14+ (oder 6.x)

lspci | grep VGA
# Erwartung: AMD Radeon Graphics (780M)

free -h
# Empfohlen: Mindestens 16 GB RAM (32 GB optimal für große Modelle)
```

### GPU-Erkennung testen

```bash
# AMD GPU sollte sichtbar sein
lspci -nn | grep -i amd

# Erwartete Ausgabe (ähnlich):
# c6:00.0 VGA compatible controller [0300]: Advanced Micro Devices, Inc. [AMD/ATI] Device [1002:15bf]
```

### Docker installieren (falls nicht vorhanden)

```bash
# Docker für Bibliotheken-Extraktion
sudo apt update
sudo apt install -y docker.io docker-compose
sudo usermod -aG docker $USER
newgrp docker

# Test
docker --version
```

---

## ROCm 6.3 Installation

### Schritt 1: ROCm Repository hinzufügen

```bash
# ROCm 6.3 Repository
wget https://repo.radeon.com/rocm/rocm.gpg.key -O - | \
    gpg --dearmor | sudo tee /etc/apt/keyrings/rocm.gpg > /dev/null

echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/rocm.gpg] https://repo.radeon.com/rocm/apt/6.3 jammy main" \
    | sudo tee /etc/apt/sources.list.d/rocm.list

echo -e 'Package: *\nPin: release o=repo.radeon.com\nPin-Priority: 600' \
    | sudo tee /etc/apt/preferences.d/rocm-pin-600
```

### Schritt 2: Alte Pakete entfernen

```bash
# Entferne alte/konflikterende ROCm Versionen
sudo apt remove -y hipcc rocminfo

# Optional: Alte ROCm Versionen komplett entfernen
sudo apt autoremove -y
```

### Schritt 3: ROCm 6.3 Pakete installieren

```bash
# Paketliste aktualisieren
sudo apt update

# ROCm 6.3 Runtime und Development Tools
sudo apt install -y \
    rocm-hip-runtime6.3.0 \
    hip-dev \
    hipcc6.3.0 \
    rocm-device-libs6.3.0

# Verifikation
/opt/rocm-6.3.0/bin/hipcc --version
# Erwartung: HIP version: 6.3.60303
```

### Schritt 4: ROCm Umgebung testen

```bash
# GPU mit HSA Override erkennen
export HSA_OVERRIDE_GFX_VERSION=11.0.0
/opt/rocm-6.3.0/bin/rocminfo | grep gfx

# Erwartete Ausgabe:
#   Name:                    gfx1100
#   Marketing Name:          AMD Radeon Graphics
```

**Hinweis:** ROCm 6.3 ist die **minimale Version** für AMD Radeon 780M Support. Ältere Versionen (6.2.4 und früher) funktionieren nicht richtig.

---

## Ollama Binary Extraktion

### Warum Docker-Binary?

- **v0.12.5** ist die letzte stabile Version mit iGPU-Support
- Docker-Binary ist getestet und funktioniert garantiert
- Vermeidet Kompilierungs-Probleme

### Schritt 1: Docker Image herunterladen

```bash
# Offizielles Ollama ROCm Image pullen
docker pull ollama/ollama:rocm

# Größe: ca. 5 GB
```

### Schritt 2: Binary extrahieren

```bash
# Container starten (muss nicht laufen, nur existieren)
docker create --name ollama-extract ollama/ollama:rocm

# Binary kopieren
docker cp ollama-extract:/bin/ollama /tmp/ollama-v0.12.5

# Container entfernen
docker rm ollama-extract

# Binary installieren
sudo cp /tmp/ollama-v0.12.5 /usr/local/bin/ollama
sudo chmod +x /usr/local/bin/ollama

# Verifikation
/usr/local/bin/ollama --version
# Erwartung: ollama version is 0.12.5
```

---

## Bibliotheken-Extraktion

### Warum komplette Bibliotheken aus Docker?

**Kritisch für den Erfolg!** Die Docker-Bibliotheken sind:
- Exakt auf die Binary abgestimmt
- Vollständig und ohne fehlende Dependencies
- Frei von Konflikten mit System-Paketen

### Schritt 1: Container für Bibliotheken-Extraktion

```bash
# Neuen Container erstellen
docker create --name ollama-libs ollama/ollama:rocm

# Komplettes Library-Verzeichnis kopieren
docker cp ollama-libs:/usr/lib/ollama /tmp/ollama-libs

# Container entfernen
docker rm ollama-libs
```

### Schritt 2: Verzeichnisstruktur überprüfen

```bash
# Größe überprüfen
du -sh /tmp/ollama-libs
# Erwartung: 2.3 GB

# Wichtigste Dateien
ls -lh /tmp/ollama-libs/rocm/ | head -20

# Sollte enthalten:
# libggml-hip.so (~448 MB)
# libamdhip64.so.6.3.60303 (~22 MB)
# libhsa-runtime64.so.1.14.60303 (~3.2 MB)
# librocblas.so.4.3.60303 (~72 MB)
# librocsolver.so.0.3.60303 (~1.6 GB)
# + viele weitere
```

### Schritt 3: Bibliotheken installieren

```bash
# Zielverzeichnis erstellen
sudo mkdir -p /usr/local/lib/ollama

# Bibliotheken kopieren
sudo cp -r /tmp/ollama-libs/* /usr/local/lib/ollama/

# Besitzer setzen
sudo chown -R root:root /usr/local/lib/ollama

# Berechtigungen setzen
sudo chmod -R 755 /usr/local/lib/ollama

# Verifikation
ls -lh /usr/local/lib/ollama/rocm/libggml-hip.so
# Sollte existieren und ~448 MB groß sein
```

### Schritt 4: Library Path konfigurieren

```bash
# Ollama Library Path (wird automatisch von systemd service verwendet)
# Keine manuelle Konfiguration nötig - Service setzt dies automatisch
```

---

## Systemd Service Konfiguration

### Schritt 1: Ollama User erstellen

```bash
# Dedizierter User für Ollama Service
sudo useradd -r -s /bin/false -d /usr/share/ollama ollama
```

### Schritt 2: Service-Datei erstellen

```bash
# Service-Datei anlegen
sudo tee /etc/systemd/system/ollama.service > /dev/null <<'EOF'
[Unit]
Description=Ollama Service
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/local/bin/ollama serve
User=ollama
Group=ollama
Restart=always
RestartSec=3
Environment="PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin"
Environment="OLLAMA_KEEP_ALIVE=15m"
Environment="HSA_OVERRIDE_GFX_VERSION=11.0.0"
Environment="OLLAMA_DEBUG=1"

[Install]
WantedBy=multi-user.target
EOF
```

### Wichtige Environment-Variablen erklärt:

| Variable | Wert | Zweck |
|----------|------|-------|
| `HSA_OVERRIDE_GFX_VERSION` | `11.0.0` | **KRITISCH!** Macht gfx1103 zu gfx1100 für ROCm-Kompatibilität |
| `OLLAMA_KEEP_ALIVE` | `15m` | Modelle bleiben 15 Min im RAM (schnellere Folgeanfragen) |
| `OLLAMA_DEBUG` | `1` | Aktiviert Debug-Logging für Troubleshooting |

### Schritt 3: Service aktivieren und starten

```bash
# Systemd neuladen
sudo systemctl daemon-reload

# Service aktivieren (Auto-Start beim Boot)
sudo systemctl enable ollama

# Service starten
sudo systemctl start ollama

# Status überprüfen
sudo systemctl status ollama
```

### Schritt 4: Logs überprüfen

```bash
# Live-Logs anzeigen
sudo journalctl -u ollama -f

# Letzte 50 Zeilen
sudo journalctl -u ollama -n 50 --no-pager

# Suche nach GPU-Erkennung:
# level=INFO msg="inference compute" compute=gfx1100 total="15.3 GiB" available="15.3 GiB"
```

---

## Verifikation

### Schritt 1: GPU-Erkennung überprüfen

```bash
# Logs nach GPU-Info filtern
sudo journalctl -u ollama -n 100 --no-pager | grep -i "inference compute"

# Erwartete Ausgabe:
# level=INFO msg="inference compute" compute=gfx1100 total="15.3 GiB" available="15.3 GiB"
```

**Wichtig:**
- ✅ `total="15.3 GiB"` - GPU wird mit vollem VRAM erkannt
- ❌ `total="0 B"` - GPU nicht richtig erkannt (siehe Troubleshooting)

### Schritt 2: Modell testen

```bash
# Kleines Modell herunterladen und testen
ollama pull qwen2.5:0.5b

# Test-Prompt
ollama run qwen2.5:0.5b "Hello, test GPU"
```

### Schritt 3: Layer-Offloading überprüfen

```bash
# Logs während Modell-Load
sudo journalctl -u ollama -n 200 --no-pager | grep -i "offload\|layers"

# Erwartete Ausgabe:
# load_tensors: offloaded 25/25 layers to GPU
```

**Interpretation:**
- `25/25 layers` = 100% GPU-Nutzung ✅
- `0/25 layers` = Nur CPU, GPU nicht genutzt ❌

### Schritt 4: Service-Stabilität

```bash
# Service sollte "active (running)" sein
systemctl is-active ollama
# Erwartung: active

# Keine Restarts
systemctl show ollama | grep NRestarts
# Erwartung: NRestarts=0
```

---

## Performance-Tests

### CPU vs. GPU Benchmark

#### Vorbereitung

```bash
# Modell herunterladen
ollama pull qwen3:8b
```

#### CPU-Only Test

```bash
# GPU deaktivieren
sudo systemctl stop ollama
sudo mv /usr/local/lib/ollama/rocm /tmp/ollama-rocm-backup
sudo systemctl start ollama

# Modell vorladen
ollama run qwen3:8b "test"

# Benchmark
time ollama run qwen3:8b "sage guten abend und hänge 3 emojis daran"

# Ergebnis notieren (z.B. 22.995 Sekunden)
```

#### GPU Test

```bash
# GPU wieder aktivieren
sudo systemctl stop ollama
sudo mv /tmp/ollama-rocm-backup /usr/local/lib/ollama/rocm
sudo systemctl start ollama

# Modell vorladen
ollama run qwen3:8b "test"

# Benchmark
time ollama run qwen3:8b "sage guten abend und hänge 3 emojis daran"

# Ergebnis notieren (z.B. 20.711 Sekunden)
```

#### Speedup berechnen

```bash
# Speedup = CPU-Zeit / GPU-Zeit
# Beispiel: 22.995 / 20.711 = 1.11x schneller
```

### Erwartete Ergebnisse

| Modell | CPU-Zeit | GPU-Zeit | Speedup |
|--------|----------|----------|---------|
| qwen2.5:0.5b | ~1.2s | ~0.4s | **3.1x** |
| qwen3:8b | ~23s | ~21s | **1.1x** |

**Hinweis:** Kleinere Modelle profitieren mehr von GPU-Beschleunigung als mittlere Modelle auf iGPUs.

---

## Monitoring und Wartung

### GPU-Auslastung überwachen

```bash
# ROCm System Management
watch -n 1 'HSA_OVERRIDE_GFX_VERSION=11.0.0 /opt/rocm-6.3.0/bin/rocm-smi'

# Zeigt:
# - GPU Temperature
# - Memory Usage
# - GPU Utilization
```

### Logs rotieren

```bash
# Systemd Journal-Größe limitieren
sudo journalctl --vacuum-size=500M

# Oder nach Zeit
sudo journalctl --vacuum-time=7d
```

### Service neu starten

```bash
# Bei Problemen oder Updates
sudo systemctl restart ollama

# Status überprüfen
sudo systemctl status ollama
```

---

## CPU-Only Modus (GPU deaktivieren)

Manchmal möchtest du Ollama ohne GPU laufen lassen (z.B. für Benchmarks oder Tests).

### Methode 1: Environment-Variable (Empfohlen)

**Einfachste Methode** - funktioniert ohne den systemd-Service zu stoppen:

```bash
# GPU für einen einzelnen Befehl deaktivieren
OLLAMA_NUM_GPU=0 ollama run qwen3:8b "test"

# Oder für mehrere Befehle:
export OLLAMA_NUM_GPU=0
ollama run qwen3:8b "test"
ollama run qwen2.5:3b "hello"
unset OLLAMA_NUM_GPU  # GPU wieder aktivieren
```

**Verwendung:**
```bash
# CPU-Test
OLLAMA_NUM_GPU=0 time ollama run qwen3:8b "sage guten abend"

# GPU-Test (normal)
time ollama run qwen3:8b "sage guten abend"
```

### Methode 2: Eigener Server auf anderem Port

Starte einen separaten Ollama-Server ohne GPU, unabhängig vom systemd-Service:

```bash
# Systemd-Service läuft weiter auf Port 11434

# Eigenen CPU-only Server auf Port 11435 starten
OLLAMA_NUM_GPU=0 OLLAMA_HOST=127.0.0.1:11435 ollama serve &

# In anderem Terminal mit dem CPU-Server verbinden
OLLAMA_HOST=127.0.0.1:11435 ollama run qwen3:8b "test"

# Server beenden
pkill -f "ollama serve"
```

**Vorteil:** Beide Server (GPU + CPU) laufen gleichzeitig.

### Methode 3: Libraries temporär deaktivieren

**System-weit** GPU deaktivieren durch Verschieben der ROCm-Bibliotheken:

```bash
# Systemd-Service stoppen
sudo systemctl stop ollama

# ROCm-Bibliotheken temporär verschieben
sudo mv /usr/local/lib/ollama/rocm /tmp/ollama-rocm-backup

# Service wieder starten (jetzt CPU-only)
sudo systemctl start ollama

# Testen
ollama run qwen3:8b "test"

# GPU wieder aktivieren
sudo systemctl stop ollama
sudo mv /tmp/ollama-rocm-backup /usr/local/lib/ollama/rocm
sudo systemctl start ollama
```

**Nachteil:** Benötigt sudo und Service-Neustart.

### Vergleich der Methoden

| Methode | Vorteil | Nachteil | Use Case |
|---------|---------|----------|----------|
| `OLLAMA_NUM_GPU=0` | ✅ Einfach, kein sudo nötig | Nur für einzelne Befehle | Schnelle Tests, Benchmarks |
| Eigener Server | ✅ GPU + CPU parallel | Port-Management | Vergleichstests parallel |
| Libraries verschieben | ✅ System-weit CPU-only | ❌ Sudo, umständlich | Längere CPU-Test-Sessions |

### Praktisches Benchmark-Beispiel

```bash
# CPU-Benchmark (Methode 1)
echo "=== CPU Test ==="
OLLAMA_NUM_GPU=0 time ollama run qwen3:8b "sage guten abend und hänge 3 emojis daran"

# GPU-Benchmark
echo "=== GPU Test ==="
time ollama run qwen3:8b "sage guten abend und hänge 3 emojis daran"

# Speedup berechnen
# Beispiel: CPU=23s, GPU=21s → 23/21 = 1.1x schneller
```

### GPU-Status überprüfen

```bash
# Überprüfe, ob GPU aktiv ist
journalctl -u ollama -n 50 --no-pager | grep "inference compute"

# Mit GPU:
# level=INFO msg="inference compute" compute=gfx1100 total="15.3 GiB" available="15.3 GiB"

# Ohne GPU (CPU-only):
# (keine GPU-Meldung)
```

### Permanente CPU-Only Konfiguration

Falls du GPU **dauerhaft** deaktivieren möchtest:

```bash
# Service-Datei editieren
sudo nano /etc/systemd/system/ollama.service

# Diese Zeile hinzufügen:
Environment="OLLAMA_NUM_GPU=0"

# Speichern und neu laden
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

**Hinweis:** Nicht empfohlen, da du ja die GPU-Beschleunigung installiert hast!

---

## Backup-Strategie

### Wichtige Dateien sichern

```bash
# Backup-Verzeichnis erstellen
mkdir -p ~/ollama-gpu-backup

# Binary sichern
cp /usr/local/bin/ollama ~/ollama-gpu-backup/

# Service-Konfiguration
cp /etc/systemd/system/ollama.service ~/ollama-gpu-backup/

# Dokumentation der Library-Quelle
echo "Libraries extracted from: ollama/ollama:rocm" > ~/ollama-gpu-backup/source.txt
echo "Date: $(date)" >> ~/ollama-gpu-backup/source.txt

# Hinweis: Bibliotheken (2.3 GB) müssen bei Bedarf aus Docker neu extrahiert werden
```

### Wiederherstellung

```bash
# Binary wiederherstellen
sudo cp ~/ollama-gpu-backup/ollama /usr/local/bin/
sudo chmod +x /usr/local/bin/ollama

# Service wiederherstellen
sudo cp ~/ollama-gpu-backup/ollama.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart ollama

# Bibliotheken aus Docker neu extrahieren (siehe oben)
```

---

## Nächste Schritte

Nach erfolgreicher Installation:

1. ✅ GPU-Beschleunigung funktioniert
2. ⏭️ Weitere Modelle testen (siehe [Ollama Library](https://ollama.com/library))
3. ⏭️ Integration in bestehende Workflows
4. ⏭️ Monitoring-Dashboards erstellen (Grafana)

---

## Probleme?

Siehe [Troubleshooting Guide](OLLAMA-GPU-TROUBLESHOOTING.md) für häufige Fehler und Lösungen.

---

**Autoren:** Dokumentiert am 16. Oktober 2025
**Geschätzte Installationszeit:** 1-2 Stunden
**Status:** Produktionsbereit ✅
