#!/bin/bash
#
# AIfred Intelligence - Complete Installation Script
# Installiert alles: System-Deps, Python-Environment, Systemd-Services (optional)
#

set -e  # Exit on error

echo "=================================================="
echo "  AIfred Intelligence - Vollständige Installation"
echo "=================================================="
echo ""

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

# Warn if running as root — Python venv should not be owned by root
if [ "$EUID" -eq 0 ]; then
    echo -e "${RED}❌ Bitte NICHT mit sudo starten.${NC}"
    echo "   System-Dependencies werden gezielt mit sudo installiert,"
    echo "   alles andere muss als normaler User laufen (venv-Owner)."
    exit 1
fi

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
        apt)    [ -n "$apt_name" ]    && sudo apt update && sudo apt install -y $apt_name ;;
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

# Docker + Compose-Plugin — getrennt prüfen, da Compose v2 ein eigenes Paket ist.
if ! command -v docker &>/dev/null; then
    echo -e "${YELLOW}⚠️  docker fehlt — installiere...${NC}"
    case "$PKG" in
        apt)    sudo apt update && sudo apt install -y docker.io ;;
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
            echo -e "${YELLOW}ℹ️  $USER zur 'docker'-Gruppe hinzugefügt — neu einloggen für Effekt.${NC}"
        fi
    fi
    command -v docker &>/dev/null && echo -e "${GREEN}✅ docker installiert${NC}"
else
    echo -e "${GREEN}✅ docker bereits installiert${NC}"
fi

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

# Ollama-Check + Embedding-Modell ziehen.
echo ""
echo "🦙 Ollama-Backend (LLM + Embeddings)..."
if command -v ollama &>/dev/null; then
    echo -e "${GREEN}✅ ollama installiert ($(ollama --version 2>&1 | head -1))${NC}"
    # Check ob der Ollama-Daemon erreichbar ist (sonst hängt 'ollama list' lange).
    if ! timeout 5 ollama list &>/dev/null; then
        echo -e "${YELLOW}⚠️  Ollama-Daemon nicht erreichbar — bge-m3-Pull übersprungen.${NC}"
        echo "   Daemon starten: ollama serve     (oder via systemd: sudo systemctl start ollama)"
        echo "   Nachholen:      ollama pull bge-m3"
    elif timeout 10 ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qE '^bge-m3(:|$)'; then
        echo -e "${GREEN}✅ Embedding-Modell 'bge-m3' bereits gepulled${NC}"
    else
        echo "   Ziehe Embedding-Modell 'bge-m3' (~1.2 GB, multilingual, 8192 ctx)..."
        if ollama pull bge-m3; then
            echo -e "${GREEN}✅ bge-m3 erfolgreich gezogen${NC}"
        else
            echo -e "${YELLOW}⚠️  bge-m3 konnte nicht gezogen werden — Internet ok?${NC}"
            echo "   Nachholen mit: ollama pull bge-m3"
        fi
    fi
else
    echo -e "${YELLOW}⚠️  ollama nicht gefunden.${NC}"
    echo ""
    echo "   Ollama ist das empfohlene Starter-LLM-Backend (auch Embeddings)."
    echo "   Der offizielle Installer ist ein Pipe-To-Shell:"
    echo "       curl -fsSL https://ollama.com/install.sh | sh"
    echo "   Er erkennt Hardware (CUDA/ROCm/CPU) automatisch, schreibt ein"
    echo "   systemd-Unit, legt den 'ollama'-Systemuser an und startet den"
    echo "   Daemon. Quellcode + Anleitung: https://ollama.com/download/linux"
    echo ""
    read -p "Ollama jetzt installieren (curl | sh)? (j/N): " -n 1 -r OLLAMA_REPLY
    echo ""
    if [[ $OLLAMA_REPLY =~ ^[JjYy]$ ]]; then
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
                    echo "   Ziehe Embedding-Modell 'bge-m3' (~1.2 GB)..."
                    if ollama pull bge-m3; then
                        echo -e "${GREEN}✅ bge-m3 erfolgreich gezogen${NC}"
                    else
                        echo -e "${YELLOW}⚠️  bge-m3-Pull fehlgeschlagen — Internet ok?${NC}"
                        echo "   Nachholen: ollama pull bge-m3"
                    fi
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
        echo "   Später nachholen:"
        echo "       curl -fsSL https://ollama.com/install.sh | sh"
        echo "       ollama pull bge-m3                  # Embeddings (Pflicht für Vector Cache)"
        echo "       ollama pull qwen3:8b                # Beispiel-LLM (siehe README)"
    fi
fi
echo ""
sleep 1

# ============================================================
# SCHRITT 3: Systemd Services Installation (Optional, MIT sudo)
# ============================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Schritt 3/3: Systemd Services Installation (Optional)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "Systemd-Services für automatischen Start beim Booten."
echo -e "${YELLOW}⚠️  Benötigt sudo-Rechte!${NC}"
echo ""
read -p "Systemd-Services installieren? (j/N): " -n 1 -r
echo ""

if [[ $REPLY =~ ^[JjYy]$ ]]; then
    if [ -f "$SCRIPT_DIR/install-services.sh" ]; then
        echo "   Starte Installation mit sudo..."
        sudo bash "$SCRIPT_DIR/install-services.sh"
    else
        echo -e "${RED}❌ Script nicht gefunden: $SCRIPT_DIR/install-services.sh${NC}"
        echo "   Überspringe Systemd-Installation..."
    fi
else
    echo -e "${YELLOW}⏭️  Systemd-Installation übersprungen${NC}"
    echo ""
    echo "   Du kannst AIfred manuell starten mit:"
    echo "   cd $PROJECT_DIR"
    echo "   source venv/bin/activate"
    echo "   reflex run"
fi

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Whitelist-User anlegen${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "AIfred braucht mindestens einen Whitelist-User, damit sich jemand"
echo "im Web-UI registrieren kann."
echo ""
read -p "Jetzt einen Whitelist-User anlegen? (J/n): " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Nn]$ ]]; then
    read -p "Username: " WHITELIST_USER
    if [ -n "$WHITELIST_USER" ]; then
        if [ -x "$PROJECT_DIR/aifred-admin" ]; then
            "$PROJECT_DIR/aifred-admin" add "$WHITELIST_USER" || true
        else
            echo -e "${YELLOW}⚠️  $PROJECT_DIR/aifred-admin nicht ausführbar — überspringe.${NC}"
        fi
    else
        echo -e "${YELLOW}⏭️  Kein Username eingegeben — überspringe.${NC}"
    fi
fi

echo ""
echo "=================================================="
echo -e "${GREEN}✅ Installation abgeschlossen!${NC}"
echo "=================================================="
echo ""
echo "📊 Nächste Schritte:"
echo ""
if ! command -v ollama &>/dev/null; then
    echo "❗ Ollama installieren (LLM-Backend, siehe oben), dann:"
    echo "      ollama pull bge-m3                  # Embeddings"
    echo ""
fi
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
echo "📚 Dokumentation: $PROJECT_DIR/README.md"
echo ""
