#!/usr/bin/env bash
# Installs the AIfred Bibliothek UI on Narnia.
#
# Idempotent: re-running updates the UI / nginx instead of duplicating.
#
# Run from anywhere:
#     sudo bash ~/Projekte/AIfred-Intelligence/deploy/bibliothek/install.sh
#
# What it does:
# 1. Copies the UI to /var/www/html/bibliothek/
# 2. Patches /etc/nginx/sites-available/narnia with the /bibliothek/ +
#    /bibliothek/api/ location blocks, then reloads nginx
# 3. Starts aifred-bibliothek.service — the unit itself is installed by
#    scripts/install-services.sh (one place renders its placeholders)
# 4. Smoke-tests /api/health
set -euo pipefail

# ── paths ──────────────────────────────────────────────────────────
REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_DIR="$REPO_DIR/deploy/bibliothek"

NGINX_CONF="/etc/nginx/sites-available/narnia"
BIBLIOTHEK_WWW="/var/www/html/bibliothek"
SYSTEMD_UNIT="aifred-bibliothek.service"

# ── pre-flight ─────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    echo "❌ Bitte mit sudo ausfuehren."
    exit 1
fi
for f in \
    "$DEPLOY_DIR/index.html" \
    "$DEPLOY_DIR/nginx-bibliothek.conf"; do
    [[ -f "$f" ]] || { echo "❌ fehlt: $f"; exit 1; }
done
[[ -f "/etc/systemd/system/$SYSTEMD_UNIT" ]] \
    || { echo "❌ $SYSTEMD_UNIT fehlt — zuerst sudo scripts/install-services.sh"; exit 1; }

echo "▶ Bibliothek-Deployment startet"
echo "  Repo: $REPO_DIR"

# ── 1. UI ──────────────────────────────────────────────────────────
echo "▶ 1/4 UI nach $BIBLIOTHEK_WWW"
mkdir -p "$BIBLIOTHEK_WWW"
cp "$DEPLOY_DIR/index.html" "$BIBLIOTHEK_WWW/index.html"
chown -R www-data:www-data "$BIBLIOTHEK_WWW"

# ── 2. nginx ───────────────────────────────────────────────────────
echo "▶ 2/4 nginx /bibliothek/ + /bibliothek/api/"
if grep -q "location /bibliothek/" "$NGINX_CONF"; then
    echo "  Bibliothek-Bloecke schon in $NGINX_CONF — uebersprungen"
else
    cp "$NGINX_CONF" "$NGINX_CONF.bak-$(date +%Y%m%d-%H%M%S)"
    # Finde die letzte schliessende } im File (Ende des HTTPS-Server-Blocks)
    # und fuege unsere Bloecke davor ein. Indentation wie der Rest des Files.
    python3 - <<PY
import re, sys
from pathlib import Path

conf_path = Path("$NGINX_CONF")
snippet_path = Path("$DEPLOY_DIR/nginx-bibliothek.conf")

conf = conf_path.read_text()
snippet = snippet_path.read_text()
# Snippet mit 4-Space-Indent versehen, Kommentar-Header dran
indented = "\n".join("    " + l if l.strip() else l for l in snippet.splitlines())
block = "\n    # ─── AIfred Bibliothek (Vector-DB Suche/Admin) — siehe deploy/bibliothek/install.sh ───\n" + indented + "\n"

# Letztes "}" im File ist Ende des HTTPS-server-Blocks. Davor einfuegen.
last = conf.rfind("\n}")
if last < 0:
    sys.exit("nginx config: kein abschliessendes '}' gefunden")
new = conf[:last] + block + conf[last:]
conf_path.write_text(new)
print("  nginx config gepatcht")
PY
    if ! nginx -t 2>&1 | tail -3; then
        echo "❌ nginx -t fehlgeschlagen, rolle zurueck"
        cp "$NGINX_CONF.bak-"* "$NGINX_CONF"
        exit 1
    fi
fi
systemctl reload nginx

# ── 3. systemd ─────────────────────────────────────────────────────
echo "▶ 3/4 $SYSTEMD_UNIT starten"
systemctl start "$SYSTEMD_UNIT"
sleep 2
systemctl --no-pager --lines=3 status "$SYSTEMD_UNIT" || true

# ── 4. Smoke-Test ──────────────────────────────────────────────────
echo "▶ 4/4 Smoke-Test"
sleep 1
if HEALTH=$(curl -fs http://127.0.0.1:8005/api/health); then
    echo "  ✅ /api/health: $HEALTH"
else
    echo "  ⚠ /api/health antwortet nicht — journalctl -u $SYSTEMD_UNIT -e"
fi

echo ""
echo "✅ Fertig. UI: https://example.com:8443/bibliothek/"
echo "   Logs: journalctl -u $SYSTEMD_UNIT -f"
