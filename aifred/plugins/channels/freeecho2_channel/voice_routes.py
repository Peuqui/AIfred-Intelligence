"""Sprach-Weichen des FreeEcho.2-Kanals: ein Wake-Wort, dessen Äußerung an einen
externen Dienst geht, statt durch AIfreds LLM-Pipeline (z. B. Agent-Orc).

Konfiguriert in ``voice_routes.json`` neben diesem Modul (maschinenlokal, siehe
``voice_routes.example.json``): Schlüssel = der Agentenname aus dem Wake-Frame des Pucks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

ROUTES_FILE = Path(__file__).parent / "voice_routes.json"
REQUIRED_ROUTE_KEYS = ("url", "token_file", "timeout_seconds")


def load_voice_routes(path: Path = ROUTES_FILE) -> dict[str, dict[str, Any]]:
    """Die Routentabelle (Agentenname → Ziel). Fail-loud bei einem unvollständigen
    Eintrag: ein Tippfehler darf keine Route still verschwinden lassen."""
    if not path.exists():
        return {}
    routes: dict[str, dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    for name, route in routes.items():
        missing = [key for key in REQUIRED_ROUTE_KEYS if key not in route]
        if missing:
            raise ValueError(f"voice route '{name}': missing keys {missing}")
    return routes


async def forward_voice(route: dict[str, Any], room: str, text: str, wav_path: str) -> dict[str, Any]:
    """Die erkannte Äußerung samt Aufnahme an das Ziel schicken und dessen JSON-Antwort
    liefern: multipart/form-data mit den Feldern ``room``, ``text`` und der Datei ``audio`` (die
    WAV des Pucks unverändert, 16 kHz, mono, 16 Bit), Bearer-Token aus der Token-Datei der
    Route. Raises bei Netzwerkfehler, fehlendem Token und jedem Status außer 2xx."""
    token = Path(route["token_file"]).expanduser().read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError(f"token file {route['token_file']} is empty")
    async with httpx.AsyncClient(timeout=route["timeout_seconds"]) as client:
        response = await client.post(
            route["url"],
            headers={"Authorization": f"Bearer {token}"},
            data={"room": room, "text": text},
            files={"audio": ("aufnahme.wav", Path(wav_path).read_bytes(), "audio/wav")},
        )
    response.raise_for_status()
    outcome: dict[str, Any] = response.json()
    return outcome
