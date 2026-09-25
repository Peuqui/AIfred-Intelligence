#!/bin/bash
# Patch vite.config.js to allow external host access + dedupe shared deps.
#
# WHY THIS EXISTS:
#   Reflex generates .web/vite.config.js on every frontend rebuild. Two
#   problems for production deployments behind a reverse proxy:
#
#   1. Vite refuses requests with a Host header that's not "localhost".
#      AIFRED_ALLOWED_HOST adds your domain to allowedHosts so e.g.
#      https://example.com works.
#
#   2. Reflex splits shared libs (react-helmet, react, …) into multiple
#      lazy chunks. Without dedupe, react-helmet ends up duplicated, the
#      browser crashes with "Identifier 'scrollState' has already been
#      declared", the frontend worker dies and kills running indexer jobs.
#
# WHEN IT RUNS:
#   As ExecStartPre of aifred-intelligence.service on every start
#   (idempotent). Before the first frontend build .web/vite.config.js does
#   not exist yet; the script then does nothing.
#
# CONFIGURATION:
#   AIFRED_ALLOWED_HOST in .env (loaded by the service unit). Unset or empty
#   skips the allowedHosts step — enough for local-only access.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VITE_CONFIG="$PROJECT_DIR/.web/vite.config.js"
ALLOWED_HOST="${AIFRED_ALLOWED_HOST:-}"

if [ ! -f "$VITE_CONFIG" ]; then
    echo "ℹ️  $VITE_CONFIG not found — Reflex has not built the frontend yet."
    exit 0
fi

# 1. allowedHosts — only patch if a host is configured AND not already present.
if [ -n "$ALLOWED_HOST" ] && ! grep -q "allowedHosts" "$VITE_CONFIG"; then
    sed -i "s/hmr: true,/hmr: true,\n    allowedHosts: [\"${ALLOWED_HOST}\", \"localhost\", \"127.0.0.1\"],/" "$VITE_CONFIG"
    echo "✅ Patched vite.config.js with allowedHosts (${ALLOWED_HOST})"
fi

# 2. dedupe — always applied, prevents react-helmet duplication.
if ! grep -q "dedupe:" "$VITE_CONFIG"; then
    sed -i 's|mainFields: \["browser", "module", "jsnext"\],|mainFields: ["browser", "module", "jsnext"],\n    dedupe: ["react-helmet", "react", "react-dom", "@radix-ui/themes", "@emotion/react"],|' "$VITE_CONFIG"
    echo "✅ Patched vite.config.js with dedupe (react-helmet, react, react-dom, …)"
fi
