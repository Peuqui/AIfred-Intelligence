#!/usr/bin/env bash
# Holt die lokal gespiegelten JS-Visualisierungs-Bibliotheken nach assets/vendor/.
#
# Warum lokal: render_html rendert nur noch mit hart blockiertem externem Netz
# (fail-closed, siehe browser_render.py) — Modell-Visualisierungen dürfen keine
# externen CDNs mehr laden. Diese Libs werden über localhost (assets/vendor, vom Server
# unter /vendor ausgeliefert) und bleiben damit nutzbar. Fehlt eine Lib, hier ergänzen.
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

# three.min.js beginnt mit einer console.warn-Zeile ("Scripts build/three.js ... are
# deprecated with r150+"). Sie ging mit jeder Render-Rückmeldung ans Modell; deshalb
# wird die erste Zeile durch ein einzelnes "!" ersetzt (gültiges JS, das Komma am
# Zeilenende der Originalzeile verkettet dann direkt mit dem Rest). Nur für frisch
# geladene Dateien, passt die erste Zeile nicht, wird abgebrochen.
strip_three_warning() {
  python3 -I - "$1" <<'PYEOF' || exit 1
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    content = handle.read()
first_line, separator, rest = content.partition("\n")
if not (first_line.startswith("console.warn('Scripts \"build/three.js\"") and first_line.endswith("),")):
    print(f"  ❌ Erste Zeile von three.min.js hat sich geändert, Warnzeile nicht erkannt: {first_line[:80]!r}", file=sys.stderr)
    sys.exit(1)
with open(path, "w", encoding="utf-8") as handle:
    handle.write("!" + separator + rest)
PYEOF
}

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
    if [ "$name" = "three.min.js" ]; then
      strip_three_warning "$tmp"
    fi
    mv "$tmp" "$dest"
    echo "  ✅ $(wc -c < "$dest") bytes"
  else
    rm -f "$tmp"
    echo "  ❌ Download fehlgeschlagen ($url) — bitte URL/Version prüfen" >&2
    exit 1
  fi
done

# three.js r160 als ES-Modul mit Zusatzteilen (OrbitControls, Nachbearbeitung, lil-gui ...)
# aus dem npm-Tarball; Einbindung per Import-Map (siehe Prompt execute_code). Der Tarball
# wird gegen dist.integrity der npm-Registry geprüft. Bewusst nicht kopiert: WebGPU/Knoten-
# Materialien, große Wasm-Bibliotheken, Exporter, WebXR.
THREE_MODULE="$VENDOR_DIR/three/three.module.min.js"
if [ -s "$THREE_MODULE" ]; then
  echo "✓ three/three.module.min.js (0.160.0) bereits vorhanden — übersprungen"
else
  echo "↓ three 0.160.0 (ES-Modul + Zusatzteile) ← registry.npmjs.org"
  python3 -I - "$VENDOR_DIR/three" <<'PYEOF'
import base64
import hashlib
import json
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

target = Path(sys.argv[1])
META_URL = "https://registry.npmjs.org/three/0.160.0"
ADDON_DIRS = [
    "animation", "cameras", "controls", "curves", "effects", "environments",
    "geometries", "helpers", "interactive", "lights", "lines", "loaders",
    "materials", "math", "misc", "modifiers", "objects", "postprocessing",
    "renderers", "shaders", "textures", "utils",
]
LIB_FILES = [
    "fflate.module.js", "stats.module.js", "lil-gui.module.min.js",
    "tween.module.js", "potpack.module.js",
]
# Brauchen Teile, die nicht mitkopiert werden (WebGPU, Wasm, Exporter ...)
REMOVE = [
    "renderers/common", "renderers/webgpu", "renderers/webgl", "renderers/webgl-legacy",
    "loaders/KTX2Loader.js", "loaders/LottieLoader.js", "loaders/MMDLoader.js",
    "loaders/MaterialXLoader.js", "loaders/TIFFLoader.js", "loaders/TTFLoader.js",
    "loaders/VRMLLoader.js", "geometries/SDFGeometryGenerator.js", "objects/InstancedPoints.js",
]


def fail(message: str) -> None:
    print(f"  ❌ {message}", file=sys.stderr)
    sys.exit(1)


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


meta = json.loads(fetch(META_URL))
tarball = fetch(meta["dist"]["tarball"])

integrity = meta["dist"]["integrity"]
algorithm, _, expected = integrity.partition("-")
if algorithm != "sha512":
    fail(f"Unerwartetes Integritätsformat: {integrity}")
actual = base64.b64encode(hashlib.sha512(tarball).digest()).decode()
if actual != expected:
    fail("Integritätsprüfung des three-Tarballs fehlgeschlagen — nichts geschrieben")

work = Path(tempfile.mkdtemp())
try:
    archive = work / "three.tgz"
    archive.write_bytes(tarball)
    unpacked = work / "unpacked"
    unpacked.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(unpacked, filter="data")
    package = unpacked / "package"
    jsm = package / "examples" / "jsm"

    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(package / "build" / "three.module.min.js", target / "three.module.min.js")
    shutil.copy2(package / "LICENSE", target / "LICENSE")
    addons = target / "addons"
    for directory in ADDON_DIRS:
        shutil.copytree(jsm / directory, addons / directory, dirs_exist_ok=True)
    (addons / "libs").mkdir(parents=True, exist_ok=True)
    for lib in LIB_FILES:
        shutil.copy2(jsm / "libs" / lib, addons / "libs" / lib)
    (addons / "capabilities").mkdir(parents=True, exist_ok=True)
    shutil.copy2(jsm / "capabilities" / "WebGL.js", addons / "capabilities" / "WebGL.js")
finally:
    shutil.rmtree(work, ignore_errors=True)

for relative in REMOVE:
    path = addons / relative
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)

# Jeder relative Import muss auflösen, sonst bricht das Modul im Browser still ab
import_pattern = re.compile(r"""from\s*(['"])(\.{1,2}/[^'"]+)\1""")
unresolved = []
for source in sorted(addons.rglob("*.js")):
    for match in import_pattern.finditer(source.read_text(encoding="utf-8")):
        if not (source.parent / match.group(2)).resolve().is_file():
            unresolved.append(f"{source.relative_to(target)} → {match.group(2)}")
if unresolved:
    fail("Nicht auflösbare Imports:\n    " + "\n    ".join(unresolved))

print(f"  ✅ three/ geschrieben, alle relativen Imports lösen auf ({len(list(addons.rglob('*.js')))} Dateien)")
PYEOF
fi

# Rapier (3D-Physik-Engine, Apache-2.0) in der "compat"-Fassung: das WebAssembly steckt als base64 in der einen Datei,
# es wird nichts nachgeladen. Aus dem npm-Tarball, gegen dist.integrity der Registry geprüft (nur Datei und Lizenz).
RAPIER_MODULE="$VENDOR_DIR/rapier/rapier.module.js"
if [ -s "$RAPIER_MODULE" ]; then
  echo "✓ rapier/rapier.module.js (0.21.0) bereits vorhanden — übersprungen"
else
  echo "↓ rapier3d-compat 0.21.0 ← registry.npmjs.org"
  python3 -I - "$VENDOR_DIR/rapier" <<'PYEOF'
import base64
import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

target = Path(sys.argv[1])
META_URL = "https://registry.npmjs.org/@dimforge/rapier3d-compat/0.21.0"


def fail(message: str) -> None:
    print(f"  ❌ {message}", file=sys.stderr)
    sys.exit(1)


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=180) as response:
        return response.read()


meta = json.loads(fetch(META_URL))
tarball = fetch(meta["dist"]["tarball"])
algorithm, _, expected = meta["dist"]["integrity"].partition("-")
if algorithm != "sha512" or base64.b64encode(hashlib.sha512(tarball).digest()).decode() != expected:
    fail("Integritätsprüfung des rapier-Tarballs fehlgeschlagen — nichts geschrieben")

work = Path(tempfile.mkdtemp())
try:
    (work / "rapier.tgz").write_bytes(tarball)
    unpacked = work / "unpacked"
    unpacked.mkdir()
    with tarfile.open(work / "rapier.tgz") as tar:
        tar.extractall(unpacked, filter="data")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(unpacked / "package" / "dist" / "rapier.mjs", target / "rapier.module.js")
    shutil.copy2(unpacked / "package" / "LICENSE", target / "LICENSE")
finally:
    shutil.rmtree(work, ignore_errors=True)
print(f"  ✅ rapier/rapier.module.js geschrieben ({(target / 'rapier.module.js').stat().st_size} bytes)")
PYEOF
fi

echo "Fertig. Vendor-Libs in: $VENDOR_DIR"
ls -la "$VENDOR_DIR"
