#!/bin/bash
# AIfred Intelligence - Systemd Services Installation Script
#
# Modes:
#   sudo ./scripts/install-services.sh                  normal install/update —
#                                                       differing files are
#                                                       backed up + overwritten
#   sudo ./scripts/install-services.sh --no-overwrite   keep existing files;
#                                                       only create missing
#                                                       ones, warn on diffs
#        ./scripts/install-services.sh --dry-run        show what WOULD change,
#                                                       no disk writes, no
#                                                       systemctl side-effects

set -e  # Exit on error

# ── Argument parsing ────────────────────────────────────────────
DRY_RUN=0
NO_OVERWRITE=0
for arg in "$@"; do
    case "$arg" in
        --dry-run|-n)         DRY_RUN=1 ;;
        --no-overwrite|-N)    NO_OVERWRITE=1 ;;
        --help|-h)
            sed -n '2,11p' "$0"
            exit 0
            ;;
        *)
            echo "❌ Unknown flag: $arg"
            sed -n '2,11p' "$0"
            exit 1
            ;;
    esac
done

if [ "$DRY_RUN" = "1" ]; then
    echo "🔬 AIfred Intelligence - Systemd Services Installation (DRY-RUN)"
    echo "================================================================"
    echo "   No files written. No systemctl side-effects. Shows planned changes."
    [ "$NO_OVERWRITE" = "1" ] && echo "   --no-overwrite is honored in the simulation."
    echo
elif [ "$NO_OVERWRITE" = "1" ]; then
    echo "🛡  AIfred Intelligence - Systemd Services Installation (NO-OVERWRITE)"
    echo "====================================================================="
    echo "   Existing service files are kept untouched. Only missing files are"
    echo "   created. Differences are reported, not applied."
    echo
else
    echo "🚀 AIfred Intelligence - Systemd Services Installation"
    echo "======================================================"
    echo
fi

# Check if running with sudo — except in dry-run, where we don't touch /etc/.
if [ "$DRY_RUN" != "1" ] && [ "$EUID" -ne 0 ]; then
    echo "❌ This script must be run with sudo:"
    echo "   sudo ./scripts/install-services.sh"
    echo "   (or ./scripts/install-services.sh --dry-run for a preview without sudo)"
    exit 1
fi

# Get the actual user (not root when using sudo)
ACTUAL_USER=${SUDO_USER:-$USER}
if [ -z "$ACTUAL_USER" ] || [ "$ACTUAL_USER" = "root" ]; then
    echo "❌ Could not determine the actual user."
    echo "   Please run via 'sudo ./scripts/install-services.sh',"
    echo "   not from a root shell."
    exit 1
fi
echo "📋 Installation for user: $ACTUAL_USER"
echo

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SYSTEMD_DIR="$PROJECT_DIR/systemd"

echo "📂 Project directory: $PROJECT_DIR"
echo

# venv check — the main service needs $PROJECT_DIR/venv/bin/python.
# In --dry-run nothing actually runs, so a missing venv is not a
# blocker — just a hint. In a real run we hard-fail.
if [ ! -x "$PROJECT_DIR/venv/bin/python" ]; then
    if [ "$DRY_RUN" = "1" ]; then
        echo "⚠️  Python venv missing: $PROJECT_DIR/venv  (hard-fail in real run)"
        echo "   In an actual setup install-all.sh would create the venv first."
    else
        echo "❌ Python venv not found: $PROJECT_DIR/venv"
        echo "   Please run ./scripts/setup-python.sh first."
        exit 1
    fi
fi

# Check if service files exist
if [ ! -f "$SYSTEMD_DIR/aifred-chromadb.service" ] || [ ! -f "$SYSTEMD_DIR/aifred-intelligence.service" ]; then
    echo "❌ Service files not found in: $SYSTEMD_DIR"
    exit 1
fi

# Resolve docker binary path — the aifred-chromadb.service template
# contains a __DOCKER_BIN__ placeholder because docker isn't always
# under /usr/bin (Snap docker → /snap/bin/docker, manual install →
# /usr/local/bin/docker). Fallback /usr/bin/docker if docker isn't
# on PATH yet (the service won't run until docker is installed
# anyway — fix later via 'sudo systemctl edit aifred-chromadb.service'
# or by re-running this script).
DOCKER_BIN="$(command -v docker || true)"
if [ -z "$DOCKER_BIN" ]; then
    DOCKER_BIN="/usr/bin/docker"
    echo "ℹ️  docker not on PATH yet — falling back to $DOCKER_BIN."
    echo "   If docker ends up elsewhere later (e.g. /snap/bin/docker),"
    echo "   re-run: sudo $SCRIPT_DIR/install-services.sh"
fi

# Update mode: re-runs do not silently clobber existing service files.
# Three cases per file:
#   * missing                    → write (SERVICES_CHANGED=1)
#   * exists + identical         → silent skip
#   * exists + differs           → backup .pre-aifred-<timestamp> + write
#                                  (SERVICES_CHANGED=1)
# A global SERVICES_CHANGED flag tracks whether daemon-reload / restart
# is needed. Idempotent re-runs can therefore run daily without
# interrupting the service.
SERVICES_CHANGED=0

# Render a service template to stdout (substitutes __USER__, __PROJECT_DIR__,
# __DOCKER_BIN__). Separating render from write lets us cmp the rendered
# output against the on-disk file without touching disk first.
_render_service_template() {
    local src="$1"
    sed -e "s|__USER__|$ACTUAL_USER|g" \
        -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
        -e "s|__DOCKER_BIN__|$DOCKER_BIN|g" \
        "$src"
}

# Helper: render + install a service file — idempotent.
install_service() {
    local src="$1"
    local name="$(basename "$src")"
    local dst="/etc/systemd/system/${name}"
    local tmp; tmp="$(mktemp)"
    _render_service_template "$src" > "$tmp"

    if [ ! -f "$dst" ]; then
        if [ "$DRY_RUN" = "1" ]; then
            echo "   📝 WOULD create: ${name}"
            rm -f "$tmp"
        else
            mv "$tmp" "$dst"
            chmod 644 "$dst"
            echo "   ✅ Newly installed: ${name}"
        fi
        SERVICES_CHANGED=1
        return 0
    fi

    if cmp -s "$tmp" "$dst"; then
        rm -f "$tmp"
        echo "   = Unchanged:    ${name}"
        return 0
    fi

    # File exists and differs.
    if [ "$NO_OVERWRITE" = "1" ]; then
        echo "   🛡  Kept:        ${name}  (--no-overwrite — diff:)"
        diff -u "$dst" "$tmp" | sed 's/^/      /' | head -30 || true
        echo "      (repo version would be installed; local edits preserved.)"
        rm -f "$tmp"
        return 0
    fi
    if [ "$DRY_RUN" = "1" ]; then
        echo "   📝 WOULD update: ${name}  (current → new diff):"
        diff -u "$dst" "$tmp" | sed 's/^/      /' | head -30 || true
        echo "      (would backup to ${name}.pre-aifred-<timestamp>)"
        rm -f "$tmp"
    else
        local stamp; stamp="$(date +%Y%m%d-%H%M%S)"
        local backup="${dst}.pre-aifred-${stamp}"
        cp -p "$dst" "$backup"
        mv "$tmp" "$dst"
        chmod 644 "$dst"
        echo "   ♻️  Updated:     ${name}  (backup: $(basename "$backup"))"
    fi
    SERVICES_CHANGED=1
}

# Helper: idempotently install a drop-in config (override.conf,
# hardening.conf, …) under /etc/systemd/system/<service>.d/. Same
# logic as install_service: only writes on mismatch, makes a backup
# beforehand.
install_dropin() {
    local svc_name="$1"      # e.g. aifred-intelligence.service
    local src="$2"           # path to source drop-in
    local dst_dir="/etc/systemd/system/${svc_name}.d"
    local name="$(basename "$src")"
    local dst="${dst_dir}/${name}"
    [ "$DRY_RUN" = "1" ] || mkdir -p "$dst_dir"
    local tmp; tmp="$(mktemp)"
    _render_service_template "$src" > "$tmp"

    if [ ! -f "$dst" ]; then
        if [ "$DRY_RUN" = "1" ]; then
            echo "   📝 WOULD create dropin: ${svc_name}.d/${name}"
            rm -f "$tmp"
        else
            mv "$tmp" "$dst"
            chmod 644 "$dst"
            echo "   ✅ Drop-in new: ${svc_name}.d/${name}"
        fi
        SERVICES_CHANGED=1
        return 0
    fi

    if cmp -s "$tmp" "$dst"; then
        rm -f "$tmp"
        echo "   = Drop-in unchanged: ${svc_name}.d/${name}"
        return 0
    fi

    if [ "$NO_OVERWRITE" = "1" ]; then
        echo "   🛡  Drop-in kept: ${svc_name}.d/${name}  (--no-overwrite — diff:)"
        diff -u "$dst" "$tmp" | sed 's/^/      /' | head -30 || true
        rm -f "$tmp"
        return 0
    fi
    if [ "$DRY_RUN" = "1" ]; then
        echo "   📝 WOULD update dropin: ${svc_name}.d/${name}  (current → new diff):"
        diff -u "$dst" "$tmp" | sed 's/^/      /' | head -30 || true
        rm -f "$tmp"
    else
        local stamp; stamp="$(date +%Y%m%d-%H%M%S)"
        local backup="${dst}.pre-aifred-${stamp}"
        cp -p "$dst" "$backup"
        mv "$tmp" "$dst"
        chmod 644 "$dst"
        echo "   ♻️  Drop-in updated: ${svc_name}.d/${name}  (backup: $(basename "$backup"))"
    fi
    SERVICES_CHANGED=1
}

echo "1️⃣  Installing main services (chromadb + aifred-intelligence)..."
install_service "$SYSTEMD_DIR/aifred-chromadb.service"
install_service "$SYSTEMD_DIR/aifred-intelligence.service"

# Install drop-in for aifred-intelligence.service if present. The
# hardening.conf drop-in extends REFLEX_HOT_RELOAD_EXCLUDE_PATHS to
# also cover aifred_vector_cache/scripts/deploy/docs/docker/tests/systemd.
# Without this drop-in Granian kills the Reflex worker on every
# ChromaDB embed batch (chroma.sqlite3 write triggers hot reload),
# so indexing jobs die after the first batch. It also clears Requires=
# and sets Wants= so a ChromaDB recreate (docker auto-update) doesn't
# cascade-stop AIfred.
DROPIN_DIR="$SYSTEMD_DIR/aifred-intelligence.service.d"
if [ -d "$DROPIN_DIR" ]; then
    for dropin in "$DROPIN_DIR"/*.conf; do
        [ -f "$dropin" ] || continue
        install_dropin "aifred-intelligence.service" "$dropin"
    done
fi

echo
echo "   Configured with:"
echo "      User:        $ACTUAL_USER"
echo "      Project dir: $PROJECT_DIR"
echo "      Docker bin:  $DOCKER_BIN"
echo

# Corpus search server (FastAPI corpus search API).
# Default yes: serves as backend for the corpus / Judaica search UI
# behind nginx. Standalone AIfred works without it, but the repo
# ships the service file and matching Python script
# (scripts/corpus_search_server.py) — opt out by answering N.
INSTALL_CORPUS=0
if [ -f "$SYSTEMD_DIR/aifred-corpus-server.service" ]; then
    echo "2️⃣  Corpus search server (FastAPI corpus search API)"
    echo "   Default install — backend for the corpus / Judaica search UI."
    if [ -t 0 ]; then
        read -p "   Install? (Y/n): " -n 1 -r CORPUS_REPLY
        echo
        if [[ ! $CORPUS_REPLY =~ ^[Nn]$ ]]; then
            install_service "$SYSTEMD_DIR/aifred-corpus-server.service"
            INSTALL_CORPUS=1
        else
            echo "   ⏭️  skipped"
        fi
    else
        # Non-interactive: default-install (matches the default-yes).
        install_service "$SYSTEMD_DIR/aifred-corpus-server.service"
        INSTALL_CORPUS=1
        echo "   ✅ Non-interactive call — default-installed"
    fi
fi
echo

# Idempotent ensure: enable always (no-op if already enabled), and prefer
# start over restart unless we actually changed the unit file. Restarting
# a healthy service for no reason interrupts a running calibration etc.
ensure_active() {
    local svc="$1"
    if [ "$DRY_RUN" = "1" ]; then
        # /run/systemd/system only exists when systemd is PID 1.
        # Without it (e.g. nspawn without --boot, or chroot) systemctl
        # would emit a confusing "Failed to connect to bus: Host is
        # down" into the WOULD line. Keep it short and honest.
        if [ ! -d /run/systemd/system ]; then
            echo "   📝 WOULD ensure active: ${svc}  (no systemd in this env)"
            return 0
        fi
        local state; state="$(systemctl is-active "$svc" 2>/dev/null || true)"
        if [ "$state" = "active" ]; then
            if [ "$SERVICES_CHANGED" = "1" ]; then
                echo "   📝 WOULD restart: ${svc}  (unit changed, currently active)"
            else
                echo "   = ${svc} already active, nothing to do"
            fi
        else
            echo "   📝 WOULD start: ${svc}  (currently ${state:-unknown})"
        fi
        return 0
    fi
    if systemctl is-active --quiet "$svc"; then
        if [ "$SERVICES_CHANGED" = "1" ]; then
            systemctl restart "$svc"
            echo "   🔄 Restarted: ${svc}  (unit file changed)"
        else
            echo "   = ${svc} already running, no restart needed"
        fi
    else
        systemctl start "$svc"
        echo "   ✅ Started:   ${svc}"
    fi
}

echo "3️⃣  Reloading systemd..."
if [ "$SERVICES_CHANGED" = "1" ]; then
    if [ "$DRY_RUN" = "1" ]; then
        echo "   📝 WOULD: systemctl daemon-reload  (unit files changed)"
    else
        systemctl daemon-reload
        echo "   ✅ Systemd reloaded"
    fi
else
    echo "   = No unit changes, daemon-reload skipped"
fi
echo

echo "4️⃣  Enabling services on boot..."
if [ "$DRY_RUN" = "1" ]; then
    for s in aifred-chromadb.service aifred-intelligence.service; do
        if systemctl is-enabled --quiet "$s" 2>/dev/null; then
            echo "   = $s already enabled"
        else
            echo "   📝 WOULD enable: $s"
        fi
    done
    [ "$INSTALL_CORPUS" = "1" ] && {
        if systemctl is-enabled --quiet aifred-corpus-server.service 2>/dev/null; then
            echo "   = aifred-corpus-server.service already enabled"
        else
            echo "   📝 WOULD enable: aifred-corpus-server.service"
        fi
    }
else
    systemctl enable aifred-chromadb.service
    systemctl enable aifred-intelligence.service
    [ "$INSTALL_CORPUS" = "1" ] && systemctl enable aifred-corpus-server.service
    echo "   ✅ Services enabled"
fi
echo

echo "5️⃣  Starting / updating services..."
# Only start ChromaDB if docker is present — otherwise ExecStart fails.
if command -v docker &>/dev/null && docker compose version &>/dev/null; then
    ensure_active aifred-chromadb.service
    [ "$DRY_RUN" = "1" ] || sleep 2
else
    echo "   ⚠️  docker or 'docker compose' missing — aifred-chromadb.service not started."
    echo "      Start ChromaDB manually once docker is installed:"
    echo "        sudo systemctl start aifred-chromadb.service"
fi
ensure_active aifred-intelligence.service
if [ "$INSTALL_CORPUS" = "1" ]; then
    ensure_active aifred-corpus-server.service
fi
echo

echo "6️⃣  Symlinking llama-swap-restart into ~/bin..."
# llama-swap-restart is the maintenance script for model switches and
# config updates. Tends to get lost inside scripts/ otherwise.
# Symlink makes it globally callable if ~/bin is on PATH.
USER_HOME=$(getent passwd "$ACTUAL_USER" | cut -d: -f6)
USER_BIN="$USER_HOME/bin"
RESTART_SCRIPT="$PROJECT_DIR/scripts/llama-swap-restart.sh"
if [ -f "$RESTART_SCRIPT" ]; then
    if [ "$DRY_RUN" = "1" ]; then
        if [ -L "$USER_BIN/llama-swap-restart" ] && \
           [ "$(readlink "$USER_BIN/llama-swap-restart" 2>/dev/null)" = "$RESTART_SCRIPT" ]; then
            echo "   = Symlink already in place: $USER_BIN/llama-swap-restart"
        else
            echo "   📝 WOULD symlink: $USER_BIN/llama-swap-restart -> $RESTART_SCRIPT"
        fi
    else
        sudo -u "$ACTUAL_USER" mkdir -p "$USER_BIN"
        chmod +x "$RESTART_SCRIPT"
        sudo -u "$ACTUAL_USER" ln -sf "$RESTART_SCRIPT" "$USER_BIN/llama-swap-restart"
        echo "   ✅ Symlink: $USER_BIN/llama-swap-restart -> $RESTART_SCRIPT"
        # PATH hint if ~/bin is not on PATH
        if ! sudo -u "$ACTUAL_USER" bash -c 'echo "$PATH"' | tr ':' '\n' | grep -qx "$USER_BIN"; then
            echo "   ⚠️  $USER_BIN is not on PATH — add to ~/.bashrc:"
            echo "       export PATH=\"\$HOME/bin:\$PATH\""
        fi
    fi
else
    echo "   ⚠️  $RESTART_SCRIPT not found — symlink skipped"
fi
echo

# Dry-run ends here — status + verification refer to the real install.
# In the 6 steps above the user already sees what would happen.
if [ "$DRY_RUN" = "1" ]; then
    echo "═══════════════════════════════════════════════════════════════"
    echo "  Dry-run finished. No disk writes, no service side-effects."
    if [ "$SERVICES_CHANGED" = "1" ]; then
        echo "  ⚠️  Some service files would be written/updated."
        echo "  Real run: sudo $0"
    else
        echo "  ✅ All service files already current. A real run would change nothing."
    fi
    echo "═══════════════════════════════════════════════════════════════"
    exit 0
fi

echo "7️⃣  Checking service status..."
echo
echo "--- ChromaDB status ---"
systemctl status aifred-chromadb.service --no-pager -l || true
echo
echo "--- AIfred Intelligence status ---"
systemctl status aifred-intelligence.service --no-pager -l || true
if [ "$INSTALL_CORPUS" = "1" ]; then
    echo
    echo "--- AIfred Corpus Server status ---"
    systemctl status aifred-corpus-server.service --no-pager -l || true
fi
echo

# ────────────────────────────────────────────────────────────────
# Runtime verification of all started services.
# 'systemctl status' alone is NOT enough — that returns exit 0 even
# when the service is "loaded but failed". We explicitly check
# is-active + open TCP ports.
# ────────────────────────────────────────────────────────────────
echo "8️⃣  Verifying service runtime..."
echo
SERVICE_FAILURES=()

check_service_active() {
    local svc="$1"
    if systemctl is-active --quiet "$svc"; then
        echo "   ✅ $svc active"
    else
        echo "   ❌ $svc NOT active (is-active = $(systemctl is-active "$svc" 2>&1 || true))"
        echo "      → journalctl -u $svc -e --no-pager"
        SERVICE_FAILURES+=("$svc not active")
    fi
}

# Only check ChromaDB if we actually started it.
if command -v docker &>/dev/null && docker compose version &>/dev/null; then
    check_service_active aifred-chromadb.service
    # Port probe (container listens on 8000 internally). First-time
    # start can additionally include the image pull (chromadb/chroma:latest
    # ~200 MB) so give it a generous 120s instead of 30s.
    chroma_port_ok=0
    for _ in $(seq 1 60); do
        (echo > /dev/tcp/localhost/8000) &>/dev/null && { chroma_port_ok=1; break; }
        sleep 2
    done
    if [ "$chroma_port_ok" = "1" ]; then
        echo "   ✅ Port 8000 (ChromaDB HTTP) open"
    else
        echo "   ❌ Port 8000 (ChromaDB) not reachable after 120s"
        echo "      → docker logs aifred-chromadb"
        SERVICE_FAILURES+=("ChromaDB port 8000 not reachable")
    fi
fi

check_service_active aifred-intelligence.service

# AIfred backend takes ~30-90s on first start (Bun setup, Reflex
# compiles the frontend, loads models from config.py etc.). Poll
# generously for 120s.
aifred_port_ok=0
for _ in $(seq 1 60); do
    (echo > /dev/tcp/localhost/8002) &>/dev/null && { aifred_port_ok=1; break; }
    sleep 2
done
if [ "$aifred_port_ok" = "1" ]; then
    echo "   ✅ Port 8002 (AIfred backend) open"
else
    echo "   ❌ Port 8002 (AIfred backend) not reachable after 120s"
    echo "      → journalctl -u aifred-intelligence.service -e --no-pager"
    SERVICE_FAILURES+=("AIfred backend port 8002 not reachable")
fi

# Frontend port 3002: first start realistically takes 2-5 min (Bun
# setup, Vite build). Poll 60s — warning, not failure, since the
# backend alone is enough for CLI/API.
frontend_port_ok=0
for _ in $(seq 1 30); do
    (echo > /dev/tcp/localhost/3002) &>/dev/null && { frontend_port_ok=1; break; }
    sleep 2
done
if [ "$frontend_port_ok" = "1" ]; then
    echo "   ✅ Port 3002 (AIfred frontend) open"
else
    echo "   ⚠️  Port 3002 (AIfred frontend) not open yet — first-time build can take 2-5 min"
    echo "      → journalctl -u aifred-intelligence.service -f"
fi

if [ "$INSTALL_CORPUS" = "1" ]; then
    check_service_active aifred-corpus-server.service
fi
echo

if [ ${#SERVICE_FAILURES[@]} -gt 0 ]; then
    echo "❌ Installation finished, but services aren't running cleanly:"
    for f in "${SERVICE_FAILURES[@]}"; do
        echo "   • $f"
    done
    echo
    SERVICE_EXIT=1
else
    echo "✅ Installation finished — all services running!"
    SERVICE_EXIT=0
fi
echo
echo "📊 Useful commands:"
echo "   View logs:       journalctl -u aifred-intelligence.service -f"
echo "   Restart service: sudo systemctl restart aifred-intelligence.service"
echo "   Stop service:    sudo systemctl stop aifred-intelligence.service"
echo "   Check status:    systemctl status aifred-intelligence.service"
echo
echo "📚 See systemd/README.md for more details"

# Propagate exit code — install-all.sh can react to it.
exit "${SERVICE_EXIT:-0}"
