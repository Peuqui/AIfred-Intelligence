"""
AIfred REST API Package

AIfred's HTTP API (FastAPI), mounted at /api by the server (aifred/server.py); documentation at /api/docs.

Endpoints (all prefixed with /api):
- GET  /health              - Health check
- GET  /settings            - Get all settings
- PATCH /settings           - Update settings
- GET  /models              - List available models
- GET  /sessions            - The logged-in user's sessions (sessions.py: open, create, delete,
                               configure, send, stop, events)
- POST /auth/login          - Login for the Vue app (sets the login cookie)
- GET  /agents/choices      - The agents' quick choices (model, persona, reasoning, thinking,
                               speed), PATCH /agents/{id}/choices changes one row
- POST /chat/inject         - A message into a session as the user's input (token)
- GET  /chat/status         - Get chat/generation status
- POST /chat/clear          - Clear chat history
- GET  /chat/history        - Get chat history
- GET  /audio/announce/rooms - Rooms an announcement can go to (Bearer token)
- POST /audio/announce      - Spoken announcement to FreeEcho.2, no LLM (Bearer token)
- GET  /tts/entries         - The TTS escalation list in order, with live status (Bearer token)
- GET  /gpu/holders         - What AIfred holds on the GPUs (the face worker), for GPU-Orc (Bearer token)
- POST /gpu/release         - Release it: end the face worker, proven by nvidia-smi (Bearer token)
- GET  /documents/source/{key}/file/{id} - Download from a further document source (login cookie)
- GET  /service-status     - Model, STT and TTS state with time left (login cookie)
- GET  /backend             - Backend in use, the ones the GPUs can run, cloud providers, GPUs
- PUT  /backend             - Switch the backend; PUT /backend/cloud-provider the cloud's provider
- GET  /calibration         - Calibration of the agents' models, matrix of AIfred's model
- POST /system/restart-backend  - Restart the backend's service (Ollama or llama-swap)
- POST /system/restart-aifred   - Restart AIfred service
- POST /system/reset-defaults   - Reset to default settings

Package layout: app.py holds the FastAPI app + middleware; the router
modules below register their endpoints on import (same registration
order as the former single-file api.py).
"""

from .app import API_VERSION, api_app

# Router modules — importing them registers their routes on api_app.
# Order matters: it mirrors the original single-file section order.
from . import core
from . import chat
from . import system
from . import agents
from . import audio
from . import announce
from . import vision
from . import tts
from . import documents
from . import service_status
from . import sessions
from . import login
from . import agent_choices
from . import memory
from . import jobs
from . import speech
from . import cameras
from . import vigilantia
from . import casus
from . import enrollment
from . import audio_library
from . import plugins
from . import housekeeping
from . import backend
from . import files
from . import gpu
from . import app_shell  # last: its /app mount must follow every /app route


__all__ = [
    "API_VERSION",
    "agents",
    "announce",
    "api_app",
    "audio",
    "chat",
    "core",
    "documents",
    "service_status",
    "system",
    "tts",
    "vision",
]
