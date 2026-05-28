#!/bin/bash
# scripts/sandbox/run-test.sh
#
# Starts an ephemeral systemd-nspawn container and runs install-all.sh
# inside. The repo is mounted read-only; a fresh `git clone` inside
# the container simulates a brand-new install. All non-flag args are
# passed through to install-all.sh.
#
# Three use-cases:
#
#   sudo ./scripts/sandbox/run-test.sh --dry-run [--no-overwrite]
#       → fastest test. No --boot (no systemd), no systemctl calls.
#         Validates apt/pip/script logic for steps 1-2g + service file
#         diffs without disk writes.
#
#   sudo ./scripts/sandbox/run-test.sh
#       → full real run with systemd inside the container. Steps
#         1-2g + service install + service enable. Service start works
#         because the systemd bus is in-container. Container state is
#         gone on exit (--ephemeral).
#
#   sudo ./scripts/sandbox/run-test.sh --shell
#       → interactive shell inside the container for manual debugging
#         / deep inspection.
#
# All non-flag args are forwarded to install-all.sh (e.g. --no-overwrite).

set -euo pipefail

# ─── Configuration ──────────────────────────────────────────────
MACHINE_NAME="${MACHINE_NAME:-aifred-test}"
MACHINE_ROOT="/var/lib/machines/${MACHINE_NAME}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ─── Sudo check ─────────────────────────────────────────────────
if [ "${EUID:-1}" -ne 0 ]; then
    echo -e "${RED}❌ This script needs sudo (systemd-nspawn).${NC}"
    echo "   sudo $0 $*"
    exit 1
fi

# ─── Container root check ───────────────────────────────────────
if [ ! -d "$MACHINE_ROOT" ] || [ ! -x "$MACHINE_ROOT/bin/bash" ]; then
    echo -e "${RED}❌ Container root missing or broken: ${MACHINE_ROOT}${NC}"
    echo "   Run setup first:"
    echo "     sudo $(dirname "$0")/setup-nspawn-root.sh"
    exit 1
fi

# ─── Locate repo ────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ ! -d "$REPO_ROOT/.git" ] || [ ! -f "$REPO_ROOT/scripts/install-all.sh" ]; then
    echo -e "${RED}❌ Repo root doesn't look like AIfred: ${REPO_ROOT}${NC}"
    exit 1
fi

# ─── Split mode + args ──────────────────────────────────────────
# Mode selection:
#   --shell   → interactive login shell (with --boot)
#   --dry-run → no --boot (faster, no systemd needed)
#   default   → --boot (full real run with systemd)
MODE="auto"
INSTALL_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --shell)             MODE="shell" ;;
        --dry-run|-n)        MODE="no-boot"; INSTALL_ARGS+=("$arg") ;;
        --help|-h)
            sed -n '2,26p' "$0"
            exit 0
            ;;
        *)
            INSTALL_ARGS+=("$arg")
            ;;
    esac
done

# No --dry-run → real run with systemd boot.
if [ "$MODE" = "auto" ]; then
    MODE="boot"
fi

# ─── Banner ─────────────────────────────────────────────────────
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  AIfred sandbox run via systemd-nspawn --ephemeral${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "  Container:       ${MACHINE_NAME} (${MACHINE_ROOT})"
echo "  Repo (read-only): ${REPO_ROOT}"
echo "  Mode:            ${MODE}"
if [ "${#INSTALL_ARGS[@]}" -gt 0 ]; then
    echo "  install-all args: ${INSTALL_ARGS[*]}"
fi
echo -e "${YELLOW}  All changes are gone on container exit (--ephemeral).${NC}"
echo ""

# ─── Common nspawn args ─────────────────────────────────────────
COMMON_NSPAWN=(
    --quiet
    --machine="${MACHINE_NAME}-$$"
    --directory="$MACHINE_ROOT"
    --ephemeral
    --bind-ro="$REPO_ROOT:/mnt/aifred-repo"
    --setenv=AIFRED_SANDBOX=1
)
# Network isolation:
#  * no-boot / dry-run: --private-network (no internet needed — apt
#                       and pip are skipped in dry-run anyway — and
#                       protection against "host ports get probed"
#                       matters for honest verification results).
#  * boot / shell:      default network (shared with host) so apt
#                       update, ollama pull, playwright install have
#                       internet. Caveat: port probes "see" host
#                       services. Conscious trade-off for v1; real
#                       installs need internet.

# ─── Mode: shell (interactive with systemd) ─────────────────────
if [ "$MODE" = "shell" ]; then
    echo -e "${GREEN}Starting interactive shell in sandbox (booted).${NC}"
    echo "  Login: aifred / no password  (or root)"
    echo "  Repo:  /mnt/aifred-repo  (read-only)"
    echo "  Exit:  'sudo poweroff' inside the container, or Ctrl-] three times."
    echo ""
    exec systemd-nspawn "${COMMON_NSPAWN[@]}" --boot
fi

# ─── Mode: no-boot (fast dry-run, no systemd needed) ────────────
# nspawn without --boot runs a command directly. systemctl calls in
# such a container would give "Failed to connect to bus" — but in
# dry-run mode install-services.sh doesn't call systemctl, and steps
# 1-2g in install-all.sh are systemd-free.
if [ "$MODE" = "no-boot" ]; then
    echo -e "${GREEN}Starting sandbox without --boot (dry-run friendly, no systemd, no network).${NC}"
    echo ""
    # Pass the runner snippet via stdin → sudo -u aifred bash.
    # --bind-ro for the repo makes the script available.
    # --private-network: sandbox sees NO host network (stops port
    # probes from picking up running host services).
    systemd-nspawn "${COMMON_NSPAWN[@]}" \
        --private-network \
        --pipe \
        --user=aifred \
        /bin/bash -lc "
            set -e
            cd \$HOME
            if [ ! -d AIfred-Intelligence ]; then
                git clone /mnt/aifred-repo AIfred-Intelligence
            fi
            cd AIfred-Intelligence
            bash scripts/install-all.sh ${INSTALL_ARGS[*]}
        "
    EXIT=$?
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}  Sandbox exit (no-boot, dry-run): ${EXIT}${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    exit "$EXIT"
fi

# ─── Mode: boot (full real run with systemd) ────────────────────
# Here we need --boot for systemctl. Auto-exec in a booted container
# goes via systemd-run over the machined bus, but not before boot
# completes. Pragmatic v1: interactive run with instructions. Later
# we automate.
echo -e "${GREEN}Starting full real run with --boot.${NC}"
echo ""
echo -e "${YELLOW}v1 caveat:${NC} auto-run inside --boot is interactive in this first"
echo "version — the container boots, you run the commands inside:"
echo ""
echo "  1. Login as aifred (no password)"
echo "  2. These commands:"
echo ""
echo -e "     ${BLUE}git clone /mnt/aifred-repo ~/AIfred-Intelligence${NC}"
echo -e "     ${BLUE}cd ~/AIfred-Intelligence${NC}"
echo -e "     ${BLUE}bash scripts/install-all.sh ${INSTALL_ARGS[*]:-}${NC}"
echo ""
echo "  3. Exit container:"
echo -e "     ${BLUE}sudo poweroff${NC}"
echo ""
echo "Press Enter to boot the container..."
read -r _

exec systemd-nspawn "${COMMON_NSPAWN[@]}" --boot
