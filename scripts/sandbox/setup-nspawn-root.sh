#!/bin/bash
# scripts/sandbox/setup-nspawn-root.sh
#
# One-shot setup: creates an Ubuntu Noble container root under
# /var/lib/machines/aifred-test/ that run-test.sh then uses via
# systemd-nspawn --ephemeral.
#
# Idempotent: re-runs check what's there and skip steps that are
# already done.
#
# Needs sudo (debootstrap + write access to /var/lib/machines/).

set -euo pipefail

# ─── Configuration ──────────────────────────────────────────────
MACHINE_NAME="${MACHINE_NAME:-aifred-test}"
MACHINE_ROOT="/var/lib/machines/${MACHINE_NAME}"
UBUNTU_CODENAME="${UBUNTU_CODENAME:-noble}"     # = 24.04 LTS
UBUNTU_MIRROR="${UBUNTU_MIRROR:-http://archive.ubuntu.com/ubuntu}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ─── Sudo check ─────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}❌ This script needs sudo:${NC}"
    echo "   sudo $0"
    exit 1
fi

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  AIfred Sandbox — nspawn root setup${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "  Container name:  ${MACHINE_NAME}"
echo "  Container root:  ${MACHINE_ROOT}"
echo "  Ubuntu release:  ${UBUNTU_CODENAME}"
echo ""

# ─── 1. Install tools ───────────────────────────────────────────
echo -e "${BLUE}1/3 — Check / install systemd-container + debootstrap${NC}"
NEEDED_PKGS=()
command -v systemd-nspawn >/dev/null || NEEDED_PKGS+=(systemd-container)
command -v debootstrap >/dev/null    || NEEDED_PKGS+=(debootstrap)

if [ "${#NEEDED_PKGS[@]}" -gt 0 ]; then
    echo "   Installing: ${NEEDED_PKGS[*]}"
    apt-get update -qq
    apt-get install -y "${NEEDED_PKGS[@]}"
else
    echo "   ✅ systemd-nspawn + debootstrap already installed"
fi
echo ""

# ─── 2. Create container root ───────────────────────────────────
echo -e "${BLUE}2/3 — Container root via debootstrap${NC}"
mkdir -p "$(dirname "$MACHINE_ROOT")"

if [ -d "$MACHINE_ROOT" ] && [ -x "$MACHINE_ROOT/bin/bash" ]; then
    echo -e "${YELLOW}   Container already exists: ${MACHINE_ROOT}${NC}"
    echo "   Delete and rebuild? (y/N)"
    read -p "   > " -n 1 -r REPLY
    echo ""
    if [[ $REPLY =~ ^[JjYy]$ ]]; then
        echo "   Deleting $MACHINE_ROOT ..."
        rm -rf "$MACHINE_ROOT"
    else
        echo "   ✅ Keeping existing container root"
    fi
fi

if [ ! -d "$MACHINE_ROOT" ]; then
    echo "   Building Ubuntu ${UBUNTU_CODENAME} root (takes ~3 min, ~500 MB)..."
    # --variant=minbase: smallest possible. Later inside the container
    # install-all.sh pulls in whatever it actually needs.
    # --include=systemd,dbus,sudo: so systemd can run as init and sudo
    # is available inside (the install script needs it).
    debootstrap \
        --variant=minbase \
        --include=systemd,systemd-sysv,dbus,sudo,ca-certificates,locales,gnupg,wget,curl,git \
        "$UBUNTU_CODENAME" "$MACHINE_ROOT" "$UBUNTU_MIRROR"
fi
echo "   ✅ Container root: ${MACHINE_ROOT}"
echo ""

# ─── 3. Container base config (hostname, sudo user) ─────────────
echo -e "${BLUE}3/3 — Container base config${NC}"

# Hostname
echo "${MACHINE_NAME}" > "$MACHINE_ROOT/etc/hostname"

# /etc/hosts (debootstrap minbase leaves this empty)
cat > "$MACHINE_ROOT/etc/hosts" << EOF
127.0.0.1   localhost ${MACHINE_NAME}
::1         localhost ${MACHINE_NAME}
EOF

# Enable the universe repository for apt (debootstrap minbase ships
# main only). AIfred needs python3-venv from main, but via pip we may
# pull packages whose build-time deps live in universe.
cat > "$MACHINE_ROOT/etc/apt/sources.list" << EOF
deb ${UBUNTU_MIRROR} ${UBUNTU_CODENAME} main universe
deb ${UBUNTU_MIRROR} ${UBUNTU_CODENAME}-updates main universe
deb ${UBUNTU_MIRROR} ${UBUNTU_CODENAME}-security main universe
EOF

# Create test user 'aifred' (passwordless sudo).
# install-all.sh expects a normal user account, NOT root — some pip
# paths look odd then (--break-system-packages warnings) and venv
# permissions under root are inconsistent.
if ! grep -q '^aifred:' "$MACHINE_ROOT/etc/passwd" 2>/dev/null; then
    echo "   Creating test user 'aifred' in container..."
    chroot "$MACHINE_ROOT" /bin/bash -c "
        useradd -m -s /bin/bash -G sudo aifred
        echo 'aifred ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/aifred-test
        chmod 440 /etc/sudoers.d/aifred-test
    "
fi
echo "   ✅ Container base config done"
echo ""

# ─── Done banner ────────────────────────────────────────────────
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Sandbox setup done.${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Next — run per test:"
echo "    sudo $(dirname "$0")/run-test.sh                  # full real run in sandbox"
echo "    sudo $(dirname "$0")/run-test.sh --dry-run        # dry-run in sandbox"
echo "    sudo $(dirname "$0")/run-test.sh --no-overwrite   # real run with --no-overwrite"
echo ""
echo "  Container root is at ${MACHINE_ROOT} (read-only base for every test)."
echo "  Each test runs with --ephemeral → changes disappear on exit."
