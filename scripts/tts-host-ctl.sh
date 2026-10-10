#!/usr/bin/env bash
# Steuerung der TTS-Container auf einem anderen Rechner (TTS-Host) durch AIfred.
#
# AIfred ruft dieses Skript per SSH auf — mit einem eigenen Schlüssel, der in
# ~/.ssh/authorized_keys des TTS-Hosts auf genau dieses Skript festgelegt ist:
#
#   command="/pfad/zu/AIfred-Intelligence/scripts/tts-host-ctl.sh",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding ssh-ed25519 AAAA… aifred-tts-host
#
# Der Befehl kommt dann in SSH_ORIGINAL_COMMAND an, direkt aufgerufen in "$@":
#
#   start <dienst>   Container von docker/tts/<dienst> starten (up -d)
#   stop [<dienst>]  diesen bzw. alle TTS-Container dieses Repos stoppen (VRAM frei)
#   status           laufende TTS-Container auflisten
#
# <dienst> ist ein Verzeichnis unter docker/tts/ mit docker-compose.yml
# (z. B. xtts, qwen3-tts). Die Karte und die Leerlaufzeit stehen in der .env
# neben der jeweiligen docker-compose.yml (TTS_GPU_UUID, *_KEEP_ALIVE).
set -euo pipefail

TTS_DIR="$(cd "$(dirname "$(readlink -f "$0")")/../docker/tts" && pwd)"

if [[ -n "${SSH_ORIGINAL_COMMAND:-}" ]]; then
    read -r -a args <<< "$SSH_ORIGINAL_COMMAND"
else
    args=("$@")
fi

action="${args[0]:-}"
service="${args[1]:-}"

compose_files() {
    find "$TTS_DIR" -mindepth 2 -maxdepth 2 -name docker-compose.yml | sort
}

require_service() {
    if [[ ! "$service" =~ ^[a-z0-9-]+$ || ! -f "$TTS_DIR/$service/docker-compose.yml" ]]; then
        echo "unknown TTS service: '$service'" >&2
        exit 2
    fi
}

case "$action" in
    start)
        require_service
        docker compose -f "$TTS_DIR/$service/docker-compose.yml" up -d
        ;;
    stop)
        if [[ -n "$service" ]]; then
            require_service
            docker compose -f "$TTS_DIR/$service/docker-compose.yml" stop
        else
            while read -r compose; do
                docker compose -f "$compose" stop
            done < <(compose_files)
        fi
        ;;
    status)
        while read -r compose; do
            docker compose -f "$compose" ps --status running --format '{{.Service}}'
        done < <(compose_files)
        ;;
    *)
        echo "usage: tts-host-ctl.sh start <service> | stop [<service>] | status" >&2
        exit 2
        ;;
esac
