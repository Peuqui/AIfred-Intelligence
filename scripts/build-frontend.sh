#!/bin/bash
#
# AIfred Intelligence - build the Vue interface
#
# frontend/ → frontend/dist, which the server (python -m aifred.server) serves under /api/app/.
# frontend/dist is not in the repository: run this after the first checkout and after every update
# of frontend/, then restart the service. Exactly the locked packages (npm ci, package-lock.json).
# Needs Node.js ^20.19 or >= 22.12 (Vite 8), and the venv first: the build takes the API's types from
# the server's OpenAPI description (npm run api-types, scripts/export_openapi.py).
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
FRONTEND_DIR="$PROJECT_DIR/frontend"

if [ ! -x "$PROJECT_DIR/venv/bin/python" ]; then
    echo "❌ Python venv not found: $PROJECT_DIR/venv — run ./scripts/setup-python.sh first."
    exit 1
fi

if ! command -v node &>/dev/null || ! command -v npm &>/dev/null; then
    echo "❌ Node.js / npm not found — needed to build the interface (Node ^20.19 or >= 22.12)."
    echo "   See docs/en/guides/deployment.md → 1. Prerequisites."
    exit 1
fi

# The engines field of Vite decides; checked here so an old Node fails with a clear message, not deep in the build.
if ! node -e '
    const [major, minor] = process.versions.node.split(".").map(Number);
    process.exit((major === 20 && minor >= 19) || (major === 22 && minor >= 12) || major > 22 ? 0 : 1);
'; then
    echo "❌ Node.js $(node --version) is too old to build the interface: ^20.19 or >= 22.12 is needed (Vite 8)."
    exit 1
fi

echo "📦 Node.js $(node --version), npm $(npm --version) — building $FRONTEND_DIR"
cd "$FRONTEND_DIR"
npm ci --no-audit --no-fund
npm run build
echo "✅ Interface built: $FRONTEND_DIR/dist (restart the service to serve it)"
