# 🚀 AIfred Intelligence - Migration Guide (Mini-PC → WSL/Hauptrechner)

**Ziel:** Projekt von Mini-PC (Ubuntu) auf WSL2 (Windows Hauptrechner) migrieren

**Hardware-Specs Zielsystem:**
- CPU: AMD Ryzen 9 9900X3D
- GPU: NVIDIA RTX 3060
- RAM: 32GB+
- OS: Windows 11 mit WSL2 (Ubuntu)

---

## 📦 Phase 1: Export vom Mini-PC

### 1.1 Service stoppen
```bash
sudo systemctl stop aifred-intelligence.service
```

### 1.2 Projekt-Archiv erstellen
```bash
cd /home/mp/Projekte

# Archiv erstellen (ohne .git, __pycache__, venv, Backups)
tar -czf AIfred-Intelligence_$(date +%Y%m%d_%H%M).tar.gz \
  --exclude='AIfred-Intelligence/.git' \
  --exclude='AIfred-Intelligence/__pycache__' \
  --exclude='AIfred-Intelligence/venv' \
  --exclude='AIfred-Intelligence/*.backup-*' \
  --exclude='AIfred-Intelligence/assistant_settings.json' \
  AIfred-Intelligence/

# Archiv-Größe prüfen
ls -lh AIfred-Intelligence_*.tar.gz
```

**Erwartete Größe:** ~20-50 MB (ohne venv und Piper Models!)

### 1.3 Archiv transferieren
```bash
# Option A: Per SCP (wenn SSH auf WSL läuft)
scp AIfred-Intelligence_*.tar.gz user@windows-pc:/mnt/c/Users/YourName/Downloads/

# Option B: Per Netzwerkfreigabe (einfacher für WSL)
# 1. Archiv in Windows-Ordner kopieren (z.B. per SMB/Netzwerk)
# 2. Von WSL aus auf /mnt/c/... zugreifen
```

### 1.4 Service wieder starten (falls gewünscht)
```bash
sudo systemctl start aifred-intelligence.service
```

---

## 🖥️ Phase 2: Import auf WSL2

### 2.1 WSL2 vorbereiten
```bash
# Prüfe WSL Version (sollte WSL2 sein)
wsl --list --verbose

# In WSL2 einloggen
wsl

# System updaten
sudo apt update && sudo apt upgrade -y
```

### 2.2 Voraussetzungen installieren

#### Python 3.10+
```bash
python3 --version  # Sollte 3.10+ sein

# Falls nicht vorhanden:
sudo apt install python3 python3-pip python3-venv -y
```

#### Ollama installieren
```bash
curl -fsSL https://ollama.com/install.sh | sh

# Ollama als Service starten
sudo systemctl enable ollama
sudo systemctl start ollama

# Status prüfen
sudo systemctl status ollama
```

#### AI-Modelle herunterladen
```bash
# Haupt-Modelle (empfohlen für Recherche)
ollama pull qwen2.5:32b    # Beste Qualität, 19GB, langsam
ollama pull qwen2.5:14b    # RAG-optimiert, 8GB
ollama pull qwen3:8b       # Schnell, 5GB

# Optional: Weitere Modelle
ollama pull llama3.2:3b    # Sehr schnell für einfache Fragen
ollama pull mistral        # Gut für Code

# Installierte Modelle prüfen
ollama list
```

#### Docker installieren (für SearXNG)
```bash
# Docker installieren
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# User zu Docker-Gruppe hinzufügen
sudo usermod -aG docker $USER

# Neu einloggen (wichtig!)
exit
wsl

# Docker testen
docker --version
docker compose version
```

### 2.3 Projekt entpacken
```bash
# Ins Home-Verzeichnis wechseln
cd ~

# Projekte-Ordner erstellen
mkdir -p Projekte
cd Projekte

# Archiv entpacken (von Windows-Downloads zugreifen)
tar -xzf /mnt/c/Users/YourName/Downloads/AIfred-Intelligence_*.tar.gz

# Ins Projekt wechseln
cd AIfred-Intelligence

# Struktur prüfen
ls -la
```

### 2.4 Virtual Environment erstellen
```bash
cd ~/Projekte/AIfred-Intelligence

# Venv erstellen
python3 -m venv venv

# Aktivieren
source venv/bin/activate

# Pip upgraden
pip install --upgrade pip

# Dependencies installieren
pip install -r requirements.txt
```

**Wichtig:** Falls `faster-whisper` oder `torch` Probleme machen:
```bash
# CUDA-Support für RTX 3060 (optional, für GPU-Acceleration)
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118

# Dann nochmal requirements
pip install -r requirements.txt
```

### 2.5 Piper TTS installieren (Optional, für lokale TTS)
```bash
cd ~/Projekte/AIfred-Intelligence

# Piper Binary herunterladen (Linux x64)
mkdir -p venv/bin
wget https://github.com/rhasspy/piper/releases/download/v1.2.0/piper_amd64.tar.gz
tar -xzf piper_amd64.tar.gz -C venv/bin/ --strip-components=1
chmod +x venv/bin/piper

# Piper-Modell herunterladen (Thorsten Voice)
mkdir -p piper_models
cd piper_models
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx.json

cd ..

# Test Piper
venv/bin/piper --version
```

### 2.6 API Keys konfigurieren
```bash
cd ~/Projekte/AIfred-Intelligence

# .env erstellen
cp .env.example .env

# .env editieren
nano .env
```

**Füge deine API Keys ein:**
```env
# Brave Search API (Primary)
BRAVE_API_KEY=your_brave_api_key_here

# Tavily AI (Fallback)
TAVILY_API_KEY=your_tavily_api_key_here

# SearXNG wird automatisch als Fallback genutzt (self-hosted)
```

**Ohne API Keys läuft nur SearXNG!**

### 2.7 SearXNG starten (Self-Hosted Search)
```bash
cd ~/Projekte/AIfred-Intelligence/docker/searxng

# SearXNG Container starten
docker compose up -d

# Status prüfen
docker compose ps

# Logs anschauen
docker compose logs -f

# SearXNG testen
curl http://localhost:8888
```

SearXNG sollte jetzt auf `http://localhost:8888` laufen.

---

## 🚀 Phase 3: Erste Inbetriebnahme

### 3.1 Manuelle Test-Ausführung
```bash
cd ~/Projekte/AIfred-Intelligence
source venv/bin/activate

# AIfred starten
python aifred_intelligence.py
```

**Erwartete Ausgabe:**
```
✅ SSL-Zertifikate nicht gefunden (optional)
🌐 Server läuft ohne HTTPS auf Port 8443
Running on local URL:  http://0.0.0.0:8443
```

**Öffne Browser:**
- Lokal: `http://localhost:8443`
- Von Windows aus: `http://localhost:8443` (WSL2 mapped automatisch)
- Von Netzwerk: `http://<WSL-IP>:8443`

### 3.2 Funktionstest
1. **Text-Eingabe testen**: "Hallo AIfred, wie geht's?"
2. **Model wählen**: qwen2.5:32b oder qwen3:8b
3. **Research-Mode testen**: "Was sind die neuesten Nachrichten zu KI?" (Web-Suche Ausführlich)
4. **Spracherkennung testen**: Mikrofon → Aufnehmen → Stopp
5. **TTS testen**: Edge TTS sollte funktionieren (Cloud-basiert)

### 3.3 Performance prüfen
```bash
# Während AIfred läuft (in neuem Terminal):

# CPU/RAM Monitor
htop

# GPU-Auslastung prüfen (für RTX 3060)
nvidia-smi -l 1

# Ollama Status
ollama ps
```

**Erwartete Performance (qwen2.5:32b):**
- RAM-Usage: ~20-24 GB während Inferenz
- CPU-Temp: sollte bei ~60-70°C bleiben (bessere Kühlung als Mini-PC!)
- Inferenz-Zeit: ~30-50% schneller als Mini-PC (9900X3D > Ryzen)

---

## ⚙️ Phase 4: Systemd Service einrichten (Optional)

### 4.1 Service-Datei erstellen
```bash
sudo nano /etc/systemd/system/aifred-intelligence.service
```

**Inhalt:**
```ini
[Unit]
Description=AIfred Intelligence Voice Assistant
After=network.target ollama.service

[Service]
Type=simple
User=YOUR_WSL_USERNAME
Group=YOUR_WSL_USERNAME
WorkingDirectory=/home/YOUR_WSL_USERNAME/Projekte/AIfred-Intelligence
Environment="PATH=/home/YOUR_WSL_USERNAME/Projekte/AIfred-Intelligence/venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="PYTHONUNBUFFERED=1"
ExecStart=/home/YOUR_WSL_USERNAME/Projekte/AIfred-Intelligence/venv/bin/python -u /home/YOUR_WSL_USERNAME/Projekte/AIfred-Intelligence/aifred_intelligence.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**Wichtig:** Ersetze `YOUR_WSL_USERNAME` mit deinem WSL-Usernamen!

### 4.2 Service aktivieren
```bash
# Daemon reload
sudo systemctl daemon-reload

# Service enablen (Autostart)
sudo systemctl enable aifred-intelligence.service

# Service starten
sudo systemctl start aifred-intelligence.service

# Status prüfen
sudo systemctl status aifred-intelligence.service

# Logs live anzeigen
sudo journalctl -u aifred-intelligence.service -f
```

### 4.3 Service-Befehle
```bash
# Status
sudo systemctl status aifred-intelligence.service

# Stoppen
sudo systemctl stop aifred-intelligence.service

# Starten
sudo systemctl start aifred-intelligence.service

# Neu starten
sudo systemctl restart aifred-intelligence.service

# Autostart deaktivieren
sudo systemctl disable aifred-intelligence.service

# Logs der letzten 100 Zeilen
sudo journalctl -u aifred-intelligence.service -n 100
```

---

## 🔒 Phase 5: SSL/HTTPS einrichten (Optional)

**Falls du von außen (mobil) zugreifen willst:**

### 5.1 Let's Encrypt Zertifikate
```bash
cd ~/Projekte/AIfred-Intelligence

# SSL-Ordner erstellen
mkdir -p ssl

# Zertifikate kopieren (falls vorhanden)
# z.B. von narnia.spdns.de oder eigene Domain
sudo cp /etc/letsencrypt/live/YOUR_DOMAIN/privkey.pem ssl/
sudo cp /etc/letsencrypt/live/YOUR_DOMAIN/fullchain.pem ssl/

# Permissions anpassen
sudo chown $USER:$USER ssl/*.pem
chmod 600 ssl/privkey.pem
chmod 644 ssl/fullchain.pem
```

AIfred erkennt die Zertifikate automatisch und startet mit HTTPS!

**Ohne SSL-Zertifikate:**
- AIfred läuft auf HTTP (Port 8443)
- Funktioniert perfekt für lokalen Zugriff
- Für mobilen Zugriff brauchst du SSL

---

## 🎯 Phase 6: Portabilität prüfen

### 6.1 Pfade sind jetzt automatisch relativ!
Das Projekt verwendet seit dem letzten Update **nur noch relative Pfade**:

```python
# Automatisch relativ zum Skript
PROJECT_ROOT = Path(__file__).parent.absolute()
PIPER_MODEL_PATH = PROJECT_ROOT / "piper_models" / "de_DE-thorsten-medium.onnx"
PIPER_BIN = PROJECT_ROOT / "venv" / "bin" / "piper"  # Linux/Mac
SETTINGS_FILE = PROJECT_ROOT / "assistant_settings.json"
```

**Das bedeutet:**
- ✅ Kein Hardcoding mehr von `/home/mp/...`
- ✅ Projekt funktioniert überall (WSL, Linux, Mac)
- ✅ Einfach Ordner kopieren → läuft sofort

### 6.2 Plattform-Unterstützung
- ✅ **Linux** (Mini-PC, WSL2, Server)
- ✅ **Windows** (über WSL2, Piper Binary automatisch erkannt)
- ✅ **macOS** (sollte funktionieren, nicht getestet)

---

## 🐛 Troubleshooting

### Problem: Ollama nicht erreichbar
```bash
# Ollama Status prüfen
sudo systemctl status ollama

# Ollama neu starten
sudo systemctl restart ollama

# Ollama-Logs
sudo journalctl -u ollama -f
```

### Problem: Port 8443 bereits belegt
```bash
# Port-Nutzung prüfen
sudo lsof -i :8443

# Falls belegt: Anderen Port in aifred_intelligence.py ändern
# Zeile suchen: app.launch(server_port=8443, ...)
```

### Problem: CUDA/GPU nicht erkannt (RTX 3060)
```bash
# NVIDIA Driver prüfen
nvidia-smi

# CUDA installieren (falls nicht vorhanden)
sudo apt install nvidia-cuda-toolkit -y

# PyTorch mit CUDA neu installieren
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
```

**Hinweis:** Ollama nutzt standardmäßig GPU automatisch (falls verfügbar)!

### Problem: faster-whisper lädt Models nicht
```bash
# Whisper-Cache löschen
rm -rf ~/.cache/huggingface/

# AIfred neu starten (lädt Models automatisch runter)
```

### Problem: SearXNG läuft nicht
```bash
cd ~/Projekte/AIfred-Intelligence/docker/searxng

# Container neu starten
docker compose down
docker compose up -d

# Logs prüfen
docker compose logs -f
```

### Problem: Piper TTS funktioniert nicht
```bash
# Piper Binary testen
~/Projekte/AIfred-Intelligence/venv/bin/piper --version

# Falls Fehler: Binary nochmal herunterladen
cd ~/Projekte/AIfred-Intelligence
rm venv/bin/piper
wget https://github.com/rhasspy/piper/releases/download/v1.2.0/piper_amd64.tar.gz
tar -xzf piper_amd64.tar.gz -C venv/bin/ --strip-components=1
chmod +x venv/bin/piper
```

### Problem: WSL2 startet nicht automatisch
```bash
# In PowerShell (Windows):
# WSL Autostart aktivieren
wsl --set-default-version 2

# Systemd in WSL aktivieren (für Services)
# /etc/wsl.conf erstellen:
sudo nano /etc/wsl.conf
```

**Inhalt:**
```ini
[boot]
systemd=true
```

Dann WSL neu starten:
```powershell
wsl --shutdown
wsl
```

---

## 📊 Performance-Vergleich: Mini-PC vs. Hauptrechner

### Mini-PC (aktuell):
- CPU: AMD Ryzen (älter)
- RAM: 32 GB
- Inferenz qwen2.5:32b: ~537s (Final Answer)
- CPU-Temp: 85-87°C (throttling)

### Hauptrechner (erwartet):
- CPU: AMD Ryzen 9 9900X3D (3D V-Cache!)
- GPU: RTX 3060 (12GB VRAM) - Ollama kann GPU nutzen!
- RAM: 32 GB+
- **Erwartete Inferenz qwen2.5:32b: ~200-300s** (50% schneller!)
- **CPU-Temp: ~60-70°C** (bessere Kühlung)

**GPU-Acceleration mit Ollama:**
Falls die RTX 3060 genutzt wird, könnten Inferenzen sogar noch schneller sein!

---

## ✅ Checkliste Migration

- [ ] Mini-PC: Service gestoppt
- [ ] Mini-PC: Archiv erstellt
- [ ] WSL2: Archiv entpackt
- [ ] WSL2: Python 3.10+ installiert
- [ ] WSL2: Ollama installiert & Models geladen
- [ ] WSL2: Docker installiert
- [ ] WSL2: Virtual Environment erstellt
- [ ] WSL2: Dependencies installiert
- [ ] WSL2: Piper TTS installiert (optional)
- [ ] WSL2: API Keys konfiguriert (.env)
- [ ] WSL2: SearXNG gestartet
- [ ] WSL2: AIfred manuell getestet
- [ ] WSL2: Systemd Service eingerichtet (optional)
- [ ] WSL2: SSL-Zertifikate kopiert (optional)
- [ ] Performance-Test durchgeführt

---

## 🎩 Fertig!

Nach erfolgreicher Migration sollte **AIfred Intelligence** auf deinem Hauptrechner laufen:

- **Schnellere Inferenzen** (9900X3D + RTX 3060!)
- **Bessere Kühlung** (keine 87°C mehr!)
- **Gleiche Features** (portables Projekt!)
- **Zugriff von Windows aus** via `http://localhost:8443`

Bei Problemen: Logs prüfen mit `sudo journalctl -u aifred-intelligence.service -f`

---

**AIfred Intelligence** - *AI at your service* 🎩
