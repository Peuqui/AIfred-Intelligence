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
- GET  /audio/announce/rooms - Rooms an announcement can go to (Bearer token)
- POST /audio/announce      - Spoken announcement to FreeEcho.2, no LLM (Bearer token)
- GET  /tts/entries         - The TTS escalation list in order, with live status (Bearer token)
- GET  /documents/source/{key}/file/{id} - Download from a further document source (login cookie)
- GET  /service-status     - Model, STT and TTS state with time left (login cookie)
- POST /system/restart-ollama   - Restart Ollama service
- POST /system/restart-aifred   - Restart AIfred service
- POST /system/reset-defaults   - Reset to default settings

Package layout: app.py holds the FastAPI app + middleware; the router
modules below register their endpoints on import (same registration
order as the former single-file api.py).
"""

from .app import API_VERSION, api_app, get_api_app, get_global_backend_state

# Router modules — importing them registers their routes on api_app.
# Order matters: it mirrors the original single-file section order.
from . import core
from . import chat
from . import system
from . import browser_bus
from . import agents
from . import audio
from . import announce
from . import vision
from . import tts
from . import documents
from . import service_status

from .browser_bus import browser_push, browser_queue_clear

__all__ = [
    "API_VERSION",
    "agents",
    "announce",
    "api_app",
    "audio",
    "browser_bus",
    "browser_push",
    "browser_queue_clear",
    "chat",
    "core",
    "documents",
    "get_api_app",
    "get_global_backend_state",
    "service_status",
    "system",
    "tts",
    "vision",
]
