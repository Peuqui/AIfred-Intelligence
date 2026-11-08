#!/bin/bash
#
# AIfred Intelligence - build the Vue interface
#
# frontend/ → frontend/dist, which the server (python -m aifred.server) serves under /api/app/.
# frontend/dist is not in the repository: run this after the first checkout and after every update
# of frontend/, then restart the service. Exactly the locked packages (npm ci, package-lock.json).
# Needs Node.js ^20.19 or >= 22.12 (Vite 8; one in frontend/node comes first, see node-env.sh), and
# the venv first: the build takes the API's types from the server's OpenAPI description
# (npm run api-types, scripts/export_openapi.py).
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
FRONTEND_DIR="$PROJECT_DIR/frontend"

if [ ! -x "$PROJECT_DIR/venv/bin/python" ]; then
    echo "❌ Python venv not found: $PROJECT_DIR/venv — run ./scripts/setup-python.sh first."
    exit 1
fi

# shellcheck source=node-env.sh
source "$SCRIPT_DIR/node-env.sh"
if ! node_usable; then
    if command -v node &>/dev/null; then
        echo "❌ Node.js $(node --version) is too old to build the interface: ^20.19 or >= 22.12 is needed (Vite 8)."
    else
        echo "❌ Node.js / npm not found — needed to build the interface (^20.19 or >= 22.12)."
    fi
    echo "   ./scripts/install-all.sh installs it (system-wide or into frontend/node);"
    echo "   see docs/en/guides/deployment.md → 1. Prerequisites."
    exit 1
fi

echo "📦 Node.js $(node --version), npm $(npm --version) — building $FRONTEND_DIR"
cd "$FRONTEND_DIR"
npm ci --no-audit --no-fund
npm run build
echo "✅ Interface built: $FRONTEND_DIR/dist (restart the service to serve it)"
