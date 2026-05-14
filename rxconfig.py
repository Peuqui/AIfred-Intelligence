"""Reflex configuration for AIfred Intelligence"""

import reflex as rx
import os
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

# Environment mode (affects Reflex optimizations)
is_prod = os.getenv("AIFRED_ENV", "dev") == "prod"

# ============================================================
# Hot Reload Exclusions
# ============================================================
# Exclude directories without Python code from hot reload to prevent
# unnecessary recompilation when runtime files change (e.g., TTS audio).
# See: https://reflex.dev/docs/api-reference/environment-variables/
os.environ.setdefault(
    "REFLEX_HOT_RELOAD_EXCLUDE_PATHS",
    ":".join([
        "data",              # Runtime data (TTS audio, sessions, etc.)
        "logs",              # Log files
        "docker",            # Docker configs
        "docs",              # Documentation
        "prompts",           # Prompt templates
        "assets",            # Static assets
        "Bilder",            # Images
        "piper_models",      # TTS models
        "uploaded_files",    # User uploads
        "aifred_vector_cache",  # ChromaDB cache
        "systemd",           # Service configs
        "scripts",           # Utility scripts
        "__pycache__",       # Python bytecode cache
    ])
)

config = rx.Config(
    app_name="aifred",
    # Backend listens only on localhost; all external access (LAN/WAN)
    # goes through the nginx reverse proxy on the same host. This keeps
    # the unauthenticated /api/* routes off the LAN.
    backend_host="127.0.0.1",
    backend_port=8002,
    frontend_port=3002,
    frontend_host="0.0.0.0",  # Frontend on all interfaces
    # Use 0.0.0.0 - browser JS will replace with actual hostname
    api_url="http://0.0.0.0:8002",
    # Frontend Sub-Path: nur wenn ENV gesetzt (MiniPC mit Nginx: "aifred", Dev: leer)
    frontend_path=os.getenv("AIFRED_FRONTEND_PATH", ""),
    env=rx.Env.PROD if is_prod else rx.Env.DEV,
    # Disable sitemap plugin (not needed) — Reflex 0.8.28+ wants the
    # plugin class, not the dotted-string path.
    disable_plugins=[SitemapPlugin],
    # Performance optimizations
    compile_timeout=90,  # Increase compilation timeout (default: 60s)
    # Hide "Built with Reflex" badge
    show_built_with_reflex=False,
    # KaTeX for LaTeX rendering in Markdown (locally hosted)
    stylesheets=[
        "/katex/katex.min.css",
    ],
)
