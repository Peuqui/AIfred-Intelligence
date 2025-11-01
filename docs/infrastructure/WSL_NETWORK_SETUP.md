# WSL Netzwerk-Setup für AIfred Intelligence

## Problem
WSL hat eine eigene interne IP-Adresse (aktuell: **172.30.8.72**), die vom lokalen Netzwerk (Handy, Tablet) nicht erreichbar ist.

Windows-Host hat IP: **192.168.0.1** (im lokalen Netzwerk erreichbar)

## Lösung: Port-Forwarding von Windows zu WSL

### 1. Port-Forwarding einrichten

**In Windows PowerShell (als Administrator):**

```powershell
# Frontend Port forwarding (React UI)
netsh interface portproxy add v4tov4 listenport=3002 listenaddress=0.0.0.0 connectport=3002 connectaddress=172.30.8.72

# Backend Port forwarding (Python API)
netsh interface portproxy add v4tov4 listenport=8002 listenaddress=0.0.0.0 connectport=8002 connectaddress=172.30.8.72
```

### 2. Firewall-Regeln erstellen

**In Windows PowerShell (als Administrator):**

```powershell
# Erlaube eingehende Verbindungen auf Port 3002
New-NetFirewallRule -DisplayName "AIfred Reflex Frontend" -Direction Inbound -LocalPort 3002 -Protocol TCP -Action Allow

# Erlaube eingehende Verbindungen auf Port 8002
New-NetFirewallRule -DisplayName "AIfred Reflex Backend" -Direction Inbound -LocalPort 8002 -Protocol TCP -Action Allow
```

### 3. Port-Forwarding prüfen

**In Windows PowerShell:**

```powershell
# Zeige alle aktiven Port-Forwardings
netsh interface portproxy show all
```

**Erwartete Ausgabe:**
```
Listen on ipv4:             Connect to ipv4:

Address         Port        Address         Port
--------------- ----------  --------------- ----------
0.0.0.0         3002        172.30.8.72     3002
0.0.0.0         8002        172.30.8.72     8002
```

---

## Zugriff

### Von Windows-Desktop
```
http://localhost:3002
```
oder
```
http://192.168.0.1:3002
```

### Von Handy/Tablet (im gleichen WLAN)
```
http://192.168.0.1:3002
```

---

## Port-Forwarding löschen (falls nötig)

**In Windows PowerShell (als Administrator):**

```powershell
# Lösche Frontend Port-Forwarding
netsh interface portproxy delete v4tov4 listenport=3002 listenaddress=0.0.0.0

# Lösche Backend Port-Forwarding
netsh interface portproxy delete v4tov4 listenport=8002 listenaddress=0.0.0.0
```

---

## Alternative: WSL Mirrored Networking (Windows 11 mit WSL 2.0+)

Falls Port-Forwarding nicht funktioniert, kannst du **Mirrored Networking** aktivieren:

### 1. WSL Config bearbeiten

**In WSL:**
```bash
sudo nano /etc/wsl.conf
```

**Füge hinzu:**
```ini
[boot]
systemd=true

[user]
default=mp

[wsl2]
networkingMode=mirrored
```

### 2. WSL neu starten

**In Windows PowerShell:**
```powershell
wsl --shutdown
```

**Danach WSL neu öffnen:**
```powershell
wsl
```

**Mit Mirrored Networking hat WSL die gleiche IP wie Windows (192.168.0.1)!**

---

## Troubleshooting

### WSL-IP hat sich geändert?

Die WSL-IP kann sich nach jedem Windows-Neustart ändern. Prüfe die aktuelle IP:

**In WSL:**
```bash
hostname -I | awk '{print $1}'
```

**Update Port-Forwarding mit neuer IP:**
```powershell
# Alte löschen
netsh interface portproxy delete v4tov4 listenport=3002 listenaddress=0.0.0.0
netsh interface portproxy delete v4tov4 listenport=8002 listenaddress=0.0.0.0

# Neue mit aktueller WSL-IP erstellen
netsh interface portproxy add v4tov4 listenport=3002 listenaddress=0.0.0.0 connectport=3002 connectaddress=<NEUE_WSL_IP>
netsh interface portproxy add v4tov4 listenport=8002 listenaddress=0.0.0.0 connectport=8002 connectaddress=<NEUE_WSL_IP>
```

### Port bereits belegt?

Prüfe welche Ports belegt sind:

**In Windows PowerShell:**
```powershell
netstat -ano | findstr :3002
netstat -ano | findstr :8002
```

---

## Aktueller Status

- **WSL-IP:** 172.30.8.72
- **Windows-Host-IP:** 192.168.0.1
- **Ports:** 3002 (Frontend), 8002 (Backend)
- **Config:** rxconfig.py nutzt 192.168.0.1 (mit Port-Forwarding)

---

**Erstellt:** 2025-10-25
**WSL-Version:** WSL2
**Windows-Version:** Windows 10/11
