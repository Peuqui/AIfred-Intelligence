# Systemd Service Files für AIfred Intelligence

Dieses Verzeichnis enthält die Systemd-Service-Dateien für den produktiven Betrieb von AIfred Intelligence.

## 📦 Enthaltene Services

### 1. `aifred-chromadb.service`
Startet und verwaltet den ChromaDB Docker-Container für den Vector Cache.

**Features:**
- Startet automatisch beim Systemstart
- Wartet auf Docker-Service
- Neustart bei Fehlern

### 2. `aifred-intelligence.service`
Der Haupt-AIfred-Service (Reflex-App).

**Features:**
- Wartet auf Ollama und ChromaDB
- Setzt `AIFRED_ENV=prod` für MiniPC-Betrieb
- Automatischer Neustart bei Fehlern
- Logging via journalctl

## 🚀 Installation

### Erstinstallation

```bash
# 1. Service-Dateien kopieren
sudo cp systemd/aifred-chromadb.service /etc/systemd/system/
sudo cp systemd/aifred-intelligence.service /etc/systemd/system/

# 2. Services aktivieren
sudo systemctl daemon-reload
sudo systemctl enable aifred-chromadb.service
sudo systemctl enable aifred-intelligence.service

# 3. Services starten
sudo systemctl start aifred-chromadb.service
sudo systemctl start aifred-intelligence.service

# 4. Status prüfen
systemctl status aifred-chromadb.service
systemctl status aifred-intelligence.service
```

### Nach Code-Updates

```bash
# Nur AIfred neu starten (ChromaDB bleibt laufen)
sudo systemctl restart aifred-intelligence.service
```

### Nach Service-Datei-Änderungen

```bash
# Service-Dateien neu kopieren
sudo cp systemd/*.service /etc/systemd/system/

# Daemon neu laden und Services neu starten
sudo systemctl daemon-reload
sudo systemctl restart aifred-chromadb.service
sudo systemctl restart aifred-intelligence.service
```

## 📊 Monitoring

### Logs ansehen

```bash
# AIfred Logs (live)
journalctl -u aifred-intelligence.service -f

# ChromaDB Logs
journalctl -u aifred-chromadb.service -f

# Beide zusammen
journalctl -u aifred-intelligence.service -u aifred-chromadb.service -f

# Letzte 100 Zeilen
journalctl -u aifred-intelligence.service -n 100
```

### Service-Status

```bash
# Alle AIfred-Services auf einen Blick
systemctl status aifred-*

# Detaillierter Status
systemctl status aifred-intelligence.service
systemctl status aifred-chromadb.service
```

## 🔧 Troubleshooting

### AIfred startet nicht

```bash
# 1. ChromaDB-Status prüfen
systemctl status aifred-chromadb.service
docker ps | grep chromadb

# 2. Ollama-Status prüfen
systemctl status ollama.service

# 3. Logs prüfen
journalctl -u aifred-intelligence.service -n 50
```

### ChromaDB-Container läuft nicht

```bash
# Container manuell starten
cd /home/mp/Projekte/AIfred-Intelligence/docker
docker compose up -d chromadb

# Oder über Service
sudo systemctl restart aifred-chromadb.service
```

### Nach Systemstart funktioniert AIfred nicht

```bash
# Start-Reihenfolge prüfen
systemctl list-dependencies aifred-intelligence.service

# Sollte zeigen:
# aifred-intelligence.service
# ├─aifred-chromadb.service
# │ └─docker.service
# ├─ollama.service
# └─network.target
```

## ⚙️ Konfiguration

### Umgebungsvariablen

Die wichtigste Umgebungsvariable ist **`AIFRED_ENV=prod`** in `aifred-intelligence.service`:

- `AIFRED_ENV=dev`: API-URL = `http://172.30.8.72:8002` (Hauptrechner/WSL)
- `AIFRED_ENV=prod`: API-URL = `https://narnia.spdns.de:8443` (MiniPC)

**⚠️ WICHTIG**: Ohne `AIFRED_ENV=prod` werden alle API-Requests an den Entwicklungsrechner weitergeleitet!

### Service anpassen

Wenn du die Service-Dateien anpasst:
1. Bearbeite die Dateien in diesem `systemd/` Verzeichnis
2. Kopiere sie nach `/etc/systemd/system/`
3. Führe `sudo systemctl daemon-reload` aus
4. Starte die Services neu

## 🔄 Service-Befehle Übersicht

```bash
# Starten
sudo systemctl start aifred-intelligence.service

# Stoppen
sudo systemctl stop aifred-intelligence.service

# Neu starten
sudo systemctl restart aifred-intelligence.service

# Status
systemctl status aifred-intelligence.service

# Beim Boot aktivieren
sudo systemctl enable aifred-intelligence.service

# Beim Boot deaktivieren
sudo systemctl disable aifred-intelligence.service
```

## 📝 Hinweise

- ChromaDB muss vor AIfred starten (wird durch `Requires=` sichergestellt)
- AIfred startet automatisch neu bei Fehlern (`Restart=always`)
- Logs werden persistent in journald gespeichert
- Die Services werden beim Systemstart automatisch gestartet (wenn enabled)
