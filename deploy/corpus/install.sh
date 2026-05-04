#!/usr/bin/env bash
# Installs the Corpus tool on Narnia.
#
# Idempotent: re-running updates the UI / nginx / systemd unit instead
# of duplicating. Backs up landing.html before replacing.
#
# Run from anywhere:
#     sudo bash ~/Projekte/AIfred-Intelligence/deploy/corpus/install.sh
#
# What it does:
# 1. Copies the UI to /var/www/html/corpus/
# 2. Backs up + replaces /var/www/html/landing.html (Corpus tile + AI-ATC reorder)
# 3. Patches /etc/nginx/sites-available/narnia with the /corpus/ + /corpus/api/
#    location blocks, then reloads nginx
# 4. Installs + enables the systemd unit aifred-corpus-server.service
# 5. Smoke-tests /api/health
set -euo pipefail

# ── paths ──────────────────────────────────────────────────────────
REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_DIR="$REPO_DIR/deploy/corpus"
SYSTEMD_DIR="$REPO_DIR/systemd"

NGINX_CONF="/etc/nginx/sites-available/narnia"
LANDING="/var/www/html/landing.html"
CORPUS_WWW="/var/www/html/corpus"
SYSTEMD_UNIT_DST="/etc/systemd/system/aifred-corpus-server.service"

# ── pre-flight ─────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    echo "❌ Bitte mit sudo ausfuehren."
    exit 1
fi
for f in \
    "$DEPLOY_DIR/index.html" \
    "$DEPLOY_DIR/landing.html" \
    "$DEPLOY_DIR/nginx-corpus.conf" \
    "$SYSTEMD_DIR/aifred-corpus-server.service"; do
    [[ -f "$f" ]] || { echo "❌ fehlt: $f"; exit 1; }
done

echo "▶ Corpus-Deployment startet"
echo "  Repo: $REPO_DIR"

# ── Migration: alte "Korpus" (mit K) Reste entfernen ───────────────
# Frueher hiess das Tool noch "Korpus" mit K. Aufraeumen falls noch da.
LEGACY_WWW="/var/www/html/korpus"
if [[ -d "$LEGACY_WWW" ]]; then
    echo "▶ Migration: entferne alte $LEGACY_WWW"
    rm -rf "$LEGACY_WWW"
fi
if grep -q "location /korpus/" "$NGINX_CONF" 2>/dev/null; then
    echo "▶ Migration: entferne alte /korpus/ Bloecke aus $NGINX_CONF"
    cp "$NGINX_CONF" "$NGINX_CONF.bak-migration-$(date +%Y%m%d-%H%M%S)"
    python3 - <<'PY'
import re
from pathlib import Path
p = Path("/etc/nginx/sites-available/narnia")
c = p.read_text()
patterns = [
    r"\n\s*# ─── Korpus.*?(?=\n\s*# ─── |\n\s*location |\n\}\Z)",
    r"\n\s*location = /korpus[^\n]*\{[^{}]*\}",
    r"\n\s*location /korpus/[^\n]*\{(?:[^{}]|\{[^{}]*\})*\}",
]
for pat in patterns:
    c = re.sub(pat, "", c, flags=re.DOTALL)
p.write_text(c)
print("  alte /korpus/ Bloecke entfernt")
PY
fi

# ── 1. UI ──────────────────────────────────────────────────────────
echo "▶ 1/5 UI nach $CORPUS_WWW"
mkdir -p "$CORPUS_WWW"
cp "$DEPLOY_DIR/index.html" "$CORPUS_WWW/index.html"
chown -R www-data:www-data "$CORPUS_WWW"

# ── 2. landing.html ────────────────────────────────────────────────
echo "▶ 2/5 landing.html (mit Backup)"
if ! cmp -s "$DEPLOY_DIR/landing.html" "$LANDING"; then
    cp "$LANDING" "$LANDING.bak-$(date +%Y%m%d-%H%M%S)"
    cp "$DEPLOY_DIR/landing.html" "$LANDING"
    chown www-data:www-data "$LANDING"
    echo "  landing.html ersetzt (Backup gespeichert)"
else
    echo "  landing.html ist bereits aktuell"
fi

# ── 3. nginx ───────────────────────────────────────────────────────
echo "▶ 3/5 nginx /corpus/ + /corpus/api/"
if grep -q "location /corpus/" "$NGINX_CONF"; then
    echo "  Corpus-Bloecke schon in $NGINX_CONF — uebersprungen"
else
    cp "$NGINX_CONF" "$NGINX_CONF.bak-$(date +%Y%m%d-%H%M%S)"
    # Finde die letzte schliessende } im File (Ende des HTTPS-Server-Blocks)
    # und fuege unsere Bloecke davor ein. Indentation wie der Rest des Files.
    python3 - <<PY
import re, sys
from pathlib import Path

conf_path = Path("$NGINX_CONF")
snippet_path = Path("$DEPLOY_DIR/nginx-corpus.conf")

conf = conf_path.read_text()
snippet = snippet_path.read_text()
# Snippet mit 4-Space-Indent versehen, Kommentar-Header dran
indented = "\n".join("    " + l if l.strip() else l for l in snippet.splitlines())
block = "\n    # ─── Corpus (Vector-DB Suche/Admin) — siehe deploy/corpus/install.sh ───\n" + indented + "\n"

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

# ── 4. systemd ─────────────────────────────────────────────────────
echo "▶ 4/5 systemd Unit aifred-corpus-server"
cp "$SYSTEMD_DIR/aifred-corpus-server.service" "$SYSTEMD_UNIT_DST"
systemctl daemon-reload
systemctl enable --now aifred-corpus-server.service
sleep 2
systemctl --no-pager --lines=3 status aifred-corpus-server.service || true

# ── 5. Smoke-Test ──────────────────────────────────────────────────
echo "▶ 5/5 Smoke-Test"
sleep 1
if curl -fs http://127.0.0.1:8005/api/health > /tmp/corpus-health.json; then
    echo "  ✅ /api/health: $(cat /tmp/corpus-health.json)"
else
    echo "  ⚠ /api/health antwortet nicht — journalctl -u aifred-corpus-server -e"
fi

echo ""
echo "✅ Fertig. UI: https://narnia.spdns.de:8443/corpus/"
echo "   Logs: journalctl -u aifred-corpus-server -f"
