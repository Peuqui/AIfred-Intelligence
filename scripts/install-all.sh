#!/bin/bash
#
# AIfred Intelligence - Complete Installation Script
# Installs everything: system deps, Python environment, systemd services (optional)
#
# Modes:
#   ./scripts/install-all.sh                   normal fresh-install / update
#   ./scripts/install-all.sh --dry-run         no disk writes, no apt/pip/
#                                              systemctl side-effects. Shows
#                                              what each step WOULD do.
#                                              Service diffs delegated to
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
    echo "  AIfred Intelligence - Full installation"
    echo "=================================================="
    echo ""
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "📂 Project directory: $PROJECT_DIR"
echo ""

# ============================================================
# Global step verification infrastructure
# ============================================================
# Every step verifies itself after completion. Errors don't abort
# immediately (set -e is bypassed in verify_step()), they're collected
# in a global list — at the end there's a summary, and the script
# exits with code != 0 if blockers remain.
STEP_FAILURES=()      # red blockers per step
STEP_WARNINGS=()      # yellow hints per step

# verify_step <human-name> <test-cmd> [optional-fix-hint]
# Runs <test-cmd> in a subshell. Success → green ✅, otherwise
# red ❌ + hint, +1 STEP_FAILURES.
#
# IMPORTANT: this function ALWAYS returns exit 0. It only collects
# failures in the global array — the final summary at the end of the
# script decides the exit code. If verify_step returned 1 on failure,
# `set -e` would abort the script on the first failed check and skip
# all subsequent checks + the final summary.
verify_step() {
    local name="$1"
    local cmd="$2"
    local hint="${3:-}"
    if bash -c "$cmd" &>/dev/null; then
        echo -e "   ${GREEN}✅ verified:${NC} $name"
        return 0
    fi
    echo -e "   ${RED}❌ MISSING:${NC} $name"
    [ -n "$hint" ] && echo -e "      ${YELLOW}→ $hint${NC}"
    STEP_FAILURES+=("$name${hint:+  ($hint)}")
    return 0
}

# warn_step <human-name> <test-cmd> [hint]
# Like verify_step, but a yellow warning instead of red blocker
# (feature gap rather than broken install). Also ALWAYS returns
# exit 0 (see above).
warn_step() {
    local name="$1"
    local cmd="$2"
    local hint="${3:-}"
    if bash -c "$cmd" &>/dev/null; then
        echo -e "   ${GREEN}✅ verified:${NC} $name"
        return 0
    fi
    echo -e "   ${YELLOW}⚠️  missing:${NC} $name"
    [ -n "$hint" ] && echo -e "      ${YELLOW}→ $hint${NC}"
    STEP_WARNINGS+=("$name${hint:+  ($hint)}")
    return 0
}

# step_summary <step-name>
# Shows a mini summary after each step. The hit list is shown again
# in the final summary — this is just direct per-step feedback.
step_summary() {
    local step="$1"
    echo ""
    echo -e "   ${BLUE}┄ Verification of step '$step' done${NC}"
}

# Warn if running as root — Python venv should not be owned by root
if [ "$EUID" -eq 0 ]; then
    echo -e "${RED}❌ Do NOT run with sudo.${NC}"
    echo "   System dependencies are installed with sudo specifically;"
    echo "   everything else must run as a normal user (venv owner)."
    exit 1
fi

# ============================================================
# Dry-run: skip steps 1 + 2 + 2b..2g (system deps, venv, embedding
# model, container builds). All of these are idempotent on a real run —
# already-installed packages skip themselves, the venv is reused if
# already present, ChromaDB/Whisper containers are no-ops if already
# up — so dry-running them gives no new information. The interesting
# dry-run is what install-services.sh would do to /etc/systemd/system,
# which is delegated below in Step 3.
# ============================================================
if [ "$DRY_RUN" = "1" ]; then
    echo -e "${YELLOW}📝 DRY-RUN: skipping Steps 1-2g (system deps, Python env,${NC}"
    echo -e "${YELLOW}              ChromaDB, Whisper, SearXNG, TTS containers).${NC}"
    echo "   These steps are idempotent on a real run. To see actual"
    echo "   install effects, re-run without --dry-run."
    echo ""
else

# ============================================================
# STEP 1: System dependencies (with sudo)
# ============================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Step 1/3: System dependencies${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "Required:"
echo "  • python3 (>=3.10), python3-venv, python3-pip — Python runtime + venv"
echo "  • poppler-utils — pdftotext for clean PDF indexing"
echo "  • ffmpeg        — audio concat for TTS (XTTS, Edge-TTS multi-chunk)"
echo "  • bubblewrap    — sandbox for the 'execute_python' tool"
echo "  • docker + docker-compose-plugin — ChromaDB vector cache + Whisper STT"
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

# Aggregate one apt update up front so we don't run `sudo apt update`
# separately for each missing package (was: 8 packages missing → 8×
# apt update). We check all required packages once, and if ANY is
# missing we do ONE apt update. Saves 30-60s on a fresh start.
APT_UPDATED=0
apt_ensure_update() {
    if [ "$PKG" = "apt" ] && [ "$APT_UPDATED" = "0" ]; then
        echo "🔄 apt update (once)..."
        sudo apt update
        APT_UPDATED=1
    fi
}

# If any required package is missing → run apt update up front.
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
        echo -e "${GREEN}✅ $human already installed${NC}"
        return 0
    fi

    echo -e "${YELLOW}⚠️  $human missing — installing...${NC}"
    case "$PKG" in
        apt)    apt_ensure_update; [ -n "$apt_name" ] && sudo apt install -y $apt_name ;;
        dnf)    [ -n "$dnf_name" ]    && sudo dnf install -y $dnf_name ;;
        pacman) [ -n "$pacman_name" ] && sudo pacman -S --noconfirm $pacman_name ;;
        brew)   [ -n "$brew_name" ]   && brew install $brew_name ;;
        *)
            echo -e "${RED}❌ No known package manager — please install $human manually.${NC}"
            return 1
            ;;
    esac

    if eval "$check_cmd" &>/dev/null; then
        echo -e "${GREEN}✅ $human installed${NC}"
    else
        echo -e "${RED}❌ Failed to install $human${NC}"
        return 1
    fi
}

# Required packages (failure aborts)
install_one "Python 3" "command -v python3" \
    apt:python3 dnf:python3 pacman:python brew:python@3.12
install_one "python3-venv (PEP 405 venv module)" "python3 -c 'import venv'" \
    apt:python3-venv dnf:python3 pacman:python brew:python@3.12
install_one "python3-pip" "command -v pip3 || python3 -m pip --version" \
    apt:python3-pip dnf:python3-pip pacman:python-pip brew:python@3.12
install_one "pdftotext (poppler-utils)" "command -v pdftotext" \
    apt:poppler-utils dnf:poppler-utils pacman:poppler brew:poppler
install_one "ffmpeg" "command -v ffmpeg" \
    apt:ffmpeg dnf:ffmpeg pacman:ffmpeg brew:ffmpeg
install_one "bubblewrap (bwrap)" "command -v bwrap" \
    apt:bubblewrap dnf:bubblewrap pacman:bubblewrap brew:bubblewrap
# curl is needed in a moment for the Ollama installer ('curl | sh').
# Minimal images (Debian slim, some container hosts) don't ship curl
# out of the box.
install_one "curl (Ollama installer fetches install.sh via curl)" "command -v curl" \
    apt:curl dnf:curl pacman:curl brew:curl
# ca-certificates: without an up-to-date cert bundle, TLS connects to
# huggingface.co/ollama.com fail on some minimal images. Only needed
# on apt (dnf/pacman/brew ship one with their TLS tools).
if [ "$PKG" = "apt" ]; then
    install_one "ca-certificates (TLS truststore for HF/Ollama)" \
        "[ -f /etc/ssl/certs/ca-certificates.crt ]" \
        apt:ca-certificates dnf:ca-certificates pacman:ca-certificates brew:openssl
fi

# Docker + compose plugin — check separately, since compose v2 is its
# own package. Track whether we just freshly added the user to the
# docker group — that won't take effect in the current shell yet, so
# we need 'sg docker' so the later 'docker compose up -d chromadb'
# doesn't fail with a permission error.
DOCKER_GROUP_NEEDS_RELOGIN=0
if ! command -v docker &>/dev/null; then
    echo -e "${YELLOW}⚠️  docker missing — installing...${NC}"
    case "$PKG" in
        apt)    apt_ensure_update; sudo apt install -y docker.io ;;
        dnf)    sudo dnf install -y docker ;;
        pacman) sudo pacman -S --noconfirm docker ;;
        brew)   brew install --cask docker ;;
        *)      echo -e "${RED}❌ No known package manager — install docker manually: https://docs.docker.com/engine/install/${NC}"; exit 1 ;;
    esac

    # Start + enable service, add user to docker group (Linux)
    if command -v systemctl &>/dev/null; then
        sudo systemctl enable --now docker || true
    fi
    if [ "$(uname)" = "Linux" ] && getent group docker &>/dev/null; then
        if ! id -nG "$USER" | tr ' ' '\n' | grep -qx docker; then
            sudo usermod -aG docker "$USER"
            DOCKER_GROUP_NEEDS_RELOGIN=1
            echo -e "${YELLOW}ℹ️  Added $USER to 'docker' group — log out and back in for it to take effect.${NC}"
        fi
    fi
    command -v docker &>/dev/null && echo -e "${GREEN}✅ docker installed${NC}"
else
    echo -e "${GREEN}✅ docker already installed${NC}"
    # Check group membership even if docker is already there.
    if [ "$(uname)" = "Linux" ] && getent group docker &>/dev/null; then
        if ! id -nG "$USER" | tr ' ' '\n' | grep -qx docker; then
            echo -e "${YELLOW}ℹ️  $USER not in 'docker' group — adding...${NC}"
            sudo usermod -aG docker "$USER"
            DOCKER_GROUP_NEEDS_RELOGIN=1
        fi
    fi
fi

# Helper: run a docker command even if the group membership isn't
# active in the current shell yet. 'sg docker -c "<cmd>"' starts a
# subshell with the fresh group membership.
docker_run() {
    if [ "$DOCKER_GROUP_NEEDS_RELOGIN" = "1" ] && command -v sg &>/dev/null; then
        sg docker -c "$*"
    else
        bash -c "$*"
    fi
}

if ! docker compose version &>/dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  docker compose plugin missing — installing...${NC}"
    case "$PKG" in
        apt)    sudo apt install -y docker-compose-plugin ;;
        dnf)    sudo dnf install -y docker-compose-plugin ;;
        pacman) sudo pacman -S --noconfirm docker-compose ;;
        brew)   brew install docker-compose ;;
        *)      echo -e "${RED}❌ Install docker compose plugin manually.${NC}"; exit 1 ;;
    esac
    docker compose version &>/dev/null && echo -e "${GREEN}✅ docker compose installed${NC}"
else
    echo -e "${GREEN}✅ docker compose already installed${NC}"
fi

# ─── Verification of step 1: each tool is callable + responds to --version ───
echo ""
echo -e "${BLUE}🔎 Verifying system dependencies...${NC}"
verify_step "python3 (>=3.10) callable" \
    "python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'" \
    "sudo apt install python3 (or dnf/pacman/brew)"
verify_step "python3-venv module importable" \
    "python3 -c 'import venv'" \
    "sudo apt install python3-venv"
verify_step "pip3 callable" \
    "python3 -m pip --version" \
    "sudo apt install python3-pip"
verify_step "pdftotext (poppler) callable" \
    "pdftotext -v" \
    "sudo apt install poppler-utils"
verify_step "ffmpeg callable" \
    "ffmpeg -version" \
    "sudo apt install ffmpeg"
verify_step "bwrap (bubblewrap) callable" \
    "bwrap --version" \
    "sudo apt install bubblewrap"
verify_step "docker client callable" \
    "docker --version" \
    "sudo apt install docker.io"
verify_step "docker compose plugin callable" \
    "docker compose version" \
    "sudo apt install docker-compose-plugin"
# Daemon reachability. If DOCKER_GROUP_NEEDS_RELOGIN, wrap via sg.
verify_step "docker daemon reachable (server responds)" \
    "$([ "$DOCKER_GROUP_NEEDS_RELOGIN" = "1" ] && echo "sg docker -c 'docker info'" || echo "docker info")" \
    "sudo systemctl start docker  (or log out/in if group is fresh)"

step_summary "1 — System dependencies"
echo ""
sleep 1

# ============================================================
# STEP 2: Python Environment Setup (NO sudo)
# ============================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Step 2/3: Python environment setup${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

if [ -f "$SCRIPT_DIR/setup-python.sh" ]; then
    bash "$SCRIPT_DIR/setup-python.sh"
else
    echo -e "${RED}❌ Script not found: $SCRIPT_DIR/setup-python.sh${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}✅ Python environment ready${NC}"
echo ""

# Playwright Chromium system libraries (required).
# setup-python.sh fetches the browser binary (user context). The
# system libraries (libnss3, libxkbcommon0, libasound2t64, …) need
# root → we install them here with sudo. Without them headless
# Chromium fails to start and web research over JS pages stays
# silent ("browser closed" in the logs).
#
# `playwright install-deps` detects the package manager itself
# (apt/dnf) and knows the library list per Playwright version. On
# brew/pacman/etc. Playwright just prints a hint — no failure path
# needed.
if [ -x "$PROJECT_DIR/venv/bin/playwright" ]; then
    echo -e "${BLUE}🌐 Installing Playwright Chromium system libraries (sudo)...${NC}"
    case "$PKG" in
        apt|dnf)
            if sudo "$PROJECT_DIR/venv/bin/playwright" install-deps chromium; then
                echo -e "${GREEN}✅ Playwright system libraries installed${NC}"
            else
                echo -e "${YELLOW}⚠️  playwright install-deps failed — web research with JS pages may be broken.${NC}"
                echo "   Catch up with: sudo $PROJECT_DIR/venv/bin/playwright install-deps chromium"
            fi
            ;;
        *)
            echo -e "${YELLOW}ℹ️  install-deps is only natively supported by Playwright on apt/dnf.${NC}"
            echo "   On macOS/Arch the required libs are usually already present."
            echo "   On 'browser closed' errors, consult the Playwright docs:"
            echo "   https://playwright.dev/docs/browsers#install-system-dependencies"
            ;;
    esac
    echo ""
fi

# ─── Verification of step 2: venv exists + core packages importable ───
echo -e "${BLUE}🔎 Verifying Python environment...${NC}"
verify_step "venv Python: $PROJECT_DIR/venv/bin/python" \
    "[ -x '$PROJECT_DIR/venv/bin/python' ]" \
    "Re-run scripts/setup-python.sh — venv creation failed"
verify_step "venv pip callable" \
    "'$PROJECT_DIR/venv/bin/python' -m pip --version" \
    "ensurepip problem — sudo apt install python3-venv python3-pip"
verify_step "import reflex" \
    "'$PROJECT_DIR/venv/bin/python' -c 'import reflex'" \
    "pip install -r requirements.txt in venv"
verify_step "import chromadb (vector cache client)" \
    "'$PROJECT_DIR/venv/bin/python' -c 'import chromadb'" \
    "pip install -r requirements.txt in venv"
verify_step "import ollama (embedding client)" \
    "'$PROJECT_DIR/venv/bin/python' -c 'import ollama'" \
    "pip install -r requirements.txt in venv"
verify_step "import fastapi (REST API)" \
    "'$PROJECT_DIR/venv/bin/python' -c 'import fastapi'" \
    "pip install -r requirements.txt in venv"
verify_step "import httpx (HTTP client)" \
    "'$PROJECT_DIR/venv/bin/python' -c 'import httpx'" \
    "pip install -r requirements.txt in venv"
# Vision stack: cv2 = motion detection + frame grab + pHash;
# insightface + onnxruntime-gpu = face recognition for vigilantia.
# cv2 + numpy are in requirements.txt, so hard verify_step. If
# missing, the vigilantia plugin start fails with ImportError.
verify_step "import cv2 (vision pipeline: motion, frames, pHash)" \
    "'$PROJECT_DIR/venv/bin/python' -c 'import cv2'" \
    "pip install -r requirements.txt in venv  (opencv-python-headless)"
verify_step "import numpy (cv2 + face embeddings)" \
    "'$PROJECT_DIR/venv/bin/python' -c 'import numpy'" \
    "pip install -r requirements.txt in venv"
# insightface + onnxruntime-gpu are only a soft requirement (for
# vigilantia face recognition). If the user doesn't use Vision /
# Vigilantia, an import failure isn't a blocker — hence warn_step.
warn_step "import insightface (vigilantia face recognition, optional)" \
    "'$PROJECT_DIR/venv/bin/python' -c 'import insightface'" \
    "pip install insightface onnxruntime-gpu  (only if vigilantia is used)"
warn_step "import onnxruntime (insightface inference backend, optional)" \
    "'$PROJECT_DIR/venv/bin/python' -c 'import onnxruntime'" \
    "pip install onnxruntime-gpu  (CUDA) or onnxruntime  (CPU-only)"
# Webcam access via V4L2 requires the user account to be in the
# 'video' group. Otherwise opencv can't open /dev/video* and
# vigilantia source discovery returns "no devices" despite a
# connected camera. warn_step (no blocker — a server without a
# webcam is legitimate).
warn_step "User in 'video' group (webcam access for vigilantia)" \
    "groups | grep -qw video" \
    "sudo usermod -aG video $USER  → then log out + back in (or 'newgrp video')"
# Playwright binary + Chromium browser are required (web research
# with JS pages needs both). setup-python.sh fetches the browser
# binary, the install-deps block above pulls in the system libs.
# If both fail, web research with JS pages doesn't work — so hard
# verify_step instead of warn_step.
verify_step "playwright binary in venv" \
    "[ -x '$PROJECT_DIR/venv/bin/playwright' ]" \
    "pip install playwright in venv"
verify_step "Playwright Chromium browser installed" \
    "ls $HOME/.cache/ms-playwright/chromium-*/chrome-linux/chrome 2>/dev/null | head -1 | grep -q ." \
    "venv/bin/playwright install chromium  (browser binary)"
# Chromium launchable — if system libs are missing (libnss3,
# libxkbcommon0, libasound2t64 etc.), the browser launch throws a
# 'browser closed unexpectedly' error. We do a real headless launch
# test that catches exactly this class of bugs.
verify_step "Playwright Chromium launchable (system libs complete)" \
    "'$PROJECT_DIR/venv/bin/python' -c 'from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); b.close(); p.stop()'" \
    "sudo venv/bin/playwright install-deps chromium"
# Reflex patch idempotently checked — patch-reflex.py --check returns 0 if ok.
verify_step "Reflex frontend_path patch applied (or fixed upstream)" \
    "'$PROJECT_DIR/venv/bin/python' '$SCRIPT_DIR/patch-reflex.py' --check" \
    "python scripts/patch-reflex.py — re-run"

step_summary "2 — Python environment"
echo ""
sleep 1

# ============================================================
# STEP 2b: Project initialization (.env, directories, embedding model)
# ============================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Step 2b: Project initialization${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Create .env from .env.example — only if no existing one.
if [ ! -f "$PROJECT_DIR/.env" ]; then
    if [ -f "$PROJECT_DIR/.env.example" ]; then
        cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
        chmod 600 "$PROJECT_DIR/.env"
        echo -e "${GREEN}✅ .env created from .env.example${NC}"
        echo -e "${YELLOW}   ℹ️  Don't forget to fill in API keys (BRAVE_API_KEY, TAVILY_API_KEY, …).${NC}"
    else
        echo -e "${YELLOW}⚠️  .env.example missing — skipping .env creation.${NC}"
    fi
else
    echo -e "${GREEN}✅ .env already exists${NC}"
fi

# Create the ChromaDB volume directory up front so docker doesn't
# create it as root (otherwise the user can't do backups/maintenance
# on the host filesystem later).
CHROMA_DIR="$PROJECT_DIR/aifred_vector_cache"
if [ ! -d "$CHROMA_DIR" ]; then
    mkdir -p "$CHROMA_DIR"
    echo -e "${GREEN}✅ ChromaDB volume directory created: $CHROMA_DIR${NC}"
else
    echo -e "${GREEN}✅ ChromaDB volume directory exists${NC}"
fi

# Proactively create required data/ subdirs. The code creates them
# lazily on first access — but if data/ doesn't exist yet and e.g.
# a systemd service running as a different user accesses it, the
# permissions clash. Better to set them up once cleanly.
for sub in sessions images tts_audio html_preview logs documents audio \
           media sandbox_output scheduler security message_hub; do
    mkdir -p "$PROJECT_DIR/data/$sub"
done
echo -e "${GREEN}✅ data/ subdirectories created${NC}"

# Ollama check + embedding model pull.
# Ollama is not "nice to have" — the vector cache (ChromaDB) needs
# 'bge-m3' as embedding model. Without it the UI starts up, but
# every vector search (RAG, documents, memory) fails with a
# connection error against http://localhost:11434. We flag this
# clearly as strongly recommended (not as 'just one LLM backend
# among alternatives').
ollama_pull_bge_m3() {
    if timeout 10 ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qE '^bge-m3(:|$)'; then
        echo -e "${GREEN}✅ Embedding model 'bge-m3' already pulled${NC}"
        return 0
    fi
    echo "   Pulling embedding model 'bge-m3' (~1.2 GB, multilingual, 8192 ctx)..."
    if ollama pull bge-m3; then
        echo -e "${GREEN}✅ bge-m3 pulled successfully${NC}"
    else
        echo -e "${YELLOW}⚠️  Could not pull bge-m3 — internet ok?${NC}"
        echo "   Catch up with: ollama pull bge-m3"
    fi
}

echo ""
echo "🦙 Ollama (embedding provider + starter LLM backend)..."
echo "   Note: Ollama provides 'bge-m3' embeddings for the ChromaDB"
echo "   vector cache. Without bge-m3 every vector search fails"
echo "   (RAG, document index, memory). De-facto required."
echo ""
if command -v ollama &>/dev/null; then
    echo -e "${GREEN}✅ ollama installed ($(ollama --version 2>&1 | head -1))${NC}"
    # Check if the Ollama daemon is reachable (otherwise 'ollama list' hangs).
    if ! timeout 5 ollama list &>/dev/null; then
        echo -e "${YELLOW}⚠️  Ollama daemon not reachable — bge-m3 pull skipped.${NC}"
        echo "   Start the daemon: ollama serve     (or via systemd: sudo systemctl start ollama)"
        echo "   Catch up:         ollama pull bge-m3"
    else
        ollama_pull_bge_m3
    fi
else
    echo -e "${YELLOW}⚠️  ollama not found — STRONGLY recommended to install.${NC}"
    echo ""
    echo "   The official installer is a pipe-to-shell:"
    echo "       curl -fsSL https://ollama.com/install.sh | sh"
    echo "   It detects the hardware (CUDA/ROCm/CPU) automatically, writes a"
    echo "   systemd unit, creates the 'ollama' system user and starts the"
    echo "   daemon. Source + instructions: https://ollama.com/download/linux"
    echo ""
    # Default YES — those who really don't want embeddings can skip.
    read -p "Install Ollama now (curl | sh)? (Y/n): " -n 1 -r OLLAMA_REPLY
    echo ""
    if [[ ! $OLLAMA_REPLY =~ ^[Nn]$ ]]; then
        if ! command -v curl &>/dev/null; then
            echo -e "${RED}❌ curl missing — please run 'sudo apt install curl' first (or dnf/pacman/brew).${NC}"
        else
            echo "   Downloading and running the Ollama installer..."
            if curl -fsSL https://ollama.com/install.sh | sh; then
                echo -e "${GREEN}✅ Ollama installer done${NC}"
                # Give the daemon a moment to come up via systemd.
                for _ in 1 2 3 4 5 6 7 8 9 10; do
                    timeout 2 ollama list &>/dev/null && break
                    sleep 1
                done

                if timeout 5 ollama list &>/dev/null; then
                    ollama_pull_bge_m3
                else
                    echo -e "${YELLOW}⚠️  Ollama daemon not yet responding.${NC}"
                    echo "   After reboot/login catch up with:  ollama pull bge-m3"
                fi
            else
                echo -e "${RED}❌ Ollama installation failed.${NC}"
                echo "   Catch up manually: curl -fsSL https://ollama.com/install.sh | sh"
            fi
        fi
    else
        echo -e "${YELLOW}⏭️  Ollama installation skipped.${NC}"
        echo -e "${YELLOW}   ⚠️  CAUTION: Vector cache (RAG, documents, memory) will NOT work without bge-m3.${NC}"
        echo "   Catch up later:"
        echo "       curl -fsSL https://ollama.com/install.sh | sh"
        echo "       ollama pull bge-m3                  # embeddings (required for vector cache)"
        echo "       ollama pull qwen3:8b                # example LLM (see README)"
    fi
fi
echo ""

# ─── Verification of step 2b: .env, data subdirs, Ollama + bge-m3 ───
echo -e "${BLUE}🔎 Verifying project initialization...${NC}"
verify_step ".env exists" \
    "[ -f '$PROJECT_DIR/.env' ]" \
    "cp .env.example .env"
verify_step "aifred_vector_cache/ volume directory exists" \
    "[ -d '$PROJECT_DIR/aifred_vector_cache' ]" \
    "mkdir -p aifred_vector_cache"
# Check each data subdir individually — the code creates them lazily,
# but if e.g. a systemd service writes as a different user, there are
# permission issues.
for sub in sessions images tts_audio html_preview logs documents audio \
           media sandbox_output scheduler security message_hub; do
    verify_step "data/$sub exists" \
        "[ -d '$PROJECT_DIR/data/$sub' ]" \
        "mkdir -p '$PROJECT_DIR/data/$sub'"
done
# Ollama setup — warning (yellow), AIfred starts without it too, but
# vector cache fails.
if command -v ollama &>/dev/null; then
    warn_step "Ollama daemon responds" \
        "timeout 5 ollama list" \
        "ollama serve  (or sudo systemctl start ollama)"
    warn_step "Embedding model 'bge-m3' available" \
        "timeout 10 ollama list | awk 'NR>1{print \$1}' | grep -qE '^bge-m3(:|\$)'" \
        "ollama pull bge-m3"
else
    warn_step "ollama installed" "command -v ollama" \
        "curl -fsSL https://ollama.com/install.sh | sh"
fi

step_summary "2b — Project initialization"
echo ""

# ============================================================
# STEP 2c: Start ChromaDB container (vector cache)
# ============================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Step 2c: Start ChromaDB container${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "ChromaDB is required for the vector cache (RAG, documents, memory)."
echo "Started independently of the systemd setup via docker compose"
echo "so AIfred is immediately runnable even without the systemd path."
echo ""

CHROMA_STARTED=0
if command -v docker &>/dev/null && docker compose version &>/dev/null 2>&1; then
    # Check if the container is already running (idempotent — re-runs
    # should not disturb).
    if docker_run "docker ps --format '{{.Names}}' 2>/dev/null | grep -qx aifred-chromadb"; then
        echo -e "${GREEN}✅ ChromaDB container 'aifred-chromadb' already running${NC}"
        CHROMA_STARTED=1
    else
        echo "   Starting ChromaDB container..."
        if docker_run "cd '$PROJECT_DIR/docker' && docker compose up -d chromadb"; then
            echo -e "${GREEN}✅ docker compose up -d chromadb (exit 0)${NC}"
            CHROMA_STARTED=1
        else
            echo -e "${YELLOW}⚠️  'docker compose up -d chromadb' failed.${NC}"
            echo "   Possible causes:"
            echo "     • docker daemon not running (sudo systemctl start docker)"
            echo "     • user not yet active in docker group (log out + in)"
            echo "   Catch up manually:"
            echo "       cd $PROJECT_DIR/docker && docker compose up -d chromadb"
        fi
    fi
else
    echo -e "${YELLOW}⚠️  docker / docker compose not available — ChromaDB not started.${NC}"
    echo "   Catch up once docker works:"
    echo "       cd $PROJECT_DIR/docker && docker compose up -d chromadb"
fi

# ─── Verification of step 2c: container running + healthy + responding ───
# If the container couldn't start (CHROMA_STARTED=0), the following
# checks all go red — that's intentional, because ChromaDB is required
# for the vector cache (RAG, memory, documents).
echo ""
echo -e "${BLUE}🔎 Verifying ChromaDB service...${NC}"

# Container-running check (uses docker_run for correct group membership).
if docker_run "docker ps --filter name=^/aifred-chromadb\$ --filter status=running --format '{{.Names}}' | grep -qx aifred-chromadb"; then
    echo -e "   ${GREEN}✅ verified:${NC} Container 'aifred-chromadb' running"
else
    echo -e "   ${RED}❌ MISSING:${NC} Container 'aifred-chromadb' not running"
    echo -e "      ${YELLOW}→ cd docker && docker compose up -d chromadb${NC}"
    STEP_FAILURES+=("ChromaDB container not running")
fi

# Health status: docker-compose.yml sets a TCP health check on port
# 8000 (interval=30s, retries=3, start_period=10s). On a fresh start
# the image pull (chromadb/chroma:latest ~200 MB) is added on top, so
# we poll generously for 120s. As long as the status is "starting"
# (health check not yet through), that doesn't count as a failure.
echo "   ⏳ Waiting for container health (max 120s, probing every 2s)..."
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
        # Image has no healthcheck — use running as a proxy.
        running="$(docker_run "docker inspect --format '{{.State.Running}}' aifred-chromadb 2>/dev/null" 2>/dev/null || echo "false")"
        if [ "$running" = "true" ]; then
            chroma_no_healthcheck=1
            break
        fi
    fi
    # "starting" or "unhealthy" → keep polling (fresh-start tolerance)
    sleep 2
done
if [ "$chroma_healthy" = "1" ]; then
    echo -e "   ${GREEN}✅ verified:${NC} Container health = 'healthy'"
elif [ "$chroma_no_healthcheck" = "1" ]; then
    echo -e "   ${GREEN}✅ verified:${NC} Container running (no healthcheck in image)"
elif [ "$chroma_last_state" = "starting" ]; then
    # Healthcheck still running (start_period 10s + interval 30s +
    # retries 3 = max ~100s). If the port heartbeat below succeeds
    # that's ok — we only emit a warning here, not a blocker.
    echo -e "   ${YELLOW}⚠️  Container health still 'starting' after 120s — heartbeat probe decides${NC}"
    STEP_WARNINGS+=("ChromaDB container health still 'starting' after 120s — heartbeat probe below decides")
else
    echo -e "   ${RED}❌ MISSING:${NC} Container not healthy after 120s (status: ${chroma_last_state:-unknown})"
    echo -e "      ${YELLOW}→ docker logs aifred-chromadb${NC}"
    STEP_FAILURES+=("ChromaDB container not healthy after 120s")
fi

# HTTP heartbeat. Chroma 0.4.x: /api/v1/heartbeat, Chroma 0.5+/0.6+:
# /api/v2/heartbeat. We try both; if neither answers (e.g. too early)
# a TCP probe acts as final anchor (says: port open, HTTP layer
# questionable). 15 × 2s = 30s probe — on a fresh start the HTTP
# layer can briefly not answer even after container start, even if
# health is already healthy.
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
    echo -e "   ${GREEN}✅ verified:${NC} ChromaDB HTTP heartbeat responds (port 8000)"
elif (echo > /dev/tcp/localhost/8000) &>/dev/null; then
    echo -e "   ${YELLOW}⚠️  Port 8000 open, but no heartbeat — maybe still warming up${NC}"
    STEP_WARNINGS+=("ChromaDB heartbeat doesn't respond (port open, API not ready yet?)")
else
    echo -e "   ${RED}❌ MISSING:${NC} ChromaDB responds neither via HTTP nor TCP on port 8000"
    echo -e "      ${YELLOW}→ docker logs aifred-chromadb${NC}"
    STEP_FAILURES+=("ChromaDB port 8000 not reachable")
fi

step_summary "2c — ChromaDB container"
echo ""
sleep 1

# ============================================================
# STEP 2d: Piper TTS (optional, local/offline)
# ============================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Step 2d: Piper TTS (optional)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "Piper is a local, fast offline TTS engine. The default engine is"
echo "Edge-TTS (cloud) — Piper is only needed if you want an offline voice."
echo "Size: pip package ~10 MB + models 20-110 MB each per voice."
echo ""
read -p "Set up Piper TTS? (y/N): " -n 1 -r PIPER_REPLY
echo ""
PIPER_CHOSEN=0
if [[ $PIPER_REPLY =~ ^[JjYy]$ ]]; then
    PIPER_CHOSEN=1
    # Install piper-tts pip package (not in requirements.txt since optional).
    echo "📥 Installing piper-tts into venv..."
    if "$PROJECT_DIR/venv/bin/pip" install piper-tts; then
        echo -e "${GREEN}✅ piper-tts installed${NC}"
        # Model selection via helper script (reads PIPER_VOICES from config.py).
        if [ -f "$SCRIPT_DIR/install-piper-models.py" ]; then
            "$PROJECT_DIR/venv/bin/python" "$SCRIPT_DIR/install-piper-models.py" || true
        else
            echo -e "${YELLOW}⚠️  $SCRIPT_DIR/install-piper-models.py missing — model download skipped.${NC}"
            echo "   Pull models manually: https://huggingface.co/rhasspy/piper-voices"
            echo "   Target directory: $PROJECT_DIR/piper_models/"
        fi
    else
        echo -e "${RED}❌ piper-tts pip install failed — skipping model download.${NC}"
        echo "   Catch up: source venv/bin/activate && pip install piper-tts"
        echo "             python scripts/install-piper-models.py"
    fi
else
    echo -e "${YELLOW}⏭️  Piper TTS skipped.${NC}"
    echo "   Catch up: source venv/bin/activate && pip install piper-tts"
    echo "             python scripts/install-piper-models.py"
fi

# ─── Verification of step 2d: only when user chose Piper ───
# On skip Piper is intentionally absent → no error, just info.
if [ "$PIPER_CHOSEN" = "1" ]; then
    echo ""
    echo -e "${BLUE}🔎 Verifying Piper TTS...${NC}"
    verify_step "piper binary in venv callable" \
        "[ -x '$PROJECT_DIR/venv/bin/piper' ] && '$PROJECT_DIR/venv/bin/piper' --help" \
        "source venv/bin/activate && pip install piper-tts"
    # At least one .onnx model in piper_models/ must exist, otherwise
    # TTS has no voice to use. Glob via shopt+nullglob in a subshell.
    warn_step "At least one Piper voice model in piper_models/" \
        "ls '$PROJECT_DIR/piper_models/'*.onnx 2>/dev/null | head -1 | grep -q ." \
        "venv/bin/python scripts/install-piper-models.py"
    step_summary "2d — Piper TTS"
fi
echo ""
sleep 1

# ============================================================
# STEP 2e: Whisper STT (required — voice input in the UI)
# ============================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Step 2e: Whisper STT (voice input)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "Whisper STT is the speech recognition behind the voice-input button in the UI."
echo "Runs as its own Docker container (faster-whisper, CPU+GPU dual device)."
echo "First start: image build takes 5-10 min + model pull (~1.5 GB for 'medium')."
echo ""

WHISPER_STARTED=0
if [ -f "$PROJECT_DIR/docker/whisper/docker-compose.yml" ] \
   && command -v docker &>/dev/null \
   && docker compose version &>/dev/null 2>&1; then
    # Idempotency check: is the container already running?
    if docker_run "docker ps --format '{{.Names}}' | grep -qx whisper-stt"; then
        echo -e "${GREEN}✅ Whisper container 'whisper-stt' already running${NC}"
        WHISPER_STARTED=1
    else
        echo "   Building + starting Whisper container (can take a while on first run)..."
        if docker_run "cd '$PROJECT_DIR/docker/whisper' && docker compose up -d --build"; then
            echo -e "${GREEN}✅ Whisper container started${NC}"
            WHISPER_STARTED=1
        else
            echo -e "${YELLOW}⚠️  Whisper build/start failed.${NC}"
            echo "   Catch up manually:"
            echo "       cd $PROJECT_DIR/docker/whisper && docker compose up -d --build"
        fi
    fi
else
    echo -e "${YELLOW}⚠️  docker / docker compose not available or docker/whisper/ missing — skipped.${NC}"
fi

# Verification — container running + port 5080 (external) listens.
echo ""
echo -e "${BLUE}🔎 Verifying Whisper STT...${NC}"
if [ "$WHISPER_STARTED" = "1" ]; then
    verify_step "whisper-stt container running" \
        "docker ps --format '{{.Names}}' | grep -qx whisper-stt" \
        "cd docker/whisper && docker compose up -d --build"
    # Healthcheck inside the container: 30s interval + start_period 60s.
    # Poll generously because the model pull on a fresh start takes
    # 1-2 min.
    echo "   ⏳ Waiting for Whisper health (max 180s — first start loads 'medium' model)..."
    whisper_ok=0
    for _ in $(seq 1 90); do
        if curl -sf --max-time 5 http://localhost:5080/health -o /dev/null 2>/dev/null; then
            whisper_ok=1
            break
        fi
        sleep 2
    done
    if [ "$whisper_ok" = "1" ]; then
        echo -e "   ${GREEN}✅ verified:${NC} Whisper /health responds (port 5080)"
    else
        echo -e "   ${YELLOW}⚠️  Whisper /health not ready after 180s${NC}"
        echo -e "      ${YELLOW}→ docker logs whisper-stt -f${NC}"
        STEP_WARNINGS+=("Whisper STT taking longer to come up — check docker logs whisper-stt")
    fi
else
    STEP_WARNINGS+=("Whisper STT not started — voice input in the UI won't work (cd docker/whisper && docker compose up -d --build)")
fi
step_summary "2e — Whisper STT"
echo ""
sleep 1

# ============================================================
# STEP 2f: SearXNG (required — privacy-friendly web search)
# ============================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Step 2f: SearXNG (web search)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "SearXNG is the local, privacy-friendly meta search engine that"
echo "AIfred's web research uses (alternative/complement to Brave + Tavily)."
echo "Defined in docker-compose.yml under 'profiles: [full]' — we start"
echo "it explicitly with --profile full."
echo ""

SEARXNG_STARTED=0
if [ -f "$PROJECT_DIR/docker/docker-compose.yml" ] \
   && command -v docker &>/dev/null \
   && docker compose version &>/dev/null 2>&1; then
    if docker_run "docker ps --format '{{.Names}}' | grep -qx searxng"; then
        echo -e "${GREEN}✅ SearXNG container 'searxng' already running${NC}"
        SEARXNG_STARTED=1
    else
        echo "   Starting SearXNG container..."
        if docker_run "cd '$PROJECT_DIR/docker' && docker compose --profile full up -d searxng"; then
            echo -e "${GREEN}✅ SearXNG started${NC}"
            SEARXNG_STARTED=1
        else
            echo -e "${YELLOW}⚠️  SearXNG start failed.${NC}"
            echo "   Catch up manually:"
            echo "       cd $PROJECT_DIR/docker && docker compose --profile full up -d searxng"
        fi
    fi
else
    echo -e "${YELLOW}⚠️  docker / docker compose not available — skipped.${NC}"
fi

echo ""
echo -e "${BLUE}🔎 Verifying SearXNG...${NC}"
if [ "$SEARXNG_STARTED" = "1" ]; then
    verify_step "searxng container running" \
        "docker ps --format '{{.Names}}' | grep -qx searxng" \
        "cd docker && docker compose --profile full up -d searxng"
    # SearXNG needs a few seconds before HTTP responds — image usually
    # already pulled (~50 MB), actual boot ~5s. 60s polling generous.
    echo "   ⏳ Waiting for SearXNG (port 8888, max 60s)..."
    searxng_ok=0
    for _ in $(seq 1 30); do
        if curl -sf --max-time 5 http://localhost:8888/ -o /dev/null 2>/dev/null; then
            searxng_ok=1
            break
        fi
        sleep 2
    done
    if [ "$searxng_ok" = "1" ]; then
        echo -e "   ${GREEN}✅ verified:${NC} SearXNG responds on port 8888"
    else
        echo -e "   ${YELLOW}⚠️  SearXNG not ready after 60s${NC}"
        echo -e "      ${YELLOW}→ docker logs searxng -f${NC}"
        STEP_WARNINGS+=("SearXNG not ready yet — web research falls back to Brave/Tavily")
    fi
else
    STEP_WARNINGS+=("SearXNG not started — web research uses only Brave/Tavily (if API keys are set)")
fi
step_summary "2f — SearXNG"
echo ""
sleep 1

# ============================================================
# STEP 2g: Local TTS containers (optional, on-demand)
# ============================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Step 2g: Local TTS containers (optional)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "AIfred supports multiple local TTS engines that run as Docker containers"
echo "on GPU and are started on-demand by AIfred itself — when the engine in"
echo "the UI dropdown changes, NOT at boot. We only build the image here; the"
echo "container lifecycle is handled by AIfred later."
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
    [qwen3-tts]="Streaming, 10 languages, voice cloning. Image ~11.8 GB · model cache ~3.4 GB · needs GPU (V100 ideal). Fastest streaming TTS."
    [xtts]="Best voice cloning, many built-in speakers. Image ~7.7 GB · model cache ~2 GB · GPU or CPU. More tonally accurate than Qwen3, higher latency."
    [moss-tts]="Zero-shot voice cloning, 20 languages. Image ~6.9 GB · model cache ~3 GB · needs GPU. Batch render after the bubble ends."
    [fish-speech]="5B dual-AR, voice cloning, 80+ languages. Image ~14 GB · needs GPU (≥24 GB VRAM). Streaming, research license."
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
    echo "No TTS engines found under docker/tts/ — skipping step."
    echo ""
else
    echo "Available:"
    echo ""
    for _engine in "${TTS_ENGINES_AVAILABLE[@]}"; do
        _label="${TTS_ENGINE_LABEL[$_engine]:-$_engine}"
        _desc="${TTS_ENGINE_DESC[$_engine]:-See docker/tts/$_engine/}"
        if [ -n "${TTS_ENGINE_RECOMMENDED[$_engine]:-}" ]; then
            echo -e "  ${GREEN}• ${_label}${NC} (recommended) — ${_desc}"
        else
            echo -e "  ${GREEN}• ${_label}${NC} — ${_desc}"
        fi
    done
    echo ""
fi
echo "Without a local TTS container AIfred uses Edge-TTS (cloud) or Piper"
echo "(offline, see step 2d). Each additional engine: more disk + one-time"
echo "build effort (5-15 min per container)."
echo ""

# Read the image name from the first `image:` of the compose file.
# Compose files under docker/tts/<engine>/ use a fixed image: line
# (e.g. "image: qwen3-tts-1.7b-base"), which we use as source of
# truth — no more guessing via heuristic.
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
        echo -e "${YELLOW}⚠️  $compose missing — skipping $label.${NC}"
        STEP_WARNINGS+=("$label skipped — docker/tts/$engine missing")
        return 1
    fi
    # Idempotent: read the image tag from compose, exact existence
    # check via `docker image inspect` (returns exit 0 if present,
    # otherwise 1).
    local image_tag
    image_tag="$(tts_compose_image "$compose")"
    if [ -n "$image_tag" ] && docker_run "docker image inspect '$image_tag'" &>/dev/null; then
        echo -e "${GREEN}✅ $label image '$image_tag' already present — skip build${NC}"
        return 0
    fi
    echo "   Building $label image (can take 5-15 min on first run)..."
    if docker_run "cd '$dir' && docker compose build"; then
        echo -e "${GREEN}✅ $label image built${NC}"
        return 0
    fi
    echo -e "${YELLOW}⚠️  $label build failed.${NC}"
    echo "   Catch up manually: cd docker/tts/$engine && docker compose build"
    STEP_WARNINGS+=("$label build failed — see docker logs / docker compose build output")
    return 1
}

# Interactive selection. Default: NO — every container is several
# GB, the user should pick consciously. Recommended engines get a
# hint in the prompt, but no pre-selected "yes".
TTS_CHOSEN_ENGINES=()
for _engine in "${TTS_ENGINES_AVAILABLE[@]}"; do
    _label="${TTS_ENGINE_LABEL[$_engine]:-$_engine}"
    if [ -n "${TTS_ENGINE_RECOMMENDED[$_engine]:-}" ]; then
        _prompt="Build $_label (recommended)? (y/N): "
    else
        _prompt="Build $_label? (y/N): "
    fi
    read -p "$_prompt" -n 1 -r REPLY; echo
    if [[ $REPLY =~ ^[JjYy]$ ]]; then
        TTS_CHOSEN_ENGINES+=("$_engine")
        tts_build "$_engine" "$_label" || true
    fi
done

if [ ${#TTS_CHOSEN_ENGINES[@]} -eq 0 ]; then
    echo -e "${YELLOW}⏭️  No local TTS containers chosen — AIfred uses Edge-TTS / Piper / eSpeak.${NC}"
fi

# ─── Verification of step 2g: chosen images really exist ───
# Image existence check via the exact tag from each compose file so
# foreign images with similar names (e.g. xtts-fork) don't sneak
# through as false positives.
if [ ${#TTS_CHOSEN_ENGINES[@]} -gt 0 ]; then
    echo ""
    echo -e "${BLUE}🔎 Verifying TTS container images...${NC}"
    for _engine in "${TTS_CHOSEN_ENGINES[@]}"; do
        _label="${TTS_ENGINE_LABEL[$_engine]:-$_engine}"
        _img="$(tts_compose_image "$PROJECT_DIR/docker/tts/$_engine/docker-compose.yml" 2>/dev/null)"
        verify_step "$_label image '$_img' present" \
            "docker image inspect '$_img'" \
            "cd docker/tts/$_engine && docker compose build"
    done
    step_summary "2g — Local TTS containers"
fi
echo ""
sleep 1

fi  # end of "if [ "$DRY_RUN" = "1" ] / else" — steps 1-2g

# ============================================================
# STEP 3: Systemd services installation (optional, WITH sudo)
# ============================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Step 3/3: Systemd services installation (optional)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "Systemd services for automatic start at boot."
if [ "$DRY_RUN" = "1" ]; then
    echo -e "${YELLOW}📝 DRY-RUN: no sudo, no disk writes — delegated to install-services.sh --dry-run.${NC}"
    REPLY="y"
else
    echo -e "${YELLOW}⚠️  Needs sudo!${NC}"
    echo ""
    read -p "Install systemd services? (y/N): " -n 1 -r
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
            echo "   Starting install-services.sh ${SYSTEMD_FLAGS[*]} (no sudo needed in dry-run)..."
            set +e
            bash "$SCRIPT_DIR/install-services.sh" "${SYSTEMD_FLAGS[@]}"
            SYSTEMD_SVC_EXIT=$?
            set -e
        else
            echo "   Starting installation with sudo ${SYSTEMD_FLAGS[*]}..."
            # install-services.sh has its own verification and exits with
            # !=0 if services don't run. We want to reach the final
            # summary in install-all.sh though, so we remember the exit
            # code instead of aborting. set +e/-e bypasses the outer
            # script's set -e.
            set +e
            sudo bash "$SCRIPT_DIR/install-services.sh" "${SYSTEMD_FLAGS[@]}"
            SYSTEMD_SVC_EXIT=$?
            set -e
        fi
        if [ "$SYSTEMD_SVC_EXIT" -ne 0 ]; then
            STEP_FAILURES+=("install-services.sh exit $SYSTEMD_SVC_EXIT — see output above")
        fi
    else
        echo -e "${RED}❌ Script not found: $SCRIPT_DIR/install-services.sh${NC}"
        echo "   Skipping systemd installation..."
        SYSTEMD_CHOSEN=0
    fi
else
    echo -e "${YELLOW}⏭️  Systemd installation skipped${NC}"
    echo ""
    echo "   You can start AIfred manually with:"
    echo "   cd $PROJECT_DIR"
    echo "   source venv/bin/activate"
    echo "   reflex run"
fi

# ─── Verification of step 3: only when user chose systemd ───
# On skip it's intentionally not there → no error, just info.
# In --dry-run we skip the whole verification: no service was
# actually installed/started, and port probes would wrongly count
# running host services in the same network namespace (sandbox
# without --private-network = host ports visible).
if [ "$DRY_RUN" = "1" ]; then
    echo ""
    echo -e "${YELLOW}📝 DRY-RUN: service verification skipped (no real service start).${NC}"
elif [ "$SYSTEMD_CHOSEN" = "1" ]; then
    echo ""
    echo -e "${BLUE}🔎 Verifying systemd services...${NC}"
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
    # AIfred needs ~30-90s on a fresh start until backend port 8002
    # listens: Reflex initializes Bun on the very first start (downloads
    # Bun binary, installs node_modules) and compiles the frontend.
    # With a warm .web/ cache it's 10-15s, on a cold start it can be
    # 60s+. We poll generously for 120s — the backend port listens
    # as soon as Reflex's Granian has started, so independent of the
    # frontend build.
    echo "   ⏳ Waiting for AIfred backend (port 8002, max 120s)..."
    aifred_port_ok=0
    for _ in $(seq 1 60); do
        if (echo > /dev/tcp/localhost/8002) &>/dev/null; then
            aifred_port_ok=1
            break
        fi
        sleep 2
    done
    if [ "$aifred_port_ok" = "1" ]; then
        echo -e "   ${GREEN}✅ verified:${NC} AIfred backend listens on port 8002"
    else
        echo -e "   ${RED}❌ MISSING:${NC} AIfred backend not reachable on port 8002 after 120s"
        echo -e "      ${YELLOW}→ journalctl -u aifred-intelligence.service -e --no-pager${NC}"
        STEP_FAILURES+=("AIfred backend port 8002 not listening")
    fi
    # Frontend port 3002 takes even longer on the very first start
    # (Bun setup, Vite build of _index.jsx with all eager-loaded
    # modals). On a first start 2-5 min is realistic. Only poll for
    # 60s — if it doesn't work, warning instead of blocker. The
    # backend alone is enough for CLI/API access.
    echo "   ⏳ Waiting for AIfred frontend (port 3002, max 60s)..."
    frontend_port_ok=0
    for _ in $(seq 1 30); do
        if (echo > /dev/tcp/localhost/3002) &>/dev/null; then
            frontend_port_ok=1
            break
        fi
        sleep 2
    done
    if [ "$frontend_port_ok" = "1" ]; then
        echo -e "   ${GREEN}✅ verified:${NC} AIfred frontend listens on port 3002"
    else
        echo -e "   ${YELLOW}⚠️  Frontend port 3002 not open yet (first-time build can take 2-5 min)${NC}"
        echo -e "      ${YELLOW}→ journalctl -u aifred-intelligence.service -f${NC}"
        STEP_WARNINGS+=("AIfred frontend port 3002 taking longer — first Bun/Vite build")
    fi
    step_summary "3 — Systemd services"
fi

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Create whitelist user${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "AIfred needs at least one whitelist user so someone can register"
echo "in the web UI. Without this entry registration rejects every"
echo "username — the UI loads but no one gets in."
echo ""

# Tracking flag for the final summary (health check reads it).
WHITELIST_USER_CREATED=0

if [ "$DRY_RUN" = "1" ]; then
    if [ -f "$PROJECT_DIR/data/allowed_users.json" ]; then
        echo -e "${YELLOW}📝 DRY-RUN: allowed_users.json already exists — would be skipped.${NC}"
    else
        echo -e "${YELLOW}📝 DRY-RUN: WOULD prompt for whitelist user (writes data/allowed_users.json).${NC}"
    fi
    echo ""
else

# Repeat until a user is created OR the user explicitly types
# 'skip'. This avoids the common silent-fail case where enter-enter
# leads to a skipped step.
while true; do
    read -p "Username (empty/skip = skip with warning): " WHITELIST_USER
    if [ -z "$WHITELIST_USER" ] || [ "$WHITELIST_USER" = "skip" ]; then
        echo -e "${YELLOW}⚠️  Whitelist user creation skipped.${NC}"
        echo -e "${YELLOW}   Login won't work in the UI until you catch up:${NC}"
        echo "       ./aifred-admin add <username>"
        break
    fi
    if [ -x "$PROJECT_DIR/aifred-admin" ]; then
        if "$PROJECT_DIR/aifred-admin" add "$WHITELIST_USER"; then
            WHITELIST_USER_CREATED=1
            break
        else
            echo -e "${YELLOW}⚠️  aifred-admin add failed — please try again or enter 'skip'.${NC}"
        fi
    else
        echo -e "${YELLOW}⚠️  $PROJECT_DIR/aifred-admin not executable — skipping.${NC}"
        break
    fi
done
fi  # end of "if [ "$DRY_RUN" = "1" ] / else" — whitelist user prompt

# ─── Verification of whitelist user ───
if [ "$WHITELIST_USER_CREATED" = "1" ]; then
    echo ""
    echo -e "${BLUE}🔎 Verifying whitelist user...${NC}"
    verify_step "allowed_users.json exists" \
        "[ -f '$PROJECT_DIR/data/allowed_users.json' ]" \
        "./aifred-admin add <username>"
    verify_step "Whitelist user '$WHITELIST_USER' registered" \
        "'$PROJECT_DIR/aifred-admin' users | grep -qiF '$WHITELIST_USER'" \
        "./aifred-admin add $WHITELIST_USER"
fi

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Final summary${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "Summary of all step verifications (failures from each step)."
echo ""

# Whitelist user skip is a soft-fail, not a step failure — put it
# into STEP_WARNINGS here if not already there.
if [ "${WHITELIST_USER_CREATED:-0}" = "0" ]; then
    STEP_WARNINGS+=("Whitelist user not created — registration in the UI rejects every user  (./aifred-admin add <user>)")
fi

if [ ${#STEP_FAILURES[@]} -gt 0 ]; then
    echo -e "${RED}❌ Critical issues from step checks — AIfred won't run (fully):${NC}"
    for b in "${STEP_FAILURES[@]}"; do
        echo -e "${RED}   • $b${NC}"
    done
    echo ""
fi

if [ ${#STEP_WARNINGS[@]} -gt 0 ]; then
    echo -e "${YELLOW}⚠️  Hints from step checks — AIfred starts, but features are missing:${NC}"
    for w in "${STEP_WARNINGS[@]}"; do
        echo -e "${YELLOW}   • $w${NC}"
    done
    echo ""
fi

if [ ${#STEP_FAILURES[@]} -eq 0 ] && [ ${#STEP_WARNINGS[@]} -eq 0 ]; then
    echo -e "${GREEN}✅ All step verifications passed.${NC}"
    echo ""
fi

echo "=================================================="
if [ ${#STEP_FAILURES[@]} -eq 0 ]; then
    echo -e "${GREEN}✅ Installation finished!${NC}"
else
    echo -e "${RED}❌ Installation finished with errors — please check above.${NC}"
fi
echo "=================================================="
echo ""
echo "📊 Next steps:"
echo ""
echo "1. Start AIfred:"
if systemctl is-enabled aifred-intelligence.service &>/dev/null; then
    echo "   sudo systemctl restart aifred-intelligence.service"
    echo "   journalctl -u aifred-intelligence.service -f    # logs"
else
    echo "   cd $PROJECT_DIR"
    echo "   source venv/bin/activate"
    echo "   reflex run"
fi
echo ""
echo "2. Open browser, register with the whitelist username:"
echo "   http://localhost:3002"
echo ""
echo "3. Set up at least ONE LLM backend (otherwise no agent answers):"
echo "   • Ollama (easiest start, local):"
echo "       ollama pull qwen3:8b           # ~5 GB, good all-rounder"
echo "       ollama pull qwen3:30b-a3b      # ~18 GB, MoE model with thinking mode"
echo "   • llama.cpp via llama-swap (best performance, see docs/en/guides/llamacpp-setup.md)"
echo "   • Cloud backends: enter API keys in .env (DASHSCOPE/DEEPSEEK/ANTHROPIC/MOONSHOT)"
echo ""
if [ "$DOCKER_GROUP_NEEDS_RELOGIN" = "1" ]; then
    echo -e "${YELLOW}ℹ️  You were freshly added to the 'docker' group.${NC}"
    echo -e "${YELLOW}   For 'docker' to work in normal shells without sg,${NC}"
    echo -e "${YELLOW}   log out once and back in.${NC}"
    echo ""
fi
echo "💡 Optional components (not part of the base installation):"
echo ""
echo "   • llama-swap (LLM backend proxy for llama.cpp, lives outside this repo):"
echo "       https://github.com/mostlygeek/llama-swap"
echo "       Binary into ~/bin, config in ~/.config/llama-swap/config.yaml,"
echo "       systemd unit into /etc/systemd/system/llama-swap.service (own research)."
echo ""
echo "   • Local TTS containers (started on-demand by AIfred — image build catch-up here):"
for _engine in "${TTS_ENGINES_AVAILABLE[@]}"; do
    _label="${TTS_ENGINE_LABEL[$_engine]:-$_engine}"
    printf "       cd %s/docker/tts/%s && docker compose build   # %s\n" "$PROJECT_DIR" "$_engine" "$_label"
done
echo ""
echo "   • Reverse proxy setup (own domain via nginx/caddy):"
echo "       cp scripts/patch-vite-config.sh.example scripts/patch-vite-config.sh"
echo "       # set ALLOWED_HOST=\"your-domain.tld\", then:"
echo "       ./scripts/patch-vite-config.sh    # after the first 'reflex run'"
echo ""
echo "📚 Documentation: $PROJECT_DIR/README.md"
echo ""

# Exit code != 0 if step failures occurred — important for CI / script
# callers that want to evaluate the install result.
if [ ${#STEP_FAILURES[@]} -gt 0 ]; then
    exit 1
fi
exit 0
