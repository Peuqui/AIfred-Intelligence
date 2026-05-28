#!/bin/bash
#
# AIfred Intelligence - Complete Installation Script
# Installiert alles: System-Deps, Python-Environment, Systemd-Services (optional)
#
# Modes:
#   ./scripts/install-all.sh                   normal fresh-install / update
#   ./scripts/install-all.sh --dry-run         no disk writes, no apt/pip/
#                                              systemctl side-effects. Shows
#                                              what each step WOULD do.
#                                              Service-Diffs delegated to
#                                              install-services.sh --dry-run
#   ./scripts/install-all.sh --no-overwrite    pass --no-overwrite to the
#                                              systemd installer (keep local
#                                              service file tweaks)

set -e  # Exit on error

# ── Argument parsing ────────────────────────────────────────────
DRY_RUN=0
NO_OVERWRITE=0
for arg in "$@"; do
    case "$arg" in
        --dry-run|-n)         DRY_RUN=1 ;;
        --no-overwrite|-N)    NO_OVERWRITE=1 ;;
        --help|-h)
            sed -n '2,15p' "$0"
            exit 0
            ;;
        *)
            echo "❌ Unknown flag: $arg"
            sed -n '2,15p' "$0"
            exit 1
            ;;
    esac
done

# Build flag passthrough for the systemd sub-installer.
SYSTEMD_FLAGS=()
[ "$DRY_RUN" = "1" ]      && SYSTEMD_FLAGS+=("--dry-run")
[ "$NO_OVERWRITE" = "1" ] && SYSTEMD_FLAGS+=("--no-overwrite")

if [ "$DRY_RUN" = "1" ]; then
    echo "=================================================="
    echo "  AIfred Intelligence - Installation (DRY-RUN)"
    echo "=================================================="
    echo "  No disk writes, no apt/pip/systemctl side-effects."
    [ "$NO_OVERWRITE" = "1" ] && echo "  --no-overwrite is honored in the simulation."
    echo ""
elif [ "$NO_OVERWRITE" = "1" ]; then
    echo "=================================================="
    echo "  AIfred Intelligence - Installation (NO-OVERWRITE)"
    echo "=================================================="
    echo "  Existing systemd service files are kept untouched."
    echo ""
else
    echo "=================================================="
    echo "  AIfred Intelligence - Vollständige Installation"
    echo "=================================================="
    echo ""
fi

# Farben für Output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "📂 Projekt-Verzeichnis: $PROJECT_DIR"
echo ""

# ============================================================
# Globale Step-Verification-Infrastruktur
# ============================================================
# Jeder Schritt verifiziert sich nach Abschluss selbst. Fehler werden
# nicht sofort abgebrochen (set -e wird in verify_step() umgangen),
# sondern in einer globalen Liste gesammelt — am Schluss gibt es eine
# Auswertung, und das Script endet mit Exit-Code != 0 falls Blocker.
STEP_FAILURES=()      # rote Blocker pro Schritt
STEP_WARNINGS=()      # gelbe Hinweise pro Schritt

# verify_step <human-name> <test-cmd> [optional-fix-hint]
# Führt <test-cmd> in einer Subshell aus. Bei Erfolg → grünes ✅, sonst
# rotes ❌ + Hint, +1 STEP_FAILURES.
#
# WICHTIG: Diese Funktion gibt IMMER Exit 0 zurück. Sie sammelt Failures nur
# im globalen Array — die finale Auswertung am Script-Ende entscheidet über
# den Exit-Code. Würde verify_step bei Failure `return 1` machen, würde
# `set -e` das Script beim ersten gescheiterten Check sofort abbrechen
# und alle nachfolgenden Checks + die Final-Auswertung NIE erreichen.
verify_step() {
    local name="$1"
    local cmd="$2"
    local hint="${3:-}"
    if bash -c "$cmd" &>/dev/null; then
        echo -e "   ${GREEN}✅ verifiziert:${NC} $name"
        return 0
    fi
    echo -e "   ${RED}❌ FEHLT:${NC} $name"
    [ -n "$hint" ] && echo -e "      ${YELLOW}→ $hint${NC}"
    STEP_FAILURES+=("$name${hint:+  ($hint)}")
    return 0
}

# warn_step <human-name> <test-cmd> [hint]
# Wie verify_step, aber als gelbe Warnung statt rotem Blocker (Feature-Lücke
# statt installation-broken). Gibt ebenfalls IMMER Exit 0 zurück (s.o.).
warn_step() {
    local name="$1"
    local cmd="$2"
    local hint="${3:-}"
    if bash -c "$cmd" &>/dev/null; then
        echo -e "   ${GREEN}✅ verifiziert:${NC} $name"
        return 0
    fi
    echo -e "   ${YELLOW}⚠️  fehlt:${NC} $name"
    [ -n "$hint" ] && echo -e "      ${YELLOW}→ $hint${NC}"
    STEP_WARNINGS+=("$name${hint:+  ($hint)}")
    return 0
}

# step_summary <schritt-name>
# Zeigt nach jedem Schritt eine Mini-Auswertung. Wird die Trefferliste in
# der Schluss-Auswertung erneut gezeigt — hier nur direktes Feedback.
step_summary() {
    local step="$1"
    echo ""
    echo -e "   ${BLUE}┄ Verifikation Schritt '$step' fertig${NC}"
}

# Warn if running as root — Python venv should not be owned by root
if [ "$EUID" -eq 0 ]; then
    echo -e "${RED}❌ Bitte NICHT mit sudo starten.${NC}"
    echo "   System-Dependencies werden gezielt mit sudo installiert,"
    echo "   alles andere muss als normaler User laufen (venv-Owner)."
    exit 1
fi

# ============================================================
# Dry-run: skip steps 1 + 2 + 2b..2g (system-deps, venv, embedding
# model, container builds). All of these are idempotent on a real run —
# already-installed packages skip themselves, the venv is reused if
# already present, ChromaDB/Whisper containers are no-ops if already
# up — so dry-running them gives no new information. The interesting
# dry-run is what install-services.sh would do to /etc/systemd/system,
# which is delegated below in Step 3.
# ============================================================
if [ "$DRY_RUN" = "1" ]; then
    echo -e "${YELLOW}📝 DRY-RUN: skipping Steps 1-2g (system-deps, Python env,${NC}"
    echo -e "${YELLOW}              ChromaDB, Whisper, SearXNG, TTS containers).${NC}"
    echo "   These steps are idempotent on a real run. To see actual"
    echo "   install effects, re-run without --dry-run."
    echo ""
else

# ============================================================
# SCHRITT 1: System-Dependencies (mit sudo)
# ============================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Schritt 1/3: System-Dependencies${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "Benötigt:"
echo "  • python3 (>=3.10), python3-venv, python3-pip — Python-Runtime + venv"
echo "  • poppler-utils — pdftotext für saubere PDF-Indexierung"
echo "  • ffmpeg        — Audio-Concat für TTS (XTTS, Edge-TTS Multi-Chunk)"
echo "  • bubblewrap    — Sandbox für 'execute_python' Tool"
echo "  • docker + docker-compose-plugin — ChromaDB Vector Cache + Whisper STT"
echo ""

# Detect package manager
if command -v apt &>/dev/null; then
    PKG="apt"
elif command -v dnf &>/dev/null; then
    PKG="dnf"
elif command -v pacman &>/dev/null; then
    PKG="pacman"
elif command -v brew &>/dev/null; then
    PKG="brew"
else
    PKG=""
fi

# apt update einmal vorab aggregieren, damit nicht pro fehlendem Paket
# `sudo apt update` separat läuft (war vorher: 8 Pakete fehlen → 8× apt update).
# Wir prüfen einmal alle Pflicht-Pakete, und wenn IRGENDEINS fehlt, machen
# wir EIN apt update. Spart auf einem Frischstart 30-60s.
APT_UPDATED=0
apt_ensure_update() {
    if [ "$PKG" = "apt" ] && [ "$APT_UPDATED" = "0" ]; then
        echo "🔄 apt update (einmalig)..."
        sudo apt update
        APT_UPDATED=1
    fi
}

# Prüfen ob irgendein Pflicht-Paket fehlt → dann gleich apt update vorziehen.
if [ "$PKG" = "apt" ]; then
    NEED_APT_UPDATE=0
    for cmd in python3 pdftotext ffmpeg bwrap docker; do
        command -v "$cmd" &>/dev/null || NEED_APT_UPDATE=1
    done
    python3 -c 'import venv' &>/dev/null || NEED_APT_UPDATE=1
    python3 -m pip --version &>/dev/null || NEED_APT_UPDATE=1
    docker compose version &>/dev/null 2>&1 || NEED_APT_UPDATE=1
    [ "$NEED_APT_UPDATE" = "1" ] && apt_ensure_update
fi

install_one() {
    local human="$1"; shift
    local check_cmd="$1"; shift
    # Remaining args are pkg-name per manager: apt:name dnf:name pacman:name brew:name
    local apt_name="" dnf_name="" pacman_name="" brew_name=""
    for spec in "$@"; do
        case "$spec" in
            apt:*) apt_name="${spec#apt:}" ;;
            dnf:*) dnf_name="${spec#dnf:}" ;;
            pacman:*) pacman_name="${spec#pacman:}" ;;
            brew:*) brew_name="${spec#brew:}" ;;
        esac
    done

    if eval "$check_cmd" &>/dev/null; then
        echo -e "${GREEN}✅ $human bereits installiert${NC}"
        return 0
    fi

    echo -e "${YELLOW}⚠️  $human fehlt — installiere...${NC}"
    case "$PKG" in
        apt)    apt_ensure_update; [ -n "$apt_name" ] && sudo apt install -y $apt_name ;;
        dnf)    [ -n "$dnf_name" ]    && sudo dnf install -y $dnf_name ;;
        pacman) [ -n "$pacman_name" ] && sudo pacman -S --noconfirm $pacman_name ;;
        brew)   [ -n "$brew_name" ]   && brew install $brew_name ;;
        *)
            echo -e "${RED}❌ Kein bekannter Paket-Manager — bitte $human manuell installieren.${NC}"
            return 1
            ;;
    esac

    if eval "$check_cmd" &>/dev/null; then
        echo -e "${GREEN}✅ $human installiert${NC}"
    else
        echo -e "${RED}❌ Installation von $human fehlgeschlagen${NC}"
        return 1
    fi
}

# Pflicht-Pakete (Fehler bricht ab)
install_one "Python 3" "command -v python3" \
    apt:python3 dnf:python3 pacman:python brew:python@3.12
install_one "python3-venv (PEP 405 venv-Modul)" "python3 -c 'import venv'" \
    apt:python3-venv dnf:python3 pacman:python brew:python@3.12
install_one "python3-pip" "command -v pip3 || python3 -m pip --version" \
    apt:python3-pip dnf:python3-pip pacman:python-pip brew:python@3.12
install_one "pdftotext (poppler-utils)" "command -v pdftotext" \
    apt:poppler-utils dnf:poppler-utils pacman:poppler brew:poppler
install_one "ffmpeg" "command -v ffmpeg" \
    apt:ffmpeg dnf:ffmpeg pacman:ffmpeg brew:ffmpeg
install_one "bubblewrap (bwrap)" "command -v bwrap" \
    apt:bubblewrap dnf:bubblewrap pacman:bubblewrap brew:bubblewrap
# curl wird gleich für den Ollama-Installer ('curl | sh') gebraucht. Minimal-
# Images (Debian-Slim, manche Container-Hosts) haben curl nicht out-of-the-box.
install_one "curl (Ollama-Installer holt sich darüber install.sh)" "command -v curl" \
    apt:curl dnf:curl pacman:curl brew:curl
# ca-certificates: ohne aktualisiertes Cert-Bundle scheitert TLS-Connect zu
# huggingface.co/ollama.com auf manchen Minimal-Images. Nur auf apt nötig
# (dnf/pacman/brew schippen das mit ihren TLS-Tools mit).
if [ "$PKG" = "apt" ]; then
    install_one "ca-certificates (TLS-Truststore für HF/Ollama)" \
        "[ -f /etc/ssl/certs/ca-certificates.crt ]" \
        apt:ca-certificates dnf:ca-certificates pacman:ca-certificates brew:openssl
fi

# Docker + Compose-Plugin — getrennt prüfen, da Compose v2 ein eigenes Paket ist.
# Track ob wir den User gerade frisch zur docker-Gruppe hinzugefügt haben — dann
# wirkt das in der aktuellen Shell noch nicht und wir müssen 'sg docker' nutzen,
# damit der spätere 'docker compose up -d chromadb' nicht an Permission scheitert.
DOCKER_GROUP_NEEDS_RELOGIN=0
if ! command -v docker &>/dev/null; then
    echo -e "${YELLOW}⚠️  docker fehlt — installiere...${NC}"
    case "$PKG" in
        apt)    apt_ensure_update; sudo apt install -y docker.io ;;
        dnf)    sudo dnf install -y docker ;;
        pacman) sudo pacman -S --noconfirm docker ;;
        brew)   brew install --cask docker ;;
        *)      echo -e "${RED}❌ Kein bekannter Paket-Manager — docker manuell installieren: https://docs.docker.com/engine/install/${NC}"; exit 1 ;;
    esac

    # Service starten + enable, User in docker-Gruppe (Linux)
    if command -v systemctl &>/dev/null; then
        sudo systemctl enable --now docker || true
    fi
    if [ "$(uname)" = "Linux" ] && getent group docker &>/dev/null; then
        if ! id -nG "$USER" | tr ' ' '\n' | grep -qx docker; then
            sudo usermod -aG docker "$USER"
            DOCKER_GROUP_NEEDS_RELOGIN=1
            echo -e "${YELLOW}ℹ️  $USER zur 'docker'-Gruppe hinzugefügt — neu einloggen für Effekt.${NC}"
        fi
    fi
    command -v docker &>/dev/null && echo -e "${GREEN}✅ docker installiert${NC}"
else
    echo -e "${GREEN}✅ docker bereits installiert${NC}"
    # Auch bei vorhandenem docker prüfen: ist der User schon Mitglied?
    if [ "$(uname)" = "Linux" ] && getent group docker &>/dev/null; then
        if ! id -nG "$USER" | tr ' ' '\n' | grep -qx docker; then
            echo -e "${YELLOW}ℹ️  $USER nicht in 'docker'-Gruppe — füge hinzu...${NC}"
            sudo usermod -aG docker "$USER"
            DOCKER_GROUP_NEEDS_RELOGIN=1
        fi
    fi
fi

# Helper: docker-Befehl ausführen, auch wenn die Gruppen-Membership in der
# aktuellen Shell noch nicht aktiv ist. 'sg docker -c "<cmd>"' startet eine
# Subshell mit der frischen Gruppen-Zugehörigkeit.
docker_run() {
    if [ "$DOCKER_GROUP_NEEDS_RELOGIN" = "1" ] && command -v sg &>/dev/null; then
        sg docker -c "$*"
    else
        bash -c "$*"
    fi
}

if ! docker compose version &>/dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  docker compose Plugin fehlt — installiere...${NC}"
    case "$PKG" in
        apt)    sudo apt install -y docker-compose-plugin ;;
        dnf)    sudo dnf install -y docker-compose-plugin ;;
        pacman) sudo pacman -S --noconfirm docker-compose ;;
        brew)   brew install docker-compose ;;
        *)      echo -e "${RED}❌ docker compose plugin manuell installieren.${NC}"; exit 1 ;;
    esac
    docker compose version &>/dev/null && echo -e "${GREEN}✅ docker compose installiert${NC}"
else
    echo -e "${GREEN}✅ docker compose bereits installiert${NC}"
fi

# ─── Verifikation Schritt 1: jedes Tool ist callable + Versions-Antwort ───
echo ""
echo -e "${BLUE}🔎 Verifiziere System-Dependencies...${NC}"
verify_step "python3 (>=3.10) aufrufbar" \
    "python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'" \
    "sudo apt install python3 (oder dnf/pacman/brew)"
verify_step "python3-venv-Modul importierbar" \
    "python3 -c 'import venv'" \
    "sudo apt install python3-venv"
verify_step "pip3 aufrufbar" \
    "python3 -m pip --version" \
    "sudo apt install python3-pip"
verify_step "pdftotext (poppler) aufrufbar" \
    "pdftotext -v" \
    "sudo apt install poppler-utils"
verify_step "ffmpeg aufrufbar" \
    "ffmpeg -version" \
    "sudo apt install ffmpeg"
verify_step "bwrap (bubblewrap) aufrufbar" \
    "bwrap --version" \
    "sudo apt install bubblewrap"
verify_step "docker-Client aufrufbar" \
    "docker --version" \
    "sudo apt install docker.io"
verify_step "docker compose-Plugin aufrufbar" \
    "docker compose version" \
    "sudo apt install docker-compose-plugin"
# Daemon-Erreichbarkeit. Wenn DOCKER_GROUP_NEEDS_RELOGIN, sg-wrappen.
verify_step "docker-Daemon erreichbar (Server antwortet)" \
    "$([ "$DOCKER_GROUP_NEEDS_RELOGIN" = "1" ] && echo "sg docker -c 'docker info'" || echo "docker info")" \
    "sudo systemctl start docker  (oder neu einloggen wenn Gruppe frisch)"

step_summary "1 — System-Dependencies"
echo ""
sleep 1

# ============================================================
# SCHRITT 2: Python Environment Setup (OHNE sudo)
# ============================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Schritt 2/3: Python Environment Setup${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

if [ -f "$SCRIPT_DIR/setup-python.sh" ]; then
    bash "$SCRIPT_DIR/setup-python.sh"
else
    echo -e "${RED}❌ Script nicht gefunden: $SCRIPT_DIR/setup-python.sh${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}✅ Python Environment bereit${NC}"
echo ""

# Playwright Chromium System-Libs (Pflicht).
# setup-python.sh holt den Browser-Binary (User-Kontext). Die System-Libs
# (libnss3, libxkbcommon0, libasound2t64, …) braucht aber root → hier mit
# sudo nachziehen. Ohne diese startet der Headless-Chromium nicht und
# Web-Research mit JS-Pages bleibt stumm ("browser closed" in den Logs).
#
# `playwright install-deps` erkennt den Paket-Manager selbst (apt/dnf) und
# kennt die Library-Liste pro Playwright-Version. Bei brew/pacman/etc. gibt
# Playwright nur einen Hinweis aus — kein Failure-Pfad nötig.
if [ -x "$PROJECT_DIR/venv/bin/playwright" ]; then
    echo -e "${BLUE}🌐 Installiere Playwright Chromium System-Libs (sudo)...${NC}"
    case "$PKG" in
        apt|dnf)
            if sudo "$PROJECT_DIR/venv/bin/playwright" install-deps chromium; then
                echo -e "${GREEN}✅ Playwright System-Libs installiert${NC}"
            else
                echo -e "${YELLOW}⚠️  playwright install-deps fehlgeschlagen — Web-Research mit JS-Pages evtl. broken.${NC}"
                echo "   Nachholen mit: sudo $PROJECT_DIR/venv/bin/playwright install-deps chromium"
            fi
            ;;
        *)
            echo -e "${YELLOW}ℹ️  install-deps wird von Playwright nur für apt/dnf nativ unterstützt.${NC}"
            echo "   Auf macOS/Arch sind die nötigen Libs i.d.R. bereits da."
            echo "   Bei 'browser closed'-Fehlern manuell die Playwright-Doc konsultieren:"
            echo "   https://playwright.dev/docs/browsers#install-system-dependencies"
            ;;
    esac
    echo ""
fi

# ─── Verifikation Schritt 2: venv existiert + Kern-Pakete importierbar ───
echo -e "${BLUE}🔎 Verifiziere Python-Environment...${NC}"
verify_step "venv-Python: $PROJECT_DIR/venv/bin/python" \
    "[ -x '$PROJECT_DIR/venv/bin/python' ]" \
    "Re-run scripts/setup-python.sh — venv-Erstellung fehlgeschlagen"
verify_step "venv-pip aufrufbar" \
    "'$PROJECT_DIR/venv/bin/python' -m pip --version" \
    "ensurepip-Problem — sudo apt install python3-venv python3-pip"
verify_step "import reflex" \
    "'$PROJECT_DIR/venv/bin/python' -c 'import reflex'" \
    "pip install -r requirements.txt im venv"
verify_step "import chromadb (Vector Cache Client)" \
    "'$PROJECT_DIR/venv/bin/python' -c 'import chromadb'" \
    "pip install -r requirements.txt im venv"
verify_step "import ollama (Embedding-Client)" \
    "'$PROJECT_DIR/venv/bin/python' -c 'import ollama'" \
    "pip install -r requirements.txt im venv"
verify_step "import fastapi (REST-API)" \
    "'$PROJECT_DIR/venv/bin/python' -c 'import fastapi'" \
    "pip install -r requirements.txt im venv"
verify_step "import httpx (HTTP-Client)" \
    "'$PROJECT_DIR/venv/bin/python' -c 'import httpx'" \
    "pip install -r requirements.txt im venv"
# Vision-Stack: cv2 = Motion-Detection + Frame-Grab + pHash; insightface
# + onnxruntime-gpu = Face-Recognition für Vigilantia. Alle drei sind
# in requirements.txt, daher harter verify_step. Fehlt eines, schlägt
# der Vigilantia-Plugin-Start mit ImportError fehl.
verify_step "import cv2 (Vision-Pipeline: motion, frames, pHash)" \
    "'$PROJECT_DIR/venv/bin/python' -c 'import cv2'" \
    "pip install -r requirements.txt im venv  (opencv-python-headless)"
verify_step "import numpy (cv2 + face-embeddings)" \
    "'$PROJECT_DIR/venv/bin/python' -c 'import numpy'" \
    "pip install -r requirements.txt im venv"
# insightface + onnxruntime-gpu sind only ein Soft-Requirement (für
# Vigilantia-Face-Recognition). Wenn der User Vision/Vigilantia nicht
# nutzt, ist der Import-Fehlschlag kein Blocker — daher warn_step.
warn_step "import insightface (Vigilantia Face-Recognition, optional)" \
    "'$PROJECT_DIR/venv/bin/python' -c 'import insightface'" \
    "pip install insightface onnxruntime-gpu  (nur falls Vigilantia genutzt wird)"
warn_step "import onnxruntime (insightface-Inference-Backend, optional)" \
    "'$PROJECT_DIR/venv/bin/python' -c 'import onnxruntime'" \
    "pip install onnxruntime-gpu  (CUDA) oder onnxruntime  (CPU-only)"
# Webcam-Zugriff via V4L2 setzt voraus, dass der User-Account in der
# 'video'-Gruppe ist. Sonst: opencv kann /dev/video* nicht öffnen,
# Vigilantia-Source-Discovery liefert "no devices" trotz angeschlossener
# Kamera. warn_step (kein Blocker — Server ohne Webcam ist legal).
warn_step "User in 'video'-Gruppe (Webcam-Zugriff für Vigilantia)" \
    "groups | grep -qw video" \
    "sudo usermod -aG video $USER  → dann neu einloggen (oder 'newgrp video')"
# Playwright Binary + Chromium-Browser sind Pflicht (Web-Research mit JS-Pages
# braucht beides). setup-python.sh holt den Browser-Binary, der vorige
# install-deps-Block holt die System-Libs. Wenn beide fehlschlagen, geht
# Web-Research mit JS-Seiten nicht — daher harter verify_step statt warn_step.
verify_step "playwright-Binary im venv" \
    "[ -x '$PROJECT_DIR/venv/bin/playwright' ]" \
    "pip install playwright im venv"
verify_step "Playwright Chromium-Browser installiert" \
    "ls $HOME/.cache/ms-playwright/chromium-*/chrome-linux/chrome 2>/dev/null | head -1 | grep -q ." \
    "venv/bin/playwright install chromium  (Browser-Binary)"
# Chromium startbar — wenn System-Libs fehlen (libnss3, libxkbcommon0, libasound2t64
# usw.), wirft der Browser-Launch direkt einen 'browser closed unexpectedly'-Error.
# Wir machen einen echten Headless-Launch-Test, der genau diese Klasse von Bugs fängt.
verify_step "Playwright Chromium startbar (System-Libs vollständig)" \
    "'$PROJECT_DIR/venv/bin/python' -c 'from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); b.close(); p.stop()'" \
    "sudo venv/bin/playwright install-deps chromium"
# Reflex-Patch idempotent prüfen — patch-reflex.py --check returnt 0 wenn ok.
verify_step "Reflex frontend_path-Patch angewandt (oder upstream gefixt)" \
    "'$PROJECT_DIR/venv/bin/python' '$SCRIPT_DIR/patch-reflex.py' --check" \
    "python scripts/patch-reflex.py erneut ausführen"

step_summary "2 — Python-Environment"
echo ""
sleep 1

# ============================================================
# SCHRITT 2b: Projekt-Initialisierung (.env, Verzeichnisse, Embedding-Modell)
# ============================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Schritt 2b: Projekt-Initialisierung${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# .env aus .env.example anlegen — nur wenn noch keine existiert.
if [ ! -f "$PROJECT_DIR/.env" ]; then
    if [ -f "$PROJECT_DIR/.env.example" ]; then
        cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
        chmod 600 "$PROJECT_DIR/.env"
        echo -e "${GREEN}✅ .env aus .env.example erstellt${NC}"
        echo -e "${YELLOW}   ℹ️  Vergiss nicht, API-Keys einzutragen (BRAVE_API_KEY, TAVILY_API_KEY, …).${NC}"
    else
        echo -e "${YELLOW}⚠️  .env.example fehlt — überspringe .env-Anlage.${NC}"
    fi
else
    echo -e "${GREEN}✅ .env existiert bereits${NC}"
fi

# ChromaDB-Volume-Verzeichnis vorher anlegen, damit Docker es nicht als
# root erstellt (sonst kann der User später keine Backups/Wartung
# direkt am Host-Filesystem machen).
CHROMA_DIR="$PROJECT_DIR/aifred_vector_cache"
if [ ! -d "$CHROMA_DIR" ]; then
    mkdir -p "$CHROMA_DIR"
    echo -e "${GREEN}✅ ChromaDB-Volume-Verzeichnis angelegt: $CHROMA_DIR${NC}"
else
    echo -e "${GREEN}✅ ChromaDB-Volume-Verzeichnis existiert${NC}"
fi

# Pflicht-Subdirs unter data/ proaktiv anlegen. Der Code legt sie zwar
# lazy beim ersten Zugriff an — aber wenn data/ noch nicht existiert und
# z.B. ein systemd-Service als anderer User darauf zugreift, kollidieren
# die Permissions. Besser einmal sauber anlegen.
for sub in sessions images tts_audio html_preview logs documents audio \
           media sandbox_output scheduler security message_hub; do
    mkdir -p "$PROJECT_DIR/data/$sub"
done
echo -e "${GREEN}✅ data/-Unterordner angelegt${NC}"

# Ollama-Check + Embedding-Modell ziehen.
# Ollama ist nicht "nice-to-have" — der Vector Cache (ChromaDB) braucht
# 'bge-m3' als Embedding-Modell. Ohne läuft das UI zwar an, aber jede
# Vektor-Suche (RAG, Dokumente, Memory) failt mit Connection-Error gegen
# http://localhost:11434. Wir kennzeichnen das hier deshalb klar als
# stark empfohlen (nicht als 'nur LLM-Backend, gibt ja Alternativen').
ollama_pull_bge_m3() {
    if timeout 10 ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qE '^bge-m3(:|$)'; then
        echo -e "${GREEN}✅ Embedding-Modell 'bge-m3' bereits gepulled${NC}"
        return 0
    fi
    echo "   Ziehe Embedding-Modell 'bge-m3' (~1.2 GB, multilingual, 8192 ctx)..."
    if ollama pull bge-m3; then
        echo -e "${GREEN}✅ bge-m3 erfolgreich gezogen${NC}"
    else
        echo -e "${YELLOW}⚠️  bge-m3 konnte nicht gezogen werden — Internet ok?${NC}"
        echo "   Nachholen mit: ollama pull bge-m3"
    fi
}

echo ""
echo "🦙 Ollama (Embedding-Provider + Starter-LLM-Backend)..."
echo "   Hinweis: Ollama liefert die 'bge-m3'-Embeddings für den ChromaDB"
echo "   Vector Cache. Ohne bge-m3 schlägt jede Vektor-Suche fehl"
echo "   (RAG, Dokumenten-Index, Memory). De-facto Pflicht."
echo ""
if command -v ollama &>/dev/null; then
    echo -e "${GREEN}✅ ollama installiert ($(ollama --version 2>&1 | head -1))${NC}"
    # Check ob der Ollama-Daemon erreichbar ist (sonst hängt 'ollama list' lange).
    if ! timeout 5 ollama list &>/dev/null; then
        echo -e "${YELLOW}⚠️  Ollama-Daemon nicht erreichbar — bge-m3-Pull übersprungen.${NC}"
        echo "   Daemon starten: ollama serve     (oder via systemd: sudo systemctl start ollama)"
        echo "   Nachholen:      ollama pull bge-m3"
    else
        ollama_pull_bge_m3
    fi
else
    echo -e "${YELLOW}⚠️  ollama nicht gefunden — STARK EMPFOHLEN zu installieren.${NC}"
    echo ""
    echo "   Der offizielle Installer ist ein Pipe-To-Shell:"
    echo "       curl -fsSL https://ollama.com/install.sh | sh"
    echo "   Er erkennt Hardware (CUDA/ROCm/CPU) automatisch, schreibt ein"
    echo "   systemd-Unit, legt den 'ollama'-Systemuser an und startet den"
    echo "   Daemon. Quellcode + Anleitung: https://ollama.com/download/linux"
    echo ""
    # Default JA — wer wirklich keine Embeddings will, kann skippen.
    read -p "Ollama jetzt installieren (curl | sh)? (J/n): " -n 1 -r OLLAMA_REPLY
    echo ""
    if [[ ! $OLLAMA_REPLY =~ ^[Nn]$ ]]; then
        if ! command -v curl &>/dev/null; then
            echo -e "${RED}❌ curl fehlt — bitte erst 'sudo apt install curl' (oder dnf/pacman/brew).${NC}"
        else
            echo "   Lade & führe Ollama-Installer aus..."
            if curl -fsSL https://ollama.com/install.sh | sh; then
                echo -e "${GREEN}✅ Ollama-Installer fertig${NC}"
                # Daemon kurz Zeit geben, sich nach dem systemd-Start zu melden.
                for _ in 1 2 3 4 5 6 7 8 9 10; do
                    timeout 2 ollama list &>/dev/null && break
                    sleep 1
                done

                if timeout 5 ollama list &>/dev/null; then
                    ollama_pull_bge_m3
                else
                    echo -e "${YELLOW}⚠️  Ollama-Daemon antwortet noch nicht.${NC}"
                    echo "   Nach dem Reboot/Login nachholen:  ollama pull bge-m3"
                fi
            else
                echo -e "${RED}❌ Ollama-Installation fehlgeschlagen.${NC}"
                echo "   Manuell nachholen: curl -fsSL https://ollama.com/install.sh | sh"
            fi
        fi
    else
        echo -e "${YELLOW}⏭️  Ollama-Installation übersprungen.${NC}"
        echo -e "${YELLOW}   ⚠️  ACHTUNG: Vector Cache (RAG, Dokumente, Memory) wird ohne bge-m3 NICHT funktionieren.${NC}"
        echo "   Später nachholen:"
        echo "       curl -fsSL https://ollama.com/install.sh | sh"
        echo "       ollama pull bge-m3                  # Embeddings (Pflicht für Vector Cache)"
        echo "       ollama pull qwen3:8b                # Beispiel-LLM (siehe README)"
    fi
fi
echo ""

# ─── Verifikation Schritt 2b: .env, data-Subdirs, Ollama + bge-m3 ───
echo -e "${BLUE}🔎 Verifiziere Projekt-Initialisierung...${NC}"
verify_step ".env existiert" \
    "[ -f '$PROJECT_DIR/.env' ]" \
    "cp .env.example .env"
verify_step "aifred_vector_cache/ Volume-Verzeichnis existiert" \
    "[ -d '$PROJECT_DIR/aifred_vector_cache' ]" \
    "mkdir -p aifred_vector_cache"
# Alle 12 data-Subdirs einzeln prüfen — der Code legt sie lazy an,
# aber wenn z.B. systemd als anderer User schreibt, gibt es Permission-Issues.
for sub in sessions images tts_audio html_preview logs documents audio \
           media sandbox_output scheduler security message_hub; do
    verify_step "data/$sub existiert" \
        "[ -d '$PROJECT_DIR/data/$sub' ]" \
        "mkdir -p '$PROJECT_DIR/data/$sub'"
done
# Ollama-Setup — als Warnung (gelb), AIfred startet auch ohne, aber Vector
# Cache failt.
if command -v ollama &>/dev/null; then
    warn_step "Ollama-Daemon antwortet" \
        "timeout 5 ollama list" \
        "ollama serve  (oder sudo systemctl start ollama)"
    warn_step "Embedding-Modell 'bge-m3' verfügbar" \
        "timeout 10 ollama list | awk 'NR>1{print \$1}' | grep -qE '^bge-m3(:|\$)'" \
        "ollama pull bge-m3"
else
    warn_step "ollama installiert" "command -v ollama" \
        "curl -fsSL https://ollama.com/install.sh | sh"
fi

step_summary "2b — Projekt-Initialisierung"
echo ""

# ============================================================
# SCHRITT 2c: ChromaDB-Container starten (Vector Cache)
# ============================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Schritt 2c: ChromaDB-Container starten${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "ChromaDB ist Pflicht für Vector Cache (RAG, Dokumente, Memory)."
echo "Wird unabhängig vom systemd-Setup direkt via docker compose gestartet,"
echo "damit AIfred auch ohne systemd-Pfad sofort lauffähig ist."
echo ""

CHROMA_STARTED=0
if command -v docker &>/dev/null && docker compose version &>/dev/null 2>&1; then
    # Prüfen ob Container schon läuft (idempotent — Re-Runs sollen nicht stören).
    if docker_run "docker ps --format '{{.Names}}' 2>/dev/null | grep -qx aifred-chromadb"; then
        echo -e "${GREEN}✅ ChromaDB-Container 'aifred-chromadb' läuft bereits${NC}"
        CHROMA_STARTED=1
    else
        echo "   Starte ChromaDB-Container..."
        if docker_run "cd '$PROJECT_DIR/docker' && docker compose up -d chromadb"; then
            echo -e "${GREEN}✅ docker compose up -d chromadb (Exit 0)${NC}"
            CHROMA_STARTED=1
        else
            echo -e "${YELLOW}⚠️  'docker compose up -d chromadb' fehlgeschlagen.${NC}"
            echo "   Mögliche Ursachen:"
            echo "     • docker-Daemon läuft nicht (sudo systemctl start docker)"
            echo "     • User noch nicht in docker-Gruppe aktiv (neu einloggen)"
            echo "   Manuell nachholen:"
            echo "       cd $PROJECT_DIR/docker && docker compose up -d chromadb"
        fi
    fi
else
    echo -e "${YELLOW}⚠️  docker / docker compose nicht verfügbar — ChromaDB nicht gestartet.${NC}"
    echo "   Nachholen sobald docker funktioniert:"
    echo "       cd $PROJECT_DIR/docker && docker compose up -d chromadb"
fi

# ─── Verifikation Schritt 2c: Container läuft + healthy + antwortet ───
# Wenn der Container nicht startbar war (CHROMA_STARTED=0), gehen die
# folgenden Checks alle auf rot — das ist gewollt, weil ChromaDB Pflicht
# ist für Vector Cache (RAG, Memory, Dokumente).
echo ""
echo -e "${BLUE}🔎 Verifiziere ChromaDB-Dienst...${NC}"

# Container-Running-Check (nutzt docker_run für korrekte Gruppen-Membership).
if docker_run "docker ps --filter name=^/aifred-chromadb\$ --filter status=running --format '{{.Names}}' | grep -qx aifred-chromadb"; then
    echo -e "   ${GREEN}✅ verifiziert:${NC} Container 'aifred-chromadb' läuft"
else
    echo -e "   ${RED}❌ FEHLT:${NC} Container 'aifred-chromadb' nicht running"
    echo -e "      ${YELLOW}→ cd docker && docker compose up -d chromadb${NC}"
    STEP_FAILURES+=("ChromaDB-Container nicht running")
fi

# Health-Status: die docker-compose.yml setzt einen TCP-Healthcheck auf Port
# 8000 (interval=30s, retries=3, start_period=10s). Bei einem Frischstart kommt
# noch der Image-Pull dazu (chromadb/chroma:latest ~200 MB) — wir pollen
# großzügig 120s. Solange der Status "starting" ist (Healthcheck noch nicht
# durchgelaufen), zählt das nicht als Failure.
echo "   ⏳ Warte auf Container-Health (max 120s, alle 2s Probe)..."
chroma_healthy=0
chroma_no_healthcheck=0
chroma_last_state=""
for _ in $(seq 1 60); do
    state="$(docker_run "docker inspect --format '{{.State.Health.Status}}' aifred-chromadb 2>/dev/null" 2>/dev/null || echo "")"
    chroma_last_state="$state"
    if [ "$state" = "healthy" ]; then
        chroma_healthy=1
        break
    fi
    if [ -z "$state" ] || [ "$state" = "<no value>" ]; then
        # Image hat keinen Healthcheck — Running als Proxy nehmen
        running="$(docker_run "docker inspect --format '{{.State.Running}}' aifred-chromadb 2>/dev/null" 2>/dev/null || echo "false")"
        if [ "$running" = "true" ]; then
            chroma_no_healthcheck=1
            break
        fi
    fi
    # "starting" oder "unhealthy" → weiter pollen (Frischstart-Toleranz)
    sleep 2
done
if [ "$chroma_healthy" = "1" ]; then
    echo -e "   ${GREEN}✅ verifiziert:${NC} Container-Health = 'healthy'"
elif [ "$chroma_no_healthcheck" = "1" ]; then
    echo -e "   ${GREEN}✅ verifiziert:${NC} Container running (kein Healthcheck im Image)"
elif [ "$chroma_last_state" = "starting" ]; then
    # Healthcheck läuft noch (start_period 10s + interval 30s + retries 3 = max ~100s).
    # Wenn der Port-Heartbeat-Check unten erfolgreich ist, ist das ok — wir
    # melden hier nur eine Warnung statt Blocker.
    echo -e "   ${YELLOW}⚠️  Container-Health noch 'starting' nach 120s — Heartbeat-Probe entscheidet${NC}"
    STEP_WARNINGS+=("ChromaDB-Container-Health nach 120s noch 'starting' — Heartbeat-Probe siehe unten")
else
    echo -e "   ${RED}❌ FEHLT:${NC} Container nicht healthy nach 120s (Status: ${chroma_last_state:-unknown})"
    echo -e "      ${YELLOW}→ docker logs aifred-chromadb${NC}"
    STEP_FAILURES+=("ChromaDB-Container nicht healthy nach 120s")
fi

# HTTP-Heartbeat. Chroma 0.4.x: /api/v1/heartbeat, Chroma 0.5+/0.6+: /api/v2/heartbeat.
# Wir probieren beide; falls keiner antwortet (z.B. zu früh), TCP-Probe als
# letzter Anker (sagt: Port offen, HTTP-Layer fragwürdig).
# 15 x 2s = 30s Probe — beim Frischstart kann die HTTP-Layer kurz nach
# Container-Start noch nicht antworten, auch wenn Health schon healthy ist.
chroma_http_ok=0
for _ in $(seq 1 15); do
    if curl -sf --max-time 5 http://localhost:8000/api/v2/heartbeat -o /dev/null 2>/dev/null \
       || curl -sf --max-time 5 http://localhost:8000/api/v1/heartbeat -o /dev/null 2>/dev/null; then
        chroma_http_ok=1
        break
    fi
    sleep 2
done
if [ "$chroma_http_ok" = "1" ]; then
    echo -e "   ${GREEN}✅ verifiziert:${NC} ChromaDB HTTP-Heartbeat antwortet (Port 8000)"
elif (echo > /dev/tcp/localhost/8000) &>/dev/null; then
    echo -e "   ${YELLOW}⚠️  Port 8000 offen, aber kein Heartbeat — evtl. noch im Warmup${NC}"
    STEP_WARNINGS+=("ChromaDB-Heartbeat antwortet nicht (Port offen, API noch nicht ready?)")
else
    echo -e "   ${RED}❌ FEHLT:${NC} ChromaDB antwortet weder via HTTP noch TCP auf Port 8000"
    echo -e "      ${YELLOW}→ docker logs aifred-chromadb${NC}"
    STEP_FAILURES+=("ChromaDB Port 8000 nicht erreichbar")
fi

step_summary "2c — ChromaDB-Container"
echo ""
sleep 1

# ============================================================
# SCHRITT 2d: Piper TTS (Optional, lokal/offline)
# ============================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Schritt 2d: Piper TTS (Optional)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "Piper ist eine lokale, schnelle Offline-TTS-Engine. Default-Engine ist"
echo "Edge-TTS (Cloud) — Piper ist nur nötig wenn du eine Offline-Stimme willst."
echo "Größe: pip-Paket ~10 MB + Modelle je 20-110 MB pro Stimme."
echo ""
read -p "Piper TTS einrichten? (j/N): " -n 1 -r PIPER_REPLY
echo ""
PIPER_CHOSEN=0
if [[ $PIPER_REPLY =~ ^[JjYy]$ ]]; then
    PIPER_CHOSEN=1
    # piper-tts Pip-Paket installieren (nicht in requirements.txt da optional).
    echo "📥 Installiere piper-tts ins venv..."
    if "$PROJECT_DIR/venv/bin/pip" install piper-tts; then
        echo -e "${GREEN}✅ piper-tts installiert${NC}"
        # Modell-Auswahl über Helper-Script (liest PIPER_VOICES aus config.py).
        if [ -f "$SCRIPT_DIR/install-piper-models.py" ]; then
            "$PROJECT_DIR/venv/bin/python" "$SCRIPT_DIR/install-piper-models.py" || true
        else
            echo -e "${YELLOW}⚠️  $SCRIPT_DIR/install-piper-models.py fehlt — Modell-Download übersprungen.${NC}"
            echo "   Modelle manuell ziehen: https://huggingface.co/rhasspy/piper-voices"
            echo "   Ziel-Verzeichnis: $PROJECT_DIR/piper_models/"
        fi
    else
        echo -e "${RED}❌ piper-tts pip-Install fehlgeschlagen — überspringe Modell-Download.${NC}"
        echo "   Nachholen: source venv/bin/activate && pip install piper-tts"
        echo "              python scripts/install-piper-models.py"
    fi
else
    echo -e "${YELLOW}⏭️  Piper TTS übersprungen.${NC}"
    echo "   Nachholen: source venv/bin/activate && pip install piper-tts"
    echo "              python scripts/install-piper-models.py"
fi

# ─── Verifikation Schritt 2d: nur wenn User Piper gewählt hat ───
# Bei Skip ist Piper bewusst nicht da → kein Fehler, nur Info.
if [ "$PIPER_CHOSEN" = "1" ]; then
    echo ""
    echo -e "${BLUE}🔎 Verifiziere Piper TTS...${NC}"
    verify_step "piper-Binary im venv aufrufbar" \
        "[ -x '$PROJECT_DIR/venv/bin/piper' ] && '$PROJECT_DIR/venv/bin/piper' --help" \
        "source venv/bin/activate && pip install piper-tts"
    # Mindestens ein .onnx-Modell im piper_models/ Verzeichnis muss da sein,
    # sonst kann TTS keine Stimme nutzen. Glob via shopt+nullglob in subshell.
    warn_step "Mindestens ein Piper-Stimmen-Modell in piper_models/" \
        "ls '$PROJECT_DIR/piper_models/'*.onnx 2>/dev/null | head -1 | grep -q ." \
        "venv/bin/python scripts/install-piper-models.py"
    step_summary "2d — Piper TTS"
fi
echo ""
sleep 1

# ============================================================
# SCHRITT 2e: Whisper STT (Pflicht — Voice Input im UI)
# ============================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Schritt 2e: Whisper STT (Voice Input)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "Whisper-STT ist die Spracherkennung für den Voice-Input-Button im UI."
echo "Läuft als eigener Docker-Container (faster-whisper, CPU+GPU dual-device)."
echo "Erststart: Image-Build dauert 5-10 Min + Modell-Pull (~1.5 GB für 'medium')."
echo ""

WHISPER_STARTED=0
if [ -f "$PROJECT_DIR/docker/whisper/docker-compose.yml" ] \
   && command -v docker &>/dev/null \
   && docker compose version &>/dev/null 2>&1; then
    # Idempotenz-Check: läuft der Container schon?
    if docker_run "docker ps --format '{{.Names}}' | grep -qx whisper-stt"; then
        echo -e "${GREEN}✅ Whisper-Container 'whisper-stt' läuft bereits${NC}"
        WHISPER_STARTED=1
    else
        echo "   Baue + starte Whisper-Container (kann beim ersten Mal dauern)..."
        if docker_run "cd '$PROJECT_DIR/docker/whisper' && docker compose up -d --build"; then
            echo -e "${GREEN}✅ Whisper-Container gestartet${NC}"
            WHISPER_STARTED=1
        else
            echo -e "${YELLOW}⚠️  Whisper-Build/Start fehlgeschlagen.${NC}"
            echo "   Manuell nachholen:"
            echo "       cd $PROJECT_DIR/docker/whisper && docker compose up -d --build"
        fi
    fi
else
    echo -e "${YELLOW}⚠️  docker / docker compose nicht verfügbar oder docker/whisper/ fehlt — übersprungen.${NC}"
fi

# Verifikation — Container running + Port 5080 (extern) hört.
echo ""
echo -e "${BLUE}🔎 Verifiziere Whisper STT...${NC}"
if [ "$WHISPER_STARTED" = "1" ]; then
    verify_step "whisper-stt Container running" \
        "docker ps --format '{{.Names}}' | grep -qx whisper-stt" \
        "cd docker/whisper && docker compose up -d --build"
    # Healthcheck im Container: 30s interval + start_period 60s.
    # Wir pollen großzügig, weil Modell-Pull beim Frischstart 1-2 min dauert.
    echo "   ⏳ Warte auf Whisper-Health (max 180s — Erststart lädt 'medium'-Modell)..."
    whisper_ok=0
    for _ in $(seq 1 90); do
        if curl -sf --max-time 5 http://localhost:5080/health -o /dev/null 2>/dev/null; then
            whisper_ok=1
            break
        fi
        sleep 2
    done
    if [ "$whisper_ok" = "1" ]; then
        echo -e "   ${GREEN}✅ verifiziert:${NC} Whisper /health antwortet (Port 5080)"
    else
        echo -e "   ${YELLOW}⚠️  Whisper-/health noch nicht ready nach 180s${NC}"
        echo -e "      ${YELLOW}→ docker logs whisper-stt -f${NC}"
        STEP_WARNINGS+=("Whisper STT braucht länger zum Hochfahren — siehe docker logs whisper-stt")
    fi
else
    STEP_WARNINGS+=("Whisper STT nicht gestartet — Voice-Input im UI funktioniert nicht (cd docker/whisper && docker compose up -d --build)")
fi
step_summary "2e — Whisper STT"
echo ""
sleep 1

# ============================================================
# SCHRITT 2f: SearXNG (Pflicht — datenschutz-freundliche Web-Suche)
# ============================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Schritt 2f: SearXNG (Web-Suche)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "SearXNG ist die lokale, datenschutz-freundliche Meta-Suchmaschine,"
echo "die AIfreds Web-Research nutzt (alternativ/ergänzend zu Brave + Tavily)."
echo "In docker-compose.yml hinterlegt unter 'profiles: [full]' — wir starten"
echo "sie explizit mit --profile full."
echo ""

SEARXNG_STARTED=0
if [ -f "$PROJECT_DIR/docker/docker-compose.yml" ] \
   && command -v docker &>/dev/null \
   && docker compose version &>/dev/null 2>&1; then
    if docker_run "docker ps --format '{{.Names}}' | grep -qx searxng"; then
        echo -e "${GREEN}✅ SearXNG-Container 'searxng' läuft bereits${NC}"
        SEARXNG_STARTED=1
    else
        echo "   Starte SearXNG-Container..."
        if docker_run "cd '$PROJECT_DIR/docker' && docker compose --profile full up -d searxng"; then
            echo -e "${GREEN}✅ SearXNG gestartet${NC}"
            SEARXNG_STARTED=1
        else
            echo -e "${YELLOW}⚠️  SearXNG-Start fehlgeschlagen.${NC}"
            echo "   Manuell nachholen:"
            echo "       cd $PROJECT_DIR/docker && docker compose --profile full up -d searxng"
        fi
    fi
else
    echo -e "${YELLOW}⚠️  docker / docker compose nicht verfügbar — übersprungen.${NC}"
fi

echo ""
echo -e "${BLUE}🔎 Verifiziere SearXNG...${NC}"
if [ "$SEARXNG_STARTED" = "1" ]; then
    verify_step "searxng Container running" \
        "docker ps --format '{{.Names}}' | grep -qx searxng" \
        "cd docker && docker compose --profile full up -d searxng"
    # SearXNG braucht ein paar Sekunden bis HTTP antwortet — Image meist schon
    # gepullt (~50 MB), eigentlicher Boot ~5s. 60s Polling großzügig.
    echo "   ⏳ Warte auf SearXNG (Port 8888, max 60s)..."
    searxng_ok=0
    for _ in $(seq 1 30); do
        if curl -sf --max-time 5 http://localhost:8888/ -o /dev/null 2>/dev/null; then
            searxng_ok=1
            break
        fi
        sleep 2
    done
    if [ "$searxng_ok" = "1" ]; then
        echo -e "   ${GREEN}✅ verifiziert:${NC} SearXNG antwortet auf Port 8888"
    else
        echo -e "   ${YELLOW}⚠️  SearXNG noch nicht ready nach 60s${NC}"
        echo -e "      ${YELLOW}→ docker logs searxng -f${NC}"
        STEP_WARNINGS+=("SearXNG noch nicht ready — Web-Research fällt auf Brave/Tavily zurück")
    fi
else
    STEP_WARNINGS+=("SearXNG nicht gestartet — Web-Research nutzt nur Brave/Tavily (falls API-Keys gesetzt)")
fi
step_summary "2f — SearXNG"
echo ""
sleep 1

# ============================================================
# SCHRITT 2g: Lokale TTS-Container (Optional, On-Demand)
# ============================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Schritt 2g: Lokale TTS-Container (Optional)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "AIfred unterstützt mehrere lokale TTS-Engines, die als Docker-Container"
echo "auf GPU laufen und on-demand von AIfred selbst gestartet werden — beim"
echo "Wechsel im Engine-Dropdown der UI, NICHT beim Boot. Wir bauen hier nur"
echo "das Image; das Container-Lifecycle macht AIfred später selbst."
echo ""
# Engine metadata. Display label + one-line description + an optional
# "recommended" flag, keyed by the compose-subdir name. Adding a new
# engine = drop a directory under docker/tts/<name>/ AND optionally
# add an entry here for a nicer prompt. Missing entries fall back to
# the directory name and a "see docker/tts/<name>/" hint — the engine
# still works, the user just sees a less polished label.
declare -A TTS_ENGINE_LABEL=(
    [qwen3-tts]="Qwen3-TTS"
    [xtts]="XTTS v2 (Coqui)"
    [moss-tts]="MOSS-TTS"
    [fish-speech]="Fish-Speech S2 Pro"
)
declare -A TTS_ENGINE_DESC=(
    [qwen3-tts]="Streaming, 10 Sprachen, Voice-Cloning. Image ~11.8 GB · Modell-Cache ~3.4 GB · braucht GPU (V100 ideal). Schnellstes Streaming-TTS."
    [xtts]="Bestes Voice-Cloning, viele Built-in-Sprecher. Image ~7.7 GB · Modell-Cache ~2 GB · GPU oder CPU. Klangtreuer als Qwen3, höhere Latenz."
    [moss-tts]="Zero-Shot Voice-Cloning, 20 Sprachen. Image ~6.9 GB · Modell-Cache ~3 GB · braucht GPU. Batch-Rendering nach Bubble-Ende."
    [fish-speech]="5B Dual-AR, Voice Cloning, 80+ Sprachen. Image ~14 GB · braucht GPU (≥24 GB VRAM). Streaming, Research-Lizenz."
)
declare -A TTS_ENGINE_RECOMMENDED=(
    [qwen3-tts]=1
)

# Discover engines from the repo: anything under docker/tts/<subdir>/
# with a docker-compose.yml is offerable. New engine = new directory.
TTS_ENGINES_AVAILABLE=()
for _compose in "$PROJECT_DIR"/docker/tts/*/docker-compose.yml; do
    [ -f "$_compose" ] || continue
    TTS_ENGINES_AVAILABLE+=("$(basename "$(dirname "$_compose")")")
done

if [ ${#TTS_ENGINES_AVAILABLE[@]} -eq 0 ]; then
    echo "Keine TTS-Engines unter docker/tts/ gefunden — Schritt überspringen."
    echo ""
else
    echo "Verfügbar sind:"
    echo ""
    for _engine in "${TTS_ENGINES_AVAILABLE[@]}"; do
        _label="${TTS_ENGINE_LABEL[$_engine]:-$_engine}"
        _desc="${TTS_ENGINE_DESC[$_engine]:-Siehe docker/tts/$_engine/}"
        if [ -n "${TTS_ENGINE_RECOMMENDED[$_engine]:-}" ]; then
            echo -e "  ${GREEN}• ${_label}${NC} (empfohlen) — ${_desc}"
        else
            echo -e "  ${GREEN}• ${_label}${NC} — ${_desc}"
        fi
    done
    echo ""
fi
echo "Ohne lokalen TTS-Container nutzt AIfred Edge-TTS (Cloud) oder Piper (offline,"
echo "siehe Schritt 2d). Mit jeder zusätzlichen Engine: mehr Disk + einmaliger"
echo "Build-Aufwand (5-15 min pro Container)."
echo ""

# Image-Name aus dem ersten `image:` der compose-Datei lesen.
# Compose-Files in docker/tts/<engine>/ verwenden eine fixe image:-Zeile
# (z.B. "image: qwen3-tts-1.7b-base"), die wir hier als Source-of-Truth
# nutzen — kein Raten via Heuristik mehr.
tts_compose_image() {
    local compose_file="$1"
    awk '/^[[:space:]]*image:[[:space:]]*/ {gsub(/^[[:space:]]*image:[[:space:]]*/, ""); gsub(/[[:space:]]+$/, ""); print; exit}' "$compose_file"
}

tts_build() {
    local engine="$1"
    local label="$2"
    local dir="$PROJECT_DIR/docker/tts/$engine"
    local compose="$dir/docker-compose.yml"
    if [ ! -f "$compose" ]; then
        echo -e "${YELLOW}⚠️  $compose fehlt — überspringe $label.${NC}"
        STEP_WARNINGS+=("$label übersprungen — docker/tts/$engine fehlt")
        return 1
    fi
    # Idempotent: image-Tag aus compose lesen, exakter Existenz-Check via
    # `docker image inspect` (gibt Exit 0 wenn vorhanden, sonst 1).
    local image_tag
    image_tag="$(tts_compose_image "$compose")"
    if [ -n "$image_tag" ] && docker_run "docker image inspect '$image_tag'" &>/dev/null; then
        echo -e "${GREEN}✅ $label Image '$image_tag' bereits vorhanden — Skip Build${NC}"
        return 0
    fi
    echo "   Baue $label Image (kann beim ersten Mal 5-15 min dauern)..."
    if docker_run "cd '$dir' && docker compose build"; then
        echo -e "${GREEN}✅ $label Image gebaut${NC}"
        return 0
    fi
    echo -e "${YELLOW}⚠️  $label Build fehlgeschlagen.${NC}"
    echo "   Manuell nachholen: cd docker/tts/$engine && docker compose build"
    STEP_WARNINGS+=("$label Build fehlgeschlagen — siehe docker logs / docker compose build Output")
    return 1
}

# Interactive selection. Default: NEIN — jeder Container ist mehrere
# GB, User soll bewusst wählen. Recommended engines bekommen einen
# Hinweis im Prompt, aber kein vorausgewähltes "yes".
TTS_CHOSEN_ENGINES=()
for _engine in "${TTS_ENGINES_AVAILABLE[@]}"; do
    _label="${TTS_ENGINE_LABEL[$_engine]:-$_engine}"
    if [ -n "${TTS_ENGINE_RECOMMENDED[$_engine]:-}" ]; then
        _prompt="$_label bauen (empfohlen)? (j/N): "
    else
        _prompt="$_label bauen? (j/N): "
    fi
    read -p "$_prompt" -n 1 -r REPLY; echo
    if [[ $REPLY =~ ^[JjYy]$ ]]; then
        TTS_CHOSEN_ENGINES+=("$_engine")
        tts_build "$_engine" "$_label" || true
    fi
done

if [ ${#TTS_CHOSEN_ENGINES[@]} -eq 0 ]; then
    echo -e "${YELLOW}⏭️  Keine lokalen TTS-Container gewählt — AIfred nutzt Edge-TTS / Piper / eSpeak.${NC}"
fi

# ─── Verifikation Schritt 2g: gewählte Images sind tatsächlich da ───
# Image-Existenz via exaktem Tag aus der jeweiligen compose-Datei prüfen,
# damit fremde Images mit ähnlichem Namen (z.B. xtts-fork) hier nicht
# als false positive durchgehen.
if [ ${#TTS_CHOSEN_ENGINES[@]} -gt 0 ]; then
    echo ""
    echo -e "${BLUE}🔎 Verifiziere TTS-Container-Images...${NC}"
    for _engine in "${TTS_CHOSEN_ENGINES[@]}"; do
        _label="${TTS_ENGINE_LABEL[$_engine]:-$_engine}"
        _img="$(tts_compose_image "$PROJECT_DIR/docker/tts/$_engine/docker-compose.yml" 2>/dev/null)"
        verify_step "$_label Image '$_img' vorhanden" \
            "docker image inspect '$_img'" \
            "cd docker/tts/$_engine && docker compose build"
    done
    step_summary "2g — Lokale TTS-Container"
fi
echo ""
sleep 1

fi  # end of "if [ "$DRY_RUN" = "1" ] / else" — Steps 1-2g

# ============================================================
# SCHRITT 3: Systemd Services Installation (Optional, MIT sudo)
# ============================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Schritt 3/3: Systemd Services Installation (Optional)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "Systemd-Services für automatischen Start beim Booten."
if [ "$DRY_RUN" = "1" ]; then
    echo -e "${YELLOW}📝 DRY-RUN: kein sudo, keine Disk-Writes — delegiert an install-services.sh --dry-run.${NC}"
    REPLY="j"
else
    echo -e "${YELLOW}⚠️  Benötigt sudo-Rechte!${NC}"
    echo ""
    read -p "Systemd-Services installieren? (j/N): " -n 1 -r
fi
echo ""

SYSTEMD_CHOSEN=0
SYSTEMD_SVC_EXIT=0
if [[ $REPLY =~ ^[JjYy]$ ]]; then
    SYSTEMD_CHOSEN=1
    if [ -f "$SCRIPT_DIR/install-services.sh" ]; then
        # In dry-run we don't need sudo (install-services.sh handles it
        # itself). In a real or --no-overwrite run sudo is required.
        if [ "$DRY_RUN" = "1" ]; then
            echo "   Starte install-services.sh ${SYSTEMD_FLAGS[*]} (kein sudo nötig im dry-run)..."
            set +e
            bash "$SCRIPT_DIR/install-services.sh" "${SYSTEMD_FLAGS[@]}"
            SYSTEMD_SVC_EXIT=$?
            set -e
        else
            echo "   Starte Installation mit sudo ${SYSTEMD_FLAGS[*]}..."
            # install-services.sh hat eigene Verifikation und exitet mit !=0,
            # falls Services nicht laufen. Wir wollen aber die Final-Auswertung
            # in install-all.sh erreichen, also Exit-Code merken statt abbrechen.
            # set +e/-e umgeht das set -e des äusseren Scripts.
            set +e
            sudo bash "$SCRIPT_DIR/install-services.sh" "${SYSTEMD_FLAGS[@]}"
            SYSTEMD_SVC_EXIT=$?
            set -e
        fi
        if [ "$SYSTEMD_SVC_EXIT" -ne 0 ]; then
            STEP_FAILURES+=("install-services.sh exit $SYSTEMD_SVC_EXIT — siehe Output oben")
        fi
    else
        echo -e "${RED}❌ Script nicht gefunden: $SCRIPT_DIR/install-services.sh${NC}"
        echo "   Überspringe Systemd-Installation..."
        SYSTEMD_CHOSEN=0
    fi
else
    echo -e "${YELLOW}⏭️  Systemd-Installation übersprungen${NC}"
    echo ""
    echo "   Du kannst AIfred manuell starten mit:"
    echo "   cd $PROJECT_DIR"
    echo "   source venv/bin/activate"
    echo "   reflex run"
fi

# ─── Verifikation Schritt 3: nur wenn User Systemd gewählt hat ───
# Bei Skip ist es bewusst nicht da → kein Fehler, nur Info.
if [ "$SYSTEMD_CHOSEN" = "1" ]; then
    echo ""
    echo -e "${BLUE}🔎 Verifiziere Systemd-Services...${NC}"
    verify_step "aifred-chromadb.service is-enabled" \
        "systemctl is-enabled aifred-chromadb.service" \
        "sudo systemctl enable aifred-chromadb.service"
    verify_step "aifred-chromadb.service is-active" \
        "systemctl is-active aifred-chromadb.service" \
        "sudo systemctl start aifred-chromadb.service"
    verify_step "aifred-intelligence.service is-enabled" \
        "systemctl is-enabled aifred-intelligence.service" \
        "sudo systemctl enable aifred-intelligence.service"
    verify_step "aifred-intelligence.service is-active" \
        "systemctl is-active aifred-intelligence.service" \
        "sudo systemctl start aifred-intelligence.service"
    # AIfred braucht beim Frischstart ~30-90s bis Backend-Port 8002 hört:
    # Reflex initialisiert beim allerersten Start Bun (lädt Bun-Binary,
    # installiert node_modules) und compiliert das Frontend. Mit warmem
    # .web/-Cache geht es in 10-15s, beim Cold-Start kann es 60s+ dauern.
    # Wir pollen großzügig 120s — der Backend-Port hört, sobald Reflex's
    # Granian gestartet ist, also unabhängig vom Frontend-Build.
    echo "   ⏳ Warte auf AIfred-Backend (Port 8002, max 120s)..."
    aifred_port_ok=0
    for _ in $(seq 1 60); do
        if (echo > /dev/tcp/localhost/8002) &>/dev/null; then
            aifred_port_ok=1
            break
        fi
        sleep 2
    done
    if [ "$aifred_port_ok" = "1" ]; then
        echo -e "   ${GREEN}✅ verifiziert:${NC} AIfred-Backend hört auf Port 8002"
    else
        echo -e "   ${RED}❌ FEHLT:${NC} AIfred-Backend nicht erreichbar auf Port 8002 nach 120s"
        echo -e "      ${YELLOW}→ journalctl -u aifred-intelligence.service -e --no-pager${NC}"
        STEP_FAILURES+=("AIfred-Backend Port 8002 hört nicht")
    fi
    # Frontend-Port 3002 dauert beim allerersten Start noch länger (Bun-Setup,
    # Vite-Build des _index.jsx mit allen eager-geladenen Modals). Beim Erststart
    # 2-5 min realistisch. Hier nur 60s pollen — wenn's nicht klappt, Warnung
    # statt Blocker. Backend reicht für CLI/API-Zugriff.
    echo "   ⏳ Warte auf AIfred-Frontend (Port 3002, max 60s)..."
    frontend_port_ok=0
    for _ in $(seq 1 30); do
        if (echo > /dev/tcp/localhost/3002) &>/dev/null; then
            frontend_port_ok=1
            break
        fi
        sleep 2
    done
    if [ "$frontend_port_ok" = "1" ]; then
        echo -e "   ${GREEN}✅ verifiziert:${NC} AIfred-Frontend hört auf Port 3002"
    else
        echo -e "   ${YELLOW}⚠️  Frontend-Port 3002 noch nicht offen (Erststart-Build kann 2-5 Min dauern)${NC}"
        echo -e "      ${YELLOW}→ journalctl -u aifred-intelligence.service -f${NC}"
        STEP_WARNINGS+=("AIfred-Frontend Port 3002 dauert noch — erster Bun/Vite-Build")
    fi
    step_summary "3 — Systemd-Services"
fi

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Whitelist-User anlegen${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "AIfred braucht mindestens einen Whitelist-User, damit sich jemand"
echo "im Web-UI registrieren kann. Ohne diesen Eintrag rejektet die"
echo "Registrierung jeden Username — die UI lädt, aber niemand kommt rein."
echo ""

# Tracking-Flag für die Schluss-Zusammenfassung (Health-Check liest ihn).
WHITELIST_USER_CREATED=0

if [ "$DRY_RUN" = "1" ]; then
    if [ -f "$PROJECT_DIR/data/allowed_users.json" ]; then
        echo -e "${YELLOW}📝 DRY-RUN: allowed_users.json existiert bereits — würde übersprungen.${NC}"
    else
        echo -e "${YELLOW}📝 DRY-RUN: WOULD prompt for whitelist user (writes data/allowed_users.json).${NC}"
    fi
    echo ""
else

# Solange wiederholen, bis ein User angelegt wurde ODER User explizit
# 'skip' eingibt. So vermeidet man den häufigen Silent-Fail-Fall, dass
# enter-enter zum übersprungenen Schritt führt.
while true; do
    read -p "Username (leer/skip = überspringen mit Warnung): " WHITELIST_USER
    if [ -z "$WHITELIST_USER" ] || [ "$WHITELIST_USER" = "skip" ]; then
        echo -e "${YELLOW}⚠️  Whitelist-User-Anlage übersprungen.${NC}"
        echo -e "${YELLOW}   Login wird in der UI nicht funktionieren, bis du nachholst:${NC}"
        echo "       ./aifred-admin add <username>"
        break
    fi
    if [ -x "$PROJECT_DIR/aifred-admin" ]; then
        if "$PROJECT_DIR/aifred-admin" add "$WHITELIST_USER"; then
            WHITELIST_USER_CREATED=1
            break
        else
            echo -e "${YELLOW}⚠️  aifred-admin add fehlgeschlagen — bitte erneut versuchen oder 'skip' eingeben.${NC}"
        fi
    else
        echo -e "${YELLOW}⚠️  $PROJECT_DIR/aifred-admin nicht ausführbar — überspringe.${NC}"
        break
    fi
done
fi  # end of "if [ "$DRY_RUN" = "1" ] / else" — Whitelist-User-Prompt

# ─── Verifikation Whitelist-User ───
if [ "$WHITELIST_USER_CREATED" = "1" ]; then
    echo ""
    echo -e "${BLUE}🔎 Verifiziere Whitelist-User...${NC}"
    verify_step "allowed_users.json existiert" \
        "[ -f '$PROJECT_DIR/data/allowed_users.json' ]" \
        "./aifred-admin add <username>"
    verify_step "Whitelist-User '$WHITELIST_USER' eingetragen" \
        "'$PROJECT_DIR/aifred-admin' users | grep -qiF '$WHITELIST_USER'" \
        "./aifred-admin add $WHITELIST_USER"
fi

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Gesamt-Auswertung${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "Zusammenfassung aller Step-Verifikationen (Failures aus jedem Schritt)."
echo ""

# Whitelist-User-Skip ist ein Soft-Fail, kein Step-Failure — hier in
# STEP_WARNINGS einsortieren, falls noch nicht passiert.
if [ "${WHITELIST_USER_CREATED:-0}" = "0" ]; then
    STEP_WARNINGS+=("Whitelist-User nicht angelegt — Registrierung in der UI rejekted jeden  (./aifred-admin add <user>)")
fi

if [ ${#STEP_FAILURES[@]} -gt 0 ]; then
    echo -e "${RED}❌ Kritische Probleme aus den Step-Checks — AIfred wird nicht (vollständig) laufen:${NC}"
    for b in "${STEP_FAILURES[@]}"; do
        echo -e "${RED}   • $b${NC}"
    done
    echo ""
fi

if [ ${#STEP_WARNINGS[@]} -gt 0 ]; then
    echo -e "${YELLOW}⚠️  Hinweise aus den Step-Checks — AIfred startet, aber Features fehlen:${NC}"
    for w in "${STEP_WARNINGS[@]}"; do
        echo -e "${YELLOW}   • $w${NC}"
    done
    echo ""
fi

if [ ${#STEP_FAILURES[@]} -eq 0 ] && [ ${#STEP_WARNINGS[@]} -eq 0 ]; then
    echo -e "${GREEN}✅ Alle Step-Verifikationen bestanden.${NC}"
    echo ""
fi

echo "=================================================="
if [ ${#STEP_FAILURES[@]} -eq 0 ]; then
    echo -e "${GREEN}✅ Installation abgeschlossen!${NC}"
else
    echo -e "${RED}❌ Installation mit Fehlern abgeschlossen — bitte oben prüfen.${NC}"
fi
echo "=================================================="
echo ""
echo "📊 Nächste Schritte:"
echo ""
echo "1. AIfred starten:"
if systemctl is-enabled aifred-intelligence.service &>/dev/null; then
    echo "   sudo systemctl restart aifred-intelligence.service"
    echo "   journalctl -u aifred-intelligence.service -f    # Logs"
else
    echo "   cd $PROJECT_DIR"
    echo "   source venv/bin/activate"
    echo "   reflex run"
fi
echo ""
echo "2. Browser öffnen, registrieren mit dem Whitelist-Username:"
echo "   http://localhost:3002"
echo ""
echo "3. Mindestens EIN LLM-Backend einrichten (sonst antwortet kein Agent):"
echo "   • Ollama (einfachster Start, lokal):"
echo "       ollama pull qwen3:8b           # ~5 GB, gutes Allround-Modell"
echo "       ollama pull qwen3:30b-a3b      # ~18 GB, MoE-Modell mit Thinking-Mode"
echo "   • llama.cpp via llama-swap (best performance, siehe docs/en/guides/llamacpp-setup.md)"
echo "   • Cloud-Backends: API-Keys in .env eintragen (DASHSCOPE/DEEPSEEK/ANTHROPIC/MOONSHOT)"
echo ""
if [ "$DOCKER_GROUP_NEEDS_RELOGIN" = "1" ]; then
    echo -e "${YELLOW}ℹ️  Du wurdest neu zur 'docker'-Gruppe hinzugefügt.${NC}"
    echo -e "${YELLOW}   Damit 'docker' in normalen Shells ohne sg funktioniert,${NC}"
    echo -e "${YELLOW}   einmal aus- und wieder einloggen.${NC}"
    echo ""
fi
echo "💡 Optionale Komponenten (nicht Teil der Basis-Installation):"
echo ""
echo "   • llama-swap (LLM-Backend-Proxy für llama.cpp, lebt außerhalb dieses Repos):"
echo "       https://github.com/mostlygeek/llama-swap"
echo "       Binary nach ~/bin, Config in ~/.config/llama-swap/config.yaml,"
echo "       systemd-Unit nach /etc/systemd/system/llama-swap.service (eigene Recherche)."
echo ""
echo "   • Lokale TTS-Container (von AIfred on-demand gestartet — Image-Build hier nachholbar):"
for _engine in "${TTS_ENGINES_AVAILABLE[@]}"; do
    _label="${TTS_ENGINE_LABEL[$_engine]:-$_engine}"
    printf "       cd %s/docker/tts/%s && docker compose build   # %s\n" "$PROJECT_DIR" "$_engine" "$_label"
done
echo ""
echo "   • Reverse-Proxy-Setup (eigene Domain via nginx/caddy):"
echo "       cp scripts/patch-vite-config.sh.example scripts/patch-vite-config.sh"
echo "       # ALLOWED_HOST=\"your-domain.tld\" einsetzen, danach:"
echo "       ./scripts/patch-vite-config.sh    # nach dem ersten 'reflex run'"
echo ""
echo "📚 Dokumentation: $PROJECT_DIR/README.md"
echo ""

# Exit-Code != 0 wenn Step-Failures vorhanden — wichtig für CI/Skript-Caller,
# die das Install-Ergebnis auswerten wollen.
if [ ${#STEP_FAILURES[@]} -gt 0 ]; then
    exit 1
fi
exit 0
