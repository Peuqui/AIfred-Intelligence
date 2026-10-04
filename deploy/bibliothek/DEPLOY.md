# AIfred Bibliothek – Deployment auf Narnia

Oberfläche für die Vector-DB unter `https://example.com:8443/bibliothek/` —
Suche, Übersicht, Upload, Verwaltung. Backend (FastAPI,
`scripts/bibliothek_server.py`) auf 127.0.0.1:8005, reverse-proxied von nginx.

Alle Quelldateien liegen im Repo. Die Schritte brauchen sudo, weil sie an
`/var/www/html/`, `/etc/nginx/` und `/etc/systemd/system/` schreiben.

## Schnellweg

```bash
sudo scripts/install-services.sh          # installiert u. a. aifred-bibliothek.service
sudo bash deploy/bibliothek/install.sh    # UI, nginx, Dienst starten, Smoke-Test
```

`install.sh` ist idempotent. Die Unit installiert ausschließlich
`install-services.sh` (setzt Benutzer und Projektpfad in die Vorlage ein).

## Einzelschritte

### 1. UI ablegen

```bash
sudo mkdir -p /var/www/html/bibliothek
sudo cp ~/Projekte/AIfred-Intelligence/deploy/bibliothek/index.html /var/www/html/bibliothek/
sudo chown -R www-data:www-data /var/www/html/bibliothek
```

### 2. nginx Reverse-Proxy

Inhalt von `deploy/bibliothek/nginx-bibliothek.conf` in den HTTPS-server-Block
in `/etc/nginx/sites-available/narnia` einfügen:

```bash
sudo nano /etc/nginx/sites-available/narnia
sudo nginx -t && sudo systemctl reload nginx
```

### 3. Dienst

```bash
sudo scripts/install-services.sh
sudo systemctl start aifred-bibliothek.service
```

Start beim Booten ist optional (fragt `install-services.sh`).
Logs: `journalctl -u aifred-bibliothek -f`

### 4. Kachel auf der Startseite (optional)

Für `/var/www/html/landing.html`:

```html
        <a class="card" href="/bibliothek/" style="--card-accent: linear-gradient(135deg, #2d1b69, #4338ca)">
            <div class="card-front">
                <div class="icon">&#128218;</div>
                <div class="info">
                    <h2>AIfred Bibliothek</h2>
                    <p>Vector-DB Suche, Verwaltung &amp; Upload</p>
                </div>
            </div>
            <div class="card-back" style="background: linear-gradient(135deg, #2d1b69, #4338ca)">
                <div class="back-icon">&#128218;</div>
                <div class="back-name">Bibliothek</div>
            </div>
        </a>
```

## Test

```bash
curl http://127.0.0.1:8005/api/health                        # lokal
curl -k https://example.com:8443/bibliothek/api/health        # über nginx
```

## CLI-Werkzeug (kein Deployment nötig)

```bash
venv/bin/python scripts/bibliothek_search.py "Heiliger Geist" --folder bibel
venv/bin/python scripts/bibliothek_search.py --grep "ewigen Gericht verfallen"
```

## Rückbau

```bash
sudo rm -rf /var/www/html/bibliothek
sudo systemctl disable --now aifred-bibliothek.service
sudo rm /etc/systemd/system/aifred-bibliothek.service
sudo systemctl daemon-reload
# nginx-Blöcke von Hand entfernen, dann: sudo nginx -t && sudo systemctl reload nginx
```
