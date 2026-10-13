"""Status of model, speech recognition and speech output for the page's status line.

The page gets changes pushed over the browser bus (kind ``service_status``) and asks this
endpoint for the current state when it opens and when the user presses the refresh button
(``refresh=true`` reads STT and TTS from their services first).
"""

import json
from typing import Any

from fastapi import Query

from ..service_status import ServiceSnapshot, get_monitor
from .app import api_app
from .browser_bus import _browser_sse_queues, browser_push

SERVICE_STATUS_KIND = "service_status"


def _push_to_connected_pages(snapshot: ServiceSnapshot) -> None:
    payload = json.dumps(snapshot.as_json())
    for session_id in list(_browser_sse_queues):
        browser_push(session_id, SERVICE_STATUS_KIND, payload)


get_monitor().add_listener(_push_to_connected_pages)


@api_app.get("/service-status", tags=["System"])
async def service_status(refresh: bool = Query(False)) -> dict[str, Any]:
    """Model, STT and TTS state with the seconds left until each unloads itself."""
    monitor = get_monitor()
    if refresh:
        await monitor.refresh("stt")
        await monitor.refresh("tts")
    return monitor.snapshot().as_json()
