#!/bin/bash
#
# AIfred Intelligence - Python Environment Setup
# Erstellt venv und installiert Python-Dependencies
#

set -e  # Exit on error

echo "=================================================="
echo "  AIfred Intelligence - Python Setup"
echo "=================================================="
echo ""

# Farben für Output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "📂 Projekt-Verzeichnis: $PROJECT_DIR"
echo ""

# Check if running with sudo (should NOT be run with sudo)
if [ "$EUID" -eq 0 ]; then
    echo -e "${RED}❌ Dieses Script sollte NICHT mit sudo ausgeführt werden!${NC}"
    echo "   Führe es als normaler User aus: ./scripts/setup-python.sh"
    exit 1
fi

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python 3 ist nicht installiert${NC}"
    echo "   Installiere mit: sudo apt install python3 python3-venv python3-pip"
    exit 1
fi

PYTHON_VERSION_FULL=$(python3 --version)
echo -e "${GREEN}✅ $PYTHON_VERSION_FULL gefunden${NC}"

# Verify version >= 3.10 (Reflex requirement)
PY_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
PY_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo -e "${RED}❌ Python ${PY_MAJOR}.${PY_MINOR} ist zu alt — mindestens 3.10 wird benötigt.${NC}"
    exit 1
fi

# Verify venv module is available — Debian/Ubuntu split this out (python3-venv).
if ! python3 -c 'import venv' &>/dev/null; then
    echo -e "${RED}❌ Python venv-Modul fehlt.${NC}"
    echo "   Installiere mit: sudo apt install python3-venv"
    exit 1
fi
echo ""

# Create venv if it doesn't exist
VENV_DIR="$PROJECT_DIR/venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Erstelle Virtual Environment..."
    python3 -m venv "$VENV_DIR"
    echo -e "${GREEN}✅ Virtual Environment erstellt${NC}"
else
    echo -e "${YELLOW}ℹ️  Virtual Environment existiert bereits${NC}"
fi
echo ""

# Activate venv
echo "🔧 Aktiviere Virtual Environment..."
# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"

# Sanity-Check: pip muss in der venv vorhanden sein. Auf Debian-Minimal
# kann python3-venv ohne ensurepip geliefert werden — dann ist das venv
# zwar erstellt aber ohne pip nutzlos.
if ! python -m pip --version &>/dev/null; then
    echo -e "${RED}❌ pip ist im venv nicht verfügbar (ensurepip fehlt?).${NC}"
    echo "   Installiere: sudo apt install python3-venv python3-pip"
    exit 1
fi
echo ""

# Upgrade pip
echo "⬆️  Upgrade pip..."
python -m pip install --upgrade pip
echo ""

# Install requirements
# pip install -r ist idempotent: bereits installierte Pakete, die die
# requirements.txt-Constraints (z.B. reflex>=0.8.17) erfüllen, bleiben auf
# ihrer aktuellen Version stehen. Das ist gewollt — ein blindes --upgrade
# könnte eine funktionierende Installation auf eine neuere Version heben,
# die Breaking-Changes hat (typischer Fall: Reflex-Major-Bump bricht den
# patch-reflex.py-Anchor oder ändert die Config-API).
REQUIREMENTS_FILE="$PROJECT_DIR/requirements.txt"
if [ -f "$REQUIREMENTS_FILE" ]; then
    echo "📥 Installiere Python-Dependencies..."
    echo "   (Dies kann einige Minuten dauern...)"
    echo ""
    pip install -r "$REQUIREMENTS_FILE"
    echo ""
    echo -e "${GREEN}✅ Alle Dependencies installiert${NC}"
    echo ""
    echo -e "${YELLOW}ℹ️  Hinweis zu Updates:${NC}"
    echo "   Bereits installierte Pakete wurden NICHT automatisch hochgezogen."
    echo "   Wenn du bewusst auf die neuesten Versionen upgraden willst:"
    echo "       source venv/bin/activate"
    echo "       pip install --upgrade -r requirements.txt"
    echo "   ACHTUNG: Major-Bumps (z.B. Reflex 0.8 → 0.9) können Breaking-Changes"
    echo "   bringen. Vorher Changelog checken + ggf. patch-reflex.py neu prüfen."
else
    echo -e "${RED}❌ requirements.txt nicht gefunden: $REQUIREMENTS_FILE${NC}"
    exit 1
fi

echo ""

# Install Playwright browser for JS-heavy web scraping.
# Soft-fail: ohne System-Libs (libnss3, libxkbcommon0, …) schlägt der
# Chromium-Download fehl. Web-Research funktioniert trotzdem über
# SearXNG/Brave/Tavily — Playwright ist nur für JS-Pages.
echo "🌐 Installiere Playwright Browser (Chromium)..."
if playwright install chromium; then
    echo -e "${GREEN}✅ Playwright Chromium installiert${NC}"
else
    echo -e "${YELLOW}⚠️  Playwright-Installation fehlgeschlagen — Web-Research mit JS-Seiten nicht möglich.${NC}"
    echo "   Nachholen mit: source venv/bin/activate && playwright install --with-deps chromium"
fi
echo ""

# Reflex hat einen Routing-Bug bei frontend_path (siehe CLAUDE.md →
# "Reflex Patch"). Wir patchen route.py idempotent — bei zukünftigem
# Upstream-Fix wird das automatisch übersprungen.
echo "🔧 Wende Reflex-Patch (frontend_path Route-Matching) an..."
python "$SCRIPT_DIR/patch-reflex.py" || true

echo ""
echo "=================================================="
echo -e "${GREEN}✅ Python Setup abgeschlossen!${NC}"
echo "=================================================="
echo ""
echo "📊 Nützliche Befehle:"
echo "   Virtual Environment aktivieren: source venv/bin/activate"
echo "   AIfred starten: reflex run"
echo "   Tests ausführen: pytest"
echo ""
