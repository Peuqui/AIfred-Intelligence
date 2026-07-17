"""
AIfred REST API Package

Provides HTTP API endpoints for remote control of AIfred.
Uses FastAPI and is mounted at /api by Reflex's app.api.mount().

API Documentation: http://localhost:8002/api/docs

Endpoints (all prefixed with /api):
- GET  /health              - Health check
- GET  /settings            - Get all settings
- PATCH /settings           - Update settings
- GET  /models              - List available models
- GET  /sessions            - List all sessions
- POST /chat/inject         - Inject message into browser session
- GET  /chat/status         - Get chat/generation status
- POST /chat/clear          - Clear chat history
- GET  /chat/history        - Get chat history
- POST /system/restart-ollama   - Restart Ollama service
- POST /system/restart-aifred   - Restart AIfred service
- POST /system/clear-vectordb   - Clear Vector DB
- POST /system/reset-defaults   - Reset to default settings

Package layout: app.py holds the FastAPI app + middleware; the router
modules below register their endpoints on import (same registration
order as the former single-file api.py).
"""

from .app import API_VERSION, api_app, get_api_app, get_global_backend_state

# Router modules — importing them registers their routes on api_app.
# Order matters: it mirrors the original single-file section order.
from . import core  # noqa: E402
from . import chat  # noqa: E402
from . import system  # noqa: E402
from . import browser_bus  # noqa: E402
from . import agents  # noqa: E402
from . import audio  # noqa: E402
from . import vision  # noqa: E402

from .browser_bus import browser_push, browser_queue_clear  # noqa: E402

__all__ = [
    "API_VERSION",
    "api_app",
    "get_api_app",
    "get_global_backend_state",
    "browser_push",
    "browser_queue_clear",
    "core",
    "chat",
    "system",
    "browser_bus",
    "agents",
    "audio",
    "vision",
]
