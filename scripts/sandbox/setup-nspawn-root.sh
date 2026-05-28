#!/bin/bash
# scripts/sandbox/setup-nspawn-root.sh
#
# Einmal-Setup: erzeugt einen Ubuntu-Noble-Container-Root unter
# /var/lib/machines/aifred-test/, der dann von run-test.sh per
# systemd-nspawn --ephemeral verwendet wird.
#
# Idempotent: re-runs prüfen ob alles da ist und überspringen
# bestehende Schritte.
#
# Benötigt sudo (debootstrap + Schreibrechte auf /var/lib/machines/).

set -euo pipefail

# ─── Konfiguration ──────────────────────────────────────────────
MACHINE_NAME="${MACHINE_NAME:-aifred-test}"
MACHINE_ROOT="/var/lib/machines/${MACHINE_NAME}"
UBUNTU_CODENAME="${UBUNTU_CODENAME:-noble}"     # = 24.04 LTS
UBUNTU_MIRROR="${UBUNTU_MIRROR:-http://archive.ubuntu.com/ubuntu}"

# Farben fuer Output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ─── Sudo-Check ─────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}❌ Dieses Skript benoetigt sudo:${NC}"
    echo "   sudo $0"
    exit 1
fi

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  AIfred Sandbox — nspawn Root Setup${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "  Container-Name:  ${MACHINE_NAME}"
echo "  Container-Root:  ${MACHINE_ROOT}"
echo "  Ubuntu-Release:  ${UBUNTU_CODENAME}"
echo ""

# ─── 1. Tools installieren ──────────────────────────────────────
echo -e "${BLUE}1/3 — Pruefe / installiere systemd-container + debootstrap${NC}"
NEEDED_PKGS=()
command -v systemd-nspawn >/dev/null || NEEDED_PKGS+=(systemd-container)
command -v debootstrap >/dev/null    || NEEDED_PKGS+=(debootstrap)

if [ "${#NEEDED_PKGS[@]}" -gt 0 ]; then
    echo "   Installiere: ${NEEDED_PKGS[*]}"
    apt-get update -qq
    apt-get install -y "${NEEDED_PKGS[@]}"
else
    echo "   ✅ systemd-nspawn + debootstrap bereits installiert"
fi
echo ""

# ─── 2. Container-Root erstellen ────────────────────────────────
echo -e "${BLUE}2/3 — Container-Root via debootstrap${NC}"
mkdir -p "$(dirname "$MACHINE_ROOT")"

if [ -d "$MACHINE_ROOT" ] && [ -x "$MACHINE_ROOT/bin/bash" ]; then
    echo -e "${YELLOW}   Container existiert bereits: ${MACHINE_ROOT}${NC}"
    echo "   Loeschen + neu bauen? (j/N)"
    read -p "   > " -n 1 -r REPLY
    echo ""
    if [[ $REPLY =~ ^[JjYy]$ ]]; then
        echo "   Loesche $MACHINE_ROOT ..."
        rm -rf "$MACHINE_ROOT"
    else
        echo "   ✅ Behalte bestehenden Container-Root"
    fi
fi

if [ ! -d "$MACHINE_ROOT" ]; then
    echo "   Baue Ubuntu-${UBUNTU_CODENAME}-Root (dauert ~3 Min, ~500 MB)..."
    # --variant=minbase: kleinstmoeglich. Spaeter im Container holt
    # install-all.sh die Pakete die es wirklich braucht.
    # --include=systemd,dbus,sudo: damit systemd als Init laeuft und
    # sudo im Container vorhanden ist (das install-Skript braucht's).
    debootstrap \
        --variant=minbase \
        --include=systemd,systemd-sysv,dbus,sudo,ca-certificates,locales,gnupg,wget,curl,git \
        "$UBUNTU_CODENAME" "$MACHINE_ROOT" "$UBUNTU_MIRROR"
fi
echo "   ✅ Container-Root: ${MACHINE_ROOT}"
echo ""

# ─── 3. Container-Konfig (Hostname, sudo-user) ──────────────────
echo -e "${BLUE}3/3 — Container-Grundkonfig${NC}"

# Hostname
echo "${MACHINE_NAME}" > "$MACHINE_ROOT/etc/hostname"

# /etc/hosts (debootstrap minbase laesst das leer)
cat > "$MACHINE_ROOT/etc/hosts" << EOF
127.0.0.1   localhost ${MACHINE_NAME}
::1         localhost ${MACHINE_NAME}
EOF

# Universe-Repository fuer apt aktivieren (debootstrap minbase hat nur main).
# AIfred braucht z.B. python3-venv aus main, aber spaeter ueber pip auch
# Pakete die ggf. universe-Buildtime-Deps haben.
cat > "$MACHINE_ROOT/etc/apt/sources.list" << EOF
deb ${UBUNTU_MIRROR} ${UBUNTU_CODENAME} main universe
deb ${UBUNTU_MIRROR} ${UBUNTU_CODENAME}-updates main universe
deb ${UBUNTU_MIRROR} ${UBUNTU_CODENAME}-security main universe
EOF

# Test-User 'aifred' anlegen (passwordless sudo).
# install-all.sh erwartet einen normalen User, NICHT root —
# manche pip-Pfade muten dann seltsam an (--break-system-packages
# Warnungen) und venv-Berechtigungen sind unter root inkonsistent.
if ! grep -q '^aifred:' "$MACHINE_ROOT/etc/passwd" 2>/dev/null; then
    echo "   Lege Test-User 'aifred' im Container an..."
    chroot "$MACHINE_ROOT" /bin/bash -c "
        useradd -m -s /bin/bash -G sudo aifred
        echo 'aifred ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/aifred-test
        chmod 440 /etc/sudoers.d/aifred-test
    "
fi
echo "   ✅ Container-Konfig fertig"
echo ""

# ─── Fertig-Banner ──────────────────────────────────────────────
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Sandbox-Setup fertig.${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Naechster Schritt — pro Test ausfuehren:"
echo "    sudo $(dirname "$0")/run-test.sh                  # voller Run im Sandbox"
echo "    sudo $(dirname "$0")/run-test.sh --dry-run        # dry-run im Sandbox"
echo "    sudo $(dirname "$0")/run-test.sh --no-overwrite   # echter Run mit no-overwrite"
echo ""
echo "  Container-Root ist bei ${MACHINE_ROOT} (read-only-Basis fuer alle Tests)."
echo "  Jeder Test laeuft mit --ephemeral → Aenderungen verschwinden nach Exit."
