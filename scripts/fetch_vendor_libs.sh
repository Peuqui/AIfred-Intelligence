#!/usr/bin/env bash
# Holt die lokal gespiegelten JS-Visualisierungs-Bibliotheken nach assets/vendor/.
#
# Warum lokal: render_html rendert nur noch mit hart blockiertem externem Netz
# (fail-closed, siehe browser_render.py) — Modell-Visualisierungen dürfen keine
# externen CDNs mehr laden. Diese Libs werden über localhost (Reflex assets/)
# ausgeliefert und bleiben damit nutzbar. Fehlt eine Lib, hier ergänzen.
#
# Idempotent: bereits vorhandene, valide Dateien werden übersprungen.
set -euo pipefail

VENDOR_DIR="$(cd "$(dirname "$0")/.." && pwd)/assets/vendor"
mkdir -p "$VENDOR_DIR"

# name|version|url  (Major-gepinnt via jsdelivr-npm; three exakt, da build/three.min.js
# ab r161 entfällt)
LIBS=(
  "chart.umd.min.js|4|https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"
  "d3.min.js|7|https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"
  "plotly.min.js|2|https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2/plotly.min.js"
  "three.min.js|0.160.1|https://cdn.jsdelivr.net/npm/three@0.160.1/build/three.min.js"
)

for entry in "${LIBS[@]}"; do
  IFS='|' read -r name version url <<< "$entry"
  dest="$VENDOR_DIR/$name"
  if [ -s "$dest" ]; then
    echo "✓ $name ($version) bereits vorhanden — übersprungen"
    continue
  fi
  echo "↓ $name ($version) ← $url"
  tmp="$(mktemp)"
  if curl -fsSL "$url" -o "$tmp" && [ "$(wc -c < "$tmp")" -gt 10000 ]; then
    mv "$tmp" "$dest"
    echo "  ✅ $(wc -c < "$dest") bytes"
  else
    rm -f "$tmp"
    echo "  ❌ Download fehlgeschlagen ($url) — bitte URL/Version prüfen" >&2
    exit 1
  fi
done

echo "Fertig. Vendor-Libs in: $VENDOR_DIR"
ls -la "$VENDOR_DIR"
