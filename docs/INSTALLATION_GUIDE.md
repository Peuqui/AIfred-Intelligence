# 🚀 AIfred Intelligence - Vollständige Installationsanleitung

Schritt-für-Schritt Anleitung für die Installation auf **Linux** (Ubuntu/Debian) und **Windows mit WSL2**.

---

## 📋 System-Anforderungen

### Minimum-Anforderungen:
- **CPU**: 4+ Kerne (x86_64)
- **RAM**: 8 GB (für kleine Modelle wie qwen2.5:3b)
- **Festplatte**: 20 GB freier Speicher
- **GPU**: Optional (Intel iGPU, AMD Radeon, NVIDIA mit CUDA)

### Empfohlen für beste Performance:
- **CPU**: 8+ Kerne (AMD Ryzen / Intel Core)
- **RAM**: 32 GB (für große Modelle wie qwen2.5:32b)
- **GPU**: NVIDIA RTX 3060+ (12GB VRAM) oder AMD Radeon (8GB+ VRAM)
- **Festplatte**: SSD mit 50+ GB freier Speicher

### GPU-Support (Optional):
- **NVIDIA**: CUDA 11.8+ (RTX 2000+, GTX 1600+ Serie)
- **AMD**: ROCm 5.7+ (Radeon RX 6000+, Radeon 780M iGPU)
- **Intel**: Intel Arc mit oneAPI (experimentell)

**Ohne GPU**: CPU-Modus funktioniert einwandfrei, ist aber langsamer (ca. 3-5x).

---

## 🐧 Installation auf Linux (Ubuntu/Debian)

### 1. System-Pakete installieren

```bash
# System aktualisieren
sudo apt update && sudo apt upgrade -y

# Python 3.10+ und Entwicklungs-Tools
sudo apt install -y python3 python3-pip python3-venv git curl

# Docker & Docker Compose (für SearXNG)
sudo apt install -y docker.io docker-compose-v2
sudo systemctl enable docker
sudo systemctl start docker

# Benutzer zur Docker-Gruppe hinzufügen (vermeidet sudo bei Docker-Befehlen)
sudo usermod -aG docker $USER
newgrp docker  # Gruppe aktivieren (oder neu anmelden)
```

### 2. Ollama installieren

```bash
# Ollama herunterladen und installieren
curl -fsSL https://ollama.com/install.sh | sh

# Ollama als Systemdienst starten
sudo systemctl enable ollama
sudo systemctl start ollama

# Testen
ollama --version
```

**GPU-Support aktivieren (AMD ROCm Beispiel)**:
```bash
# Für AMD Radeon GPU (ROCm)
# Siehe: https://rocm.docs.amd.com/en/latest/deploy/linux/quick_start.html

# Beispiel für Ubuntu 22.04:
wget https://repo.radeon.com/amdgpu-install/latest/ubuntu/jammy/amdgpu-install_6.3.60300-1_all.deb
sudo apt install -y ./amdgpu-install_6.3.60300-1_all.deb
sudo amdgpu-install --usecase=rocm --no-dkms -y

# GPU-Override für nicht-offizielle GPUs (z.B. Radeon 780M)
sudo systemctl edit ollama
# Füge hinzu:
# [Service]
# Environment="HSA_OVERRIDE_GFX_VERSION=11.0.0"
sudo systemctl restart ollama
```

**GPU-Support aktivieren (NVIDIA CUDA)**:
```bash
# NVIDIA CUDA Toolkit installieren
# Siehe: https://developer.nvidia.com/cuda-downloads

# Beispiel für Ubuntu 22.04:
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt update
sudo apt install -y cuda-toolkit-12-6

# Testen
nvidia-smi
```

### 3. AI-Modelle herunterladen

```bash
# Kleine, schnelle Modelle (empfohlen für Start)
ollama pull qwen2.5:3b      # 1.9 GB - Schnell, gute Balance
ollama pull qwen3:1.7b      # 1.1 GB - Sehr schnell (Automatik-Tasks)

# Mittelgroße Modelle (empfohlen für Qualität)
ollama pull qwen2.5:14b     # 9 GB - Beste RAG-Performance
ollama pull llama3.1:8b     # 4.7 GB - Gute Allzweck-Performance

# Große Modelle (nur mit 32+ GB RAM)
ollama pull qwen2.5:32b     # 19 GB - Beste Qualität (braucht 21+ GB RAM!)

# Liste aller installierten Modelle
ollama list
```

### 4. Repository klonen

```bash
# In dein Projekt-Verzeichnis navigieren
cd ~/Projekte  # oder ~/projects oder wo auch immer

# Repository klonen
git clone https://github.com/Peuqui/AIfred-Intelligence.git
cd AIfred-Intelligence
```

### 5. Python Virtual Environment einrichten

```bash
# Virtual Environment erstellen
python3 -m venv venv

# Virtual Environment aktivieren
source venv/bin/activate

# Dependencies installieren (inkl. python-dotenv für .env Support)
pip install --upgrade pip
pip install -r requirements.txt
```

**Hinweis**: Bei Problemen mit `faster-whisper`:
```bash
# Alternative: CPU-only Installation
pip install faster-whisper --no-deps
pip install -r requirements.txt
```

### 6. SearXNG Docker-Container starten

SearXNG ist eine **selbst-gehostete Meta-Suchmaschine** (durchsucht Google, Bing, DuckDuckGo, etc. anonym).

```bash
# Ins SearXNG-Verzeichnis wechseln
cd docker/searxng

# Container starten (Hintergrund)
docker compose up -d

# Logs anschauen (optional)
docker compose logs -f

# Container-Status prüfen
docker ps | grep searxng
```

SearXNG läuft nun auf: **http://localhost:8888**

**Wichtig**: Der Container startet automatisch beim Reboot (`restart: unless-stopped`).

**SearXNG konfigurieren** (optional):
- Config-Datei: `docker/searxng/settings.yml`
- Nach Änderungen: `docker compose restart`

### 7. API Keys konfigurieren (Optional)

AIfred funktioniert **ohne API Keys** (verwendet dann nur SearXNG), aber mit Brave/Tavily wird die Suche schneller und besser.

```bash
# .env.example als Vorlage kopieren
cp .env.example .env

# .env editieren
nano .env
```

Füge deine API Keys ein:
```bash
# Brave Search API (2.000 Requests/Monat kostenlos)
# https://brave.com/search/api/
BRAVE_API_KEY=dein_brave_api_key_hier

# Tavily AI Search (1.000 Requests/Monat kostenlos)
# https://tavily.com/
TAVILY_API_KEY=dein_tavily_api_key_hier

# Google Custom Search (optional, falls du es nutzen willst)
GOOGLE_API_KEY=dein_google_api_key_hier
GOOGLE_CX=deine_custom_search_engine_id_hier
```

**3-Stufen Fallback**:
1. **Brave Search** (Primary) - Schnell, privacy-focused
2. **Tavily AI** (Fallback) - RAG-optimiert, KI-basierte Suche
3. **SearXNG** (Last Resort) - Immer verfügbar, unlimited

### 8. Voice Assistant starten

```bash
# Zurück ins Hauptverzeichnis
cd /home/mp/Projekte/AIfred-Intelligence  # Anpassen!

# Virtual Environment aktivieren (falls nicht schon aktiv)
source venv/bin/activate

# App starten
python aifred_intelligence.py
```

**Gradio öffnet automatisch im Browser**: `https://localhost:7860`

**Von anderen Geräten im Netzwerk** (Smartphone, Tablet):
- AIfred zeigt beim Start die LAN-IP an, z.B. `http://192.168.1.42:7860`

---

## 🪟 Installation auf Windows mit WSL2

Windows-Nutzer können AIfred über **WSL2** (Windows Subsystem for Linux) laufen lassen.

### 1. WSL2 aktivieren

```powershell
# In PowerShell als Administrator:
wsl --install
wsl --set-default-version 2

# Ubuntu installieren
wsl --install -d Ubuntu-22.04

# Neustart erforderlich!
```

Nach dem Neustart startet Ubuntu automatisch und fragt nach Benutzername/Passwort.

### 2. WSL2 konfigurieren

In der **WSL2-Ubuntu-Shell**:

```bash
# System aktualisieren
sudo apt update && sudo apt upgrade -y

# Docker & Docker Compose installieren
sudo apt install -y docker.io docker-compose-v2
sudo systemctl enable docker
sudo systemctl start docker

# Benutzer zur Docker-Gruppe hinzufügen
sudo usermod -aG docker $USER
```

**WSL2 neu starten** (in PowerShell):
```powershell
wsl --shutdown
wsl
```

### 3. Ollama auf Windows installieren

**Option A: Ollama nativ auf Windows** (empfohlen für NVIDIA GPUs):
1. Download: https://ollama.com/download/windows
2. Installer ausführen
3. Ollama läuft als Windows-Service

**Option B: Ollama in WSL2**:
```bash
# In WSL2-Shell:
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable ollama
sudo systemctl start ollama
```

### 4. Ab hier wie Linux-Installation fortfahren

Folge den Schritten 3-8 der **Linux-Installation** oben.

**Wichtig für Windows-Nutzer**:
- Projekt-Ordner liegt in WSL2: `/home/<username>/Projekte/AIfred-Intelligence`
- **NICHT** in `/mnt/c/Users/...` (Windows-Laufwerk, langsam!)
- VS Code mit "Remote - WSL" Extension nutzen

---

## 🎯 Installation auf dem Hauptrechner (RTX 3060)

Du hast einen Hauptrechner mit **NVIDIA RTX 3060 (12GB VRAM)**. Hier die optimale Konfiguration:

### 1. NVIDIA CUDA Toolkit

```bash
# Ubuntu 22.04/24.04:
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt update
sudo apt install -y cuda-toolkit-12-6 nvidia-driver-550

# Neustart nach Treiber-Installation
sudo reboot

# Nach Reboot: GPU testen
nvidia-smi
```

### 2. Ollama mit GPU-Support

```bash
# Ollama installieren
curl -fsSL https://ollama.com/install.sh | sh

# Ollama erkennt NVIDIA GPU automatisch
ollama --version

# Test: Modell auf GPU laden
ollama run qwen2.5:3b "Test auf RTX 3060"
```

**Erwartete VRAM-Nutzung** (nvidia-smi):
- qwen2.5:3b: ~2.5 GB VRAM
- qwen2.5:14b: ~9 GB VRAM
- qwen2.5:32b: **Nicht auf 12GB VRAM!** (braucht 19GB+)

### 3. Empfohlene Modelle für RTX 3060

```bash
# Schnelle Modelle (1-3 GB VRAM)
ollama pull qwen3:1.7b      # Automatik-Tasks (hardcoded)
ollama pull qwen2.5:3b      # Beste Balance für RTX 3060
ollama pull llama3.2:3b     # Alternative, schnell

# Mittelgroße Modelle (6-10 GB VRAM)
ollama pull qwen2.5:14b     # Beste RAG-Performance (passt auf 12GB!)
ollama pull llama3.1:8b     # Gute Qualität

# NICHT empfohlen für RTX 3060:
# qwen2.5:32b (braucht 19GB VRAM)
# mixtral:8x7b (braucht 26GB VRAM)
```

### 4. SearXNG Docker auf Windows/WSL2

```bash
# In WSL2:
cd ~/Projekte/AIfred-Intelligence/docker/searxng
docker compose up -d

# Prüfen
docker ps | grep searxng
curl http://localhost:8888
```

### 5. VS Code mit WSL Extension

1. **VS Code für Windows** installieren: https://code.visualstudio.com/
2. **Extension installieren**: "Remote - WSL" (Microsoft)
3. **In WSL öffnen**:
   ```bash
   # In WSL2-Shell:
   cd ~/Projekte/AIfred-Intelligence
   code .
   ```
4. **Claude Code Extension installieren**:
   - Extensions → "Claude Code" suchen
   - "Install in WSL: Ubuntu" klicken

---

## 🧪 Testen der Installation

### 1. SearXNG testen

```bash
# Browser: http://localhost:8888
# Oder via curl:
curl "http://localhost:8888/search?q=test&format=json&language=de"
```

Erwartete Antwort: JSON mit Suchergebnissen.

### 2. Ollama testen

```bash
# Modell-Liste anzeigen
ollama list

# Modell testen
ollama run qwen2.5:3b "Erkläre mir, was du bist"
```

### 3. AIfred starten

```bash
cd ~/Projekte/AIfred-Intelligence
source venv/bin/activate
python aifred_intelligence.py
```

**Erwartete Ausgabe**:
```
🎩 AIfred Intelligence startet...
✅ Ollama-Server erreichbar
📊 Verfügbare Modelle: qwen2.5:3b, qwen3:1.7b, llama3.1:8b
🔍 SearXNG erreichbar: http://localhost:8888
🌍 Gradio läuft auf: http://localhost:7860
```

### 4. Erste Test-Frage

In der Gradio-UI:
1. **Modus**: "🧠 Eigenes Wissen" (schneller Start ohne Web-Suche)
2. **Model**: qwen2.5:3b
3. **Text-Eingabe**: "Was ist die Hauptstadt von Deutschland?"
4. **Text senden** klicken

**Erwartete Antwort**: ~5-10 Sekunden, Antwort "Berlin".

---

## 🐛 Troubleshooting

### Problem: Ollama findet GPU nicht

**Symptom**: Modelle laufen langsam auf CPU.

**Lösung NVIDIA**:
```bash
# CUDA-Treiber prüfen
nvidia-smi

# Falls nicht gefunden:
sudo apt install -y nvidia-driver-550
sudo reboot
```

**Lösung AMD**:
```bash
# ROCm installieren
# Siehe: https://rocm.docs.amd.com/

# GPU-Override (für inoffizielle GPUs)
sudo systemctl edit ollama
# Füge hinzu:
# [Service]
# Environment="HSA_OVERRIDE_GFX_VERSION=11.0.0"
sudo systemctl restart ollama
```

### Problem: SearXNG antwortet nicht

```bash
# Container-Status prüfen
docker ps | grep searxng

# Falls nicht läuft:
cd ~/Projekte/AIfred-Intelligence/docker/searxng
docker compose up -d

# Logs prüfen
docker compose logs -f searxng
```

### Problem: Faster-Whisper Installation schlägt fehl

```bash
# Alternative: CPU-only Installation
pip uninstall faster-whisper
pip install faster-whisper --no-deps
pip install -r requirements.txt
```

### Problem: Modell-Ladezeit zu lang (>30s)

**Ursache**: Modell wird von Disk geladen (nicht im RAM gecached).

**Lösung**: Modell vorab laden:
```bash
# Modell im RAM halten
ollama run qwen2.5:3b ""
# (leerer Prompt, lädt Modell ohne Inferenz)
```

**Automatisch**: AIfred lädt das gewählte Modell beim Start automatisch.

### Problem: "Out of Memory" bei großen Modellen

**Symptom**: `model runner has unexpectedly stopped`

**Lösung**:
1. **Kleineres Modell verwenden**: qwen2.5:3b statt qwen2.5:32b
2. **Context-Fenster reduzieren** (in `lib/config.py`):
   ```python
   DEFAULT_NUM_CTX = 4096  # statt 8192
   ```
3. **GPU-RAM prüfen**:
   ```bash
   # NVIDIA:
   nvidia-smi

   # AMD:
   rocm-smi
   ```

### Problem: Port 7860 bereits belegt

```bash
# Port ändern in aifred_intelligence.py (letzte Zeile):
app.launch(server_name="0.0.0.0", server_port=7861, share=False)
```

---

## 📚 Weiterführende Dokumentation

- **Model Comparison**: `docs/MODEL_COMPARISON_DETAILED.md`
- **Benchmark-Ergebnisse**: `benchmark_results/FINALE_QUALITAETS_ANALYSE.md`
- **URL-Rating Benchmark**: `benchmarks/URL_RATING_RESULTS.md`
- **Development Docs**: `docs/development/`

---

## 🤝 Support

Bei Problemen:
1. **Logs prüfen**: AIfred gibt Debug-Infos in der Konsole aus
2. **GitHub Issues**: https://github.com/Peuqui/AIfred-Intelligence/issues
3. **Ollama Docs**: https://ollama.com/docs

---

**Happy AI Chatting! 🎩**
