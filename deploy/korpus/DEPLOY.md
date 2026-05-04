# Korpus-Tool Deployment auf Narnia

UI fuer die Vector-DB unter `https://narnia.spdns.de:8443/korpus/` —
Suche, Uebersicht, Upload, Verwaltung. Backend (FastAPI) auf
127.0.0.1:8005, reverse-proxied von nginx.

Alle Quelldateien liegen im Repo. Die Schritte unten brauchen sudo,
weil sie an `/var/www/html/`, `/etc/nginx/` und `/etc/systemd/system/`
schreiben.

## 1. UI ablegen

```bash
sudo mkdir -p /var/www/html/korpus
sudo cp ~/Projekte/AIfred-Intelligence/deploy/korpus/index.html /var/www/html/korpus/
sudo chown -R www-data:www-data /var/www/html/korpus
```

## 2. landing.html aktualisieren (Korpus-Tile + AI-ATC umstellen)

Die Aenderungen:

- Korpus-Tile (Folder-Symbol, lila-Akzent) zwischen AIfred und Code Editor
- AI-ATC verschoben von Position 2 nach zwischen System Monitoring und ComfyUI

```bash
sudo cp /var/www/html/landing.html /var/www/html/landing.html.bak-$(date +%Y%m%d-%H%M%S)
sudo nano /var/www/html/landing.html
```

**Stelle 1:** direkt nach dem `</a>` der AIfred-Card, vor dem
AI-ATC-Block, einfuegen:

```html
        <a class="card" href="/korpus/" style="--card-accent: linear-gradient(135deg, #2d1b69, #4338ca)">
            <div class="card-front">
                <div class="icon">&#128218;</div>
                <div class="info">
                    <h2>Korpus</h2>
                    <p>Vector-DB Suche, Verwaltung &amp; Upload</p>
                </div>
            </div>
            <div class="card-back" style="background: linear-gradient(135deg, #2d1b69, #4338ca)">
                <div class="back-icon">&#128218;</div>
                <div class="back-name">Korpus</div>
            </div>
        </a>
```

**Stelle 2:** den ganzen AI-ATC-Block (`<a class="card" href="/ai-atc/" ...>`
bis zum schliessenden `</a>`) **ausschneiden** und **nach dem
System-Monitoring-Block** einfuegen (vor dem ComfyUI-Block).

## 3. nginx Reverse-Proxy einrichten

Inhalt von `deploy/korpus/nginx-korpus.conf` in den HTTPS-server-Block
in `/etc/nginx/sites-available/narnia` einfuegen — nahe der
bestehenden `/aifred/`- und `/ai-atc/`-Eintraege:

```bash
sudo nano /etc/nginx/sites-available/narnia
# Block aus deploy/korpus/nginx-korpus.conf einfuegen
sudo nginx -t && sudo systemctl reload nginx
```

## 4. FastAPI-Service als systemd-Unit

```bash
sudo cp ~/Projekte/AIfred-Intelligence/systemd/aifred-corpus-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now aifred-corpus-server
sudo systemctl status aifred-corpus-server
```

Logs: `journalctl -u aifred-corpus-server -f`

## 5. Test

```bash
# Lokal:
curl http://127.0.0.1:8005/api/health

# Ueber nginx:
curl -k https://narnia.spdns.de:8443/korpus/api/health

# Im Browser:
https://narnia.spdns.de:8443/korpus/
```

## CLI-Tool (kein Deployment noetig)

```bash
venv/bin/python scripts/search_corpus.py "Heiliger Geist" --folder bibel
venv/bin/python scripts/search_corpus.py --grep "ewigen Gericht verfallen"
```

## Rollback

```bash
sudo rm -rf /var/www/html/korpus
sudo cp /var/www/html/landing.html.bak-* /var/www/html/landing.html
sudo systemctl disable --now aifred-corpus-server
sudo rm /etc/systemd/system/aifred-corpus-server.service
sudo systemctl daemon-reload
# nginx-Bloecke manuell rausnehmen, dann reload
```
