# 📦 Migration & Portabilität

Anleitung zum Portieren von AIfred Intelligence auf einen anderen Rechner.

---

## ✅ Was ist portabel (automatisch)

Diese Komponenten funktionieren **ohne Änderungen** auf jedem System:

### 🔧 Code & Konfiguration
- ✅ **Python Code** - Alle `.py` Dateien und `lib/` Modul
- ✅ **Pfade** - Verwendet `PROJECT_ROOT = Path(__file__).parent.parent.absolute()`
- ✅ **Dependencies** - `requirements.txt` für pip install
- ✅ **Settings** - `assistant_settings.json` (wird automatisch erstellt)

### 🌐 Externe Services (localhost)
- ✅ **Ollama** - Läuft auf `http://localhost:11434` (Standard-Port)
- ✅ **SearXNG** - Läuft auf `http://localhost:8888` (konfigurierbar)
- ✅ **Gradio UI** - Bindet an `0.0.0.0:7860` (alle Interfaces)

---

## ⚙️ Was muss angepasst werden

### 1. **SSL-Zertifikate** (Optional für HTTPS)

**Aktueller Pfad** (wird automatisch erkannt):
```python
# lib/config.py
SSL_KEYFILE = PROJECT_ROOT / "ssl" / "privkey.pem"
SSL_CERTFILE = PROJECT_ROOT / "ssl" / "fullchain.pem"
```

**Auf neuem System:**
- Entweder: Eigene Zertifikate in `ssl/` Verzeichnis legen
- Oder: Ohne SSL starten (HTTP statt HTTPS)

**Fallback**: Code prüft automatisch, ob Zertifikate existieren:
```python
if SSL_KEYFILE.exists() and SSL_CERTFILE.exists():
    # HTTPS
else:
    # HTTP
```

### 2. **Piper TTS Model** (Optional für lokales TTS)

**Aktueller Pfad**:
```python
# lib/config.py
PIPER_MODEL_PATH = PROJECT_ROOT / "piper_models" / "de_DE-thorsten-medium.onnx"
```

**Auf neuem System:**
1. Piper Model herunterladen (falls lokal TTS gewünscht)
2. In `piper_models/` Verzeichnis legen
3. **Fallback**: Ohne Piper läuft nur Edge TTS (Cloud)

### 3. **API Keys** (Optional für Brave/Tavily)

**`.env` Datei erstellen:**
```bash
cp .env.example .env
nano .env
```

**Inhalt**:
```env
BRAVE_API_KEY=your_brave_api_key_here
TAVILY_API_KEY=your_tavily_api_key_here
```

**Fallback**: Ohne API Keys läuft **SearXNG** als einzige Suchmaschine.

### 4. **Systemd Service** (Optional für Autostart)

**Service-Datei anpassen** (`/etc/systemd/system/aifred-intelligence.service`):

```ini
[Service]
User=<DEIN_USERNAME>                          # ← Anpassen!
WorkingDirectory=/pfad/zu/AIfred-Intelligence  # ← Anpassen!
ExecStart=/pfad/zu/venv/bin/python -u aifred_intelligence.py  # ← Anpassen!
Environment="PATH=/pfad/zu/venv/bin:/usr/local/bin:/usr/bin"  # ← Anpassen!
```

**Installation:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable aifred-intelligence.service
sudo systemctl start aifred-intelligence.service
```

---

## 📋 Migrations-Checkliste

### Schritt 1: Repository klonen
```bash
git clone https://github.com/Peuqui/AIfred-Intelligence.git
cd AIfred-Intelligence
```

### Schritt 2: Virtual Environment erstellen
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Schritt 3: Ollama installieren & Modelle pullen
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3:1.7b   # Für Automatik
ollama pull qwen3:8b     # Für Hauptmodell (empfohlen)
ollama pull qwen3:32b    # Optional: Beste Qualität
```

### Schritt 4: SearXNG starten (Docker)
```bash
cd docker/searxng
docker compose up -d
```

**Test**: Öffne `http://localhost:8888` im Browser

### Schritt 5: SSL-Zertifikate (Optional)
```bash
# Entweder: Eigene Zertifikate in ssl/ legen
mkdir -p ssl
cp /pfad/zu/privkey.pem ssl/
cp /pfad/zu/fullchain.pem ssl/

# Oder: Ohne SSL starten (HTTP)
# → Code erkennt automatisch fehlende Zertifikate
```

### Schritt 6: Piper TTS Model (Optional)
```bash
# Falls lokales TTS gewünscht:
mkdir -p piper_models
cd piper_models
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx
```

### Schritt 7: API Keys konfigurieren (Optional)
```bash
cp .env.example .env
nano .env  # Füge API Keys ein
```

### Schritt 8: Anwendung starten
```bash
source venv/bin/activate
python aifred_intelligence.py
```

**Öffne Browser**: `https://localhost:7860` (oder `http://...` ohne SSL)

---

## 🔄 Settings-Migration

**Settings werden automatisch migriert!**

Alte Settings (`settings.json`) werden beim ersten Start automatisch zu `assistant_settings.json` konvertiert:

```python
# lib/settings_manager.py
def load_settings():
    # Alte Settings laden
    if old_file.exists() and not new_file.exists():
        shutil.copy(old_file, new_file)
        # Migration erfolgreich
```

**Was wird migriert:**
- AI Model Auswahl
- Automatik Model
- Voice & TTS Settings
- Whisper Model
- Research Mode
- Alle User-Präferenzen

---

## 🛠️ Troubleshooting

### Problem: "ModuleNotFoundError: No module named 'lib'"
**Lösung**: Virtual Environment aktivieren:
```bash
source venv/bin/activate
pip install -r requirements.txt
```

### Problem: "Connection refused" bei Ollama
**Lösung**: Ollama Service starten:
```bash
ollama serve  # Oder: systemctl start ollama
```

### Problem: SSL-Fehler beim Start
**Lösung**: Zertifikate prüfen oder HTTP nutzen:
```bash
# Zertifikate prüfen
ls -la ssl/

# Oder: Ohne SSL testen (Code fällt auf HTTP zurück)
```

### Problem: SearXNG nicht erreichbar
**Lösung**: Docker Container prüfen:
```bash
cd docker/searxng
docker compose ps
docker compose logs
```

### Problem: Piper TTS funktioniert nicht
**Lösung**: Auf Edge TTS umschalten (Cloud):
```bash
# In UI: Settings → TTS Engine → "Edge TTS (Cloud)"
# Oder: Piper Model herunterladen (siehe Schritt 6)
```

---

## 📊 Portabilitäts-Übersicht

| Komponente | Portabel? | Aktion nötig |
|---|---|---|
| Python Code | ✅ Ja | Keine |
| lib/ Module | ✅ Ja | Keine |
| requirements.txt | ✅ Ja | `pip install -r requirements.txt` |
| settings.json | ✅ Ja | Wird automatisch migriert |
| Ollama Models | ⚠️ Neu pullen | `ollama pull <model>` |
| SearXNG Docker | ⚠️ Neu starten | `docker compose up -d` |
| SSL Zertifikate | ❌ Optional | Eigene Zertifikate oder ohne |
| Piper Model | ❌ Optional | Download oder Edge TTS nutzen |
| API Keys | ❌ Optional | `.env` neu erstellen |
| Systemd Service | ❌ Optional | Pfade anpassen |

---

## ✅ Minimale Portierung (ohne optionale Features)

**Was du wirklich brauchst:**
1. Repository klonen
2. Virtual Environment + Dependencies
3. Ollama installieren + Models pullen
4. SearXNG Docker starten
5. `python aifred_intelligence.py` ausführen

**Alles andere ist optional!**
- SSL: Nur für HTTPS nötig
- Piper: Nur für lokales TTS nötig (Edge TTS funktioniert ohne)
- API Keys: Nur für Brave/Tavily nötig (SearXNG funktioniert ohne)
- Systemd: Nur für Autostart nötig

---

**🎩 AIfred Intelligence - AI at your service**
