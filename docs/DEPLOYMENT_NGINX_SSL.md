# AIfred Intelligence - Deployment mit nginx und SSL

## Übersicht

Diese Anleitung beschreibt die Konfiguration von AIfred Intelligence mit nginx als Reverse Proxy und SSL-Verschlüsselung für externen Zugriff.

## Architektur

```
Internet → narnia.spdns.de:8443 (HTTPS)
    ↓
Fritzbox Port-Forwarding (8443 → 192.168.0.252:8443)
    ↓
nginx (SSL-Terminierung auf Port 8443)
    ├── Backend-Routes (/api/*, /_event, /_upload) → localhost:8002
    └── Frontend (alles andere) → localhost:3002
```

## 1. Reflex Konfiguration (rxconfig.py)

**WICHTIG**: Die `api_url` MUSS auf die öffentlich erreichbare URL gesetzt werden!

```python
config = rx.Config(
    app_name="aifred",
    backend_host="0.0.0.0",  # Listen on all interfaces
    backend_port=8002,
    frontend_port=3002,
    frontend_host="0.0.0.0",  # Frontend auch auf allen Interfaces!
    # KRITISCH: api_url muss die öffentliche URL sein!
    api_url="https://narnia.spdns.de:8443",
    env=rx.Env.PROD,
)
```

## 2. Systemd Service Configuration

`/etc/systemd/system/aifred-intelligence.service`:

```ini
[Unit]
Description=AIfred Intelligence Voice Assistant (Reflex Version)
After=network.target ollama.service
Requires=ollama.service

[Service]
Type=simple
User=mp
Group=mp
WorkingDirectory=/home/mp/Projekte/AIfred-Intelligence
Environment="PATH=/home/mp/Projekte/AIfred-Intelligence/venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="PYTHONUNBUFFERED=1"
ExecStart=/home/mp/Projekte/AIfred-Intelligence/venv/bin/python -m reflex run --frontend-port 3002 --backend-port 8002 --backend-host 0.0.0.0
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## 3. nginx Configuration

`/etc/nginx/sites-available/narnia`:

```nginx
server {
    listen 8443 ssl;
    server_name narnia.spdns.de;

    ssl_certificate /home/mp/Projekte/AIfred-Intelligence/ssl/fullchain.pem;
    ssl_certificate_key /home/mp/Projekte/AIfred-Intelligence/ssl/privkey.pem;

    client_max_body_size 100M;

    # Backend API und WebSocket - MUSS an Port 8002!
    location ~ ^/(api/|_event|_upload) {
        proxy_pass http://127.0.0.1:8002;
        proxy_http_version 1.1;

        # WebSocket headers
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Standard headers
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        # Disable buffering
        proxy_buffering off;
        proxy_request_buffering off;

        # Timeouts für WebSockets
        proxy_connect_timeout 7d;
        proxy_read_timeout 7d;
        proxy_send_timeout 7d;
    }

    # Frontend - alles andere an Port 3002
    location / {
        proxy_pass http://127.0.0.1:3002;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

## 4. Vite Configuration

`.web/vite.config.js` muss angepasst werden:

```javascript
server: {
    port: process.env.PORT,
    hmr: true,
    host: "0.0.0.0",
    allowedHosts: ["narnia.spdns.de", "192.168.0.252", "localhost"],
    // ...
}
```

## 5. Installation

```bash
# 1. nginx Konfiguration aktivieren
sudo ln -s /etc/nginx/sites-available/narnia /etc/nginx/sites-enabled/narnia
sudo nginx -t
sudo systemctl reload nginx

# 2. Service aktivieren
sudo systemctl enable aifred-intelligence.service
sudo systemctl start aifred-intelligence.service

# 3. Status prüfen
sudo systemctl status aifred-intelligence
sudo systemctl status nginx
```

## 6. Wichtige Punkte

### Was NICHT funktioniert:
- ❌ `api_url` auf interne IP setzen (z.B. `http://192.168.0.252:8002`)
- ❌ Alles auf einen Port (3002) leiten
- ❌ Backend und Frontend auf dem gleichen Port laufen lassen

### Was funktioniert:
- ✅ `api_url` auf öffentliche URL (`https://narnia.spdns.de:8443`)
- ✅ nginx trennt Backend (8002) und Frontend (3002) Routes
- ✅ WebSocket Support durch korrekte Headers
- ✅ SSL-Verschlüsselung über nginx

## 7. Router-Konfiguration

In der Fritzbox (oder anderem Router):
- Port 8443 (extern) → 192.168.0.252:8443 (intern)

## 8. Zugriff

- **Lokal**: http://192.168.0.252:3002
- **Extern**: https://narnia.spdns.de:8443

## Troubleshooting

### "Cannot connect to server" Fehler
- Prüfe ob `api_url` in rxconfig.py auf die öffentliche URL zeigt
- Stelle sicher, dass nginx Backend-Routes an Port 8002 weiterleitet

### "Host not allowed" Fehler
- Füge den Hostnamen zu `allowedHosts` in vite.config.js hinzu

### WebSocket-Verbindung schlägt fehl
- Prüfe nginx headers für WebSocket Support
- Stelle sicher, dass `/_event` Route an Port 8002 weitergeleitet wird

## Logs

```bash
# Service logs
sudo journalctl -u aifred-intelligence -f

# nginx logs
sudo tail -f /var/log/nginx/error.log
sudo tail -f /var/log/nginx/access.log
```