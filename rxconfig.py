"""Reflex configuration for AIfred Intelligence"""

import reflex as rx
import os
from pathlib import Path
from reflex.plugins.sitemap import SitemapPlugin

# Load .env file for environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed, rely on system env vars

# ============================================================
# API URL Configuration
# ============================================================
# We use "0.0.0.0" as the hostname because Reflex's frontend JS
# has a SAME_DOMAIN_HOSTNAMES list that includes "0.0.0.0".
# When the browser sees this, it replaces it with window.location.hostname.
#
# This allows the same deployment to work via:
#   - https://narnia.spdns.de:8443 (nginx/external)
#   - https://narnia.spdns.de:443 (nginx/external alt port)
#   - http://192.168.0.252:3002 (direct/local from other machines)
#
# The frontend JS (state.js getBackendURL) does the magic replacement.

APP_NAME = "aifred"

# ============================================================
# Hot Reload Exclusions
# ============================================================
# Only the app package holds backend code; every other top-level directory
# is excluded (runtime data incl. the ChromaDB volume data/chromadb, venv,
# docs, scripts, models, ...). ChromaDB writes chroma.sqlite3 on every embed
# batch — watched, Granian would kill the worker mid-indexing.
# Taken from the directory listing, not a hand-kept list: Reflex stats every
# exclude path at startup (samefile) and the backend dies on a missing one.
# Hidden and "__" directories are skipped — Reflex excludes them itself, and
# .web may be rebuilt during startup.
# See: https://reflex.dev/docs/api-reference/environment-variables/
os.environ.setdefault(
    "REFLEX_HOT_RELOAD_EXCLUDE_PATHS",
    ":".join(
        entry.name
        for entry in Path(__file__).resolve().parent.iterdir()
        if entry.is_dir() and entry.name != APP_NAME and not entry.name.startswith((".", "__"))
    ),
)

config = rx.Config(
    app_name=APP_NAME,
    # Backend listens on all interfaces (the service passes the same via
    # --backend-host): over plain HTTP the frontend talks to <host>:8002
    # directly (see api_url below), so LAN access by IP needs it. External
    # access goes through nginx (HTTPS, port 443); the API itself requires
    # the login cookie.
    backend_host="0.0.0.0",
    backend_port=8002,
    frontend_port=3002,
    # Use 0.0.0.0 - browser JS will replace with actual hostname
    api_url="http://0.0.0.0:8002",
    # Frontend Sub-Path: nur wenn ENV gesetzt (MiniPC mit Nginx: "aifred", Dev: leer)
    frontend_path=os.getenv("AIFRED_FRONTEND_PATH", ""),
    # Disable sitemap plugin (not needed) — Reflex 0.8.28+ wants the
    # plugin class, not the dotted-string path.
    disable_plugins=[SitemapPlugin],
    # Hide "Built with Reflex" badge
    show_built_with_reflex=False,
)
