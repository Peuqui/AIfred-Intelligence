#!/bin/bash
# scripts/sandbox/run-test.sh
#
# Startet einen ephemeral systemd-nspawn-Container und fuehrt drin
# install-all.sh aus. Repo wird read-only reingespielt, drin wird per
# git clone ein "fresh clone" simuliert.
#
# Drei Use-Cases:
#
#   sudo ./scripts/sandbox/run-test.sh --dry-run [--no-overwrite]
#       → schnellster Test. Kein --boot (kein systemd), kein
#         systemctl-Aufruf. Validiert apt/pip/Skript-Logik fuer
#         Steps 1-2g + Service-File-Diffs ohne Disk-Writes.
#
#   sudo ./scripts/sandbox/run-test.sh
#       → vollstaendiger Real-Run mit systemd im Container.
#         Steps 1-2g + Service-Install + Service-Enable. Service-Start
#         klappt mit systemd-Bus im Container. Container-State ist
#         nach Exit weg (--ephemeral).
#
#   sudo ./scripts/sandbox/run-test.sh --shell
#       → interaktive Shell im Container, du fuehrst manuell aus.
#         Fuer Debugging / Tiefenpruefung.
#
# Alle nicht-Flag-Args werden an install-all.sh weitergereicht (z.B.
# --no-overwrite).

set -euo pipefail

# ─── Konfiguration ──────────────────────────────────────────────
MACHINE_NAME="${MACHINE_NAME:-aifred-test}"
MACHINE_ROOT="/var/lib/machines/${MACHINE_NAME}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ─── Sudo-Check ─────────────────────────────────────────────────
if [ "${EUID:-1}" -ne 0 ]; then
    echo -e "${RED}❌ Dieses Skript benoetigt sudo (systemd-nspawn).${NC}"
    echo "   sudo $0 $*"
    exit 1
fi

# ─── Container-Root-Check ───────────────────────────────────────
if [ ! -d "$MACHINE_ROOT" ] || [ ! -x "$MACHINE_ROOT/bin/bash" ]; then
    echo -e "${RED}❌ Container-Root fehlt oder ist defekt: ${MACHINE_ROOT}${NC}"
    echo "   Erst Setup ausfuehren:"
    echo "     sudo $(dirname "$0")/setup-nspawn-root.sh"
    exit 1
fi

# ─── Repo-Pfad ermitteln ────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ ! -d "$REPO_ROOT/.git" ] || [ ! -f "$REPO_ROOT/scripts/install-all.sh" ]; then
    echo -e "${RED}❌ Repo-Root sieht nicht nach AIfred aus: ${REPO_ROOT}${NC}"
    exit 1
fi

# ─── Mode + Args trennen ────────────────────────────────────────
# Modus-Wahl:
#   --shell   → interaktive Login-Shell (mit --boot)
#   --dry-run → kein --boot (schneller, kein systemd noetig)
#   default   → --boot (voller Real-Run mit systemd)
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

# Falls --dry-run nicht dabei ist → Real-Run mit systemd-Boot.
if [ "$MODE" = "auto" ]; then
    MODE="boot"
fi

# ─── Banner ─────────────────────────────────────────────────────
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  AIfred Sandbox-Run via systemd-nspawn --ephemeral${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "  Container:       ${MACHINE_NAME} (${MACHINE_ROOT})"
echo "  Repo (read-only): ${REPO_ROOT}"
echo "  Modus:           ${MODE}"
if [ "${#INSTALL_ARGS[@]}" -gt 0 ]; then
    echo "  install-all-Args: ${INSTALL_ARGS[*]}"
fi
echo -e "${YELLOW}  Alle Aenderungen sind nach Container-Exit weg (--ephemeral).${NC}"
echo ""

# ─── Gemeinsame nspawn-Args ─────────────────────────────────────
COMMON_NSPAWN=(
    --quiet
    --machine="${MACHINE_NAME}-$$"
    --directory="$MACHINE_ROOT"
    --ephemeral
    --bind-ro="$REPO_ROOT:/mnt/aifred-repo"
    --setenv=AIFRED_SANDBOX=1
)
# Netzwerk-Isolation:
#  * no-boot/dry-run:  --private-network (kein Internet noetig — apt
#                      + pip werden im dry-run uebersprungen — und der
#                      Schutz gegen "Host-Ports werden gesehen" ist
#                      wichtig fuer ehrliche Verifikations-Ergebnisse).
#  * boot/shell:       Default-Netzwerk (Host shared), damit apt update,
#                      ollama pull, playwright install Internet haben.
#                      Dafuer Caveat: Port-Probes "sehen" Host-Services.
#                      Reality-Check: das ist in v1 ein bewusster
#                      Trade-off, real geht's ohne Internet nicht.

# ─── Mode: shell (interaktiv mit systemd) ───────────────────────
if [ "$MODE" = "shell" ]; then
    echo -e "${GREEN}Starte interaktive Shell im Sandbox (booted).${NC}"
    echo "  Login: aifred / kein Passwort  (oder root)"
    echo "  Repo  : /mnt/aifred-repo  (read-only)"
    echo "  Beenden: 'sudo poweroff' im Container, oder Ctrl-] dreimal."
    echo ""
    exec systemd-nspawn "${COMMON_NSPAWN[@]}" --boot
fi

# ─── Mode: no-boot (schneller dry-run, kein systemd noetig) ─────
# nspawn ohne --boot fuehrt direkt einen Command aus. systemctl-Aufrufe
# wuerden in einem solchen Container zwar "Failed to connect to bus"
# liefern, aber im dry-run-Modus ruft install-services.sh kein systemctl
# auf — und Steps 1-2g in install-all.sh sind systemd-frei.
if [ "$MODE" = "no-boot" ]; then
    echo -e "${GREEN}Starte Sandbox ohne --boot (Dry-Run-tauglich, kein systemd, kein Netzwerk).${NC}"
    echo ""
    # Wir uebergeben das Runner-Snippet via stdin -> sudo -u aifred bash.
    # --bind-ro fuer das Repo macht das Skript verfuegbar.
    # --private-network: Sandbox sieht KEIN Host-Netzwerk (verhindert
    # dass Port-Probes laufende Host-Services faelschlich zaehlen).
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
    echo -e "${BLUE}  Sandbox-Exit (no-boot, dry-run): ${EXIT}${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    exit "$EXIT"
fi

# ─── Mode: boot (voller Real-Run mit systemd) ───────────────────
# Hier brauchen wir --boot fuer systemctl. Auto-Ausfuehrung im booted
# Container geht via systemd-run ueber den machined-Bus, ABER nicht
# vor dem Boot. Pragmatische Loesung: wir starten den Container im
# Hintergrund, warten auf "Reached target multi-user.target", dann
# systemd-run im Container.
#
# Einfacher v1: interaktiver Run mit Anleitung. Spaeter automatisieren.
echo -e "${GREEN}Starte vollen Real-Run mit --boot.${NC}"
echo ""
echo -e "${YELLOW}Hinweis fuer v1:${NC} der Auto-Run innerhalb von --boot ist in dieser"
echo "ersten Version manuell — der Container bootet, du fuehrst drin aus:"
echo ""
echo "  1. Login als aifred (kein Passwort)"
echo "  2. Folgende Befehle:"
echo ""
echo -e "     ${BLUE}git clone /mnt/aifred-repo ~/AIfred-Intelligence${NC}"
echo -e "     ${BLUE}cd ~/AIfred-Intelligence${NC}"
echo -e "     ${BLUE}bash scripts/install-all.sh ${INSTALL_ARGS[*]:-}${NC}"
echo ""
echo "  3. Container beenden:"
echo -e "     ${BLUE}sudo poweroff${NC}"
echo ""
echo "Druecke Enter zum Booten des Containers..."
read -r _

exec systemd-nspawn "${COMMON_NSPAWN[@]}" --boot
