"""Vigilantia — VLM-Analyse für bereits gespeicherte Events.

Nimmt ein Event aus dem VisionStore, lädt das zugehörige Frame-JPEG
von Disk, schickt es durch das VLM und schreibt die Beschreibung
zurück in ``events.classification["description"]``.

Wird vom „VLM analysieren"-Button im Casus aufgerufen (Single-Event)
und vom Bulk-Worker für Cluster-Repräsentanten.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from .vision_store import VisionStore

logger = logging.getLogger(__name__)

# Pragmatischer Default-Prompt für die nachträgliche Analyse —
# bewusst kurz, weil ein Bild meist ~1 Satz wert ist. Caller kann
# überschreiben.
DEFAULT_EVENT_PROMPT = (
    "Beschreibe in einem Satz, was auf diesem Bild zu sehen ist. "
    "Wenn Personen sichtbar sind, beschreibe deren Aktion knapp, "
    "ohne Details zum Aussehen."
)


def _load_frame_bytes(frame_path: str) -> bytes:
    """Frame-JPEG von Disk lesen. Wirft FileNotFoundError wenn weg
    (Cleanup-Task hat zugeschlagen)."""
    p = Path(frame_path)
    if not p.exists():
        raise FileNotFoundError(f"frame not on disk: {frame_path}")
    return p.read_bytes()


async def analyze_event_with_vlm(
    event_id: int,
    *,
    prompt: str | None = None,
    store: VisionStore | None = None,
    model: str | None = None,
) -> str:
    """Single-Event-Analyse. Lädt das Frame, ruft VLM, persistiert
    Beschreibung in ``classification.description``, returnt den Text.

    Idempotent: existiert schon eine description, wird sie überschrieben
    (User-Wunsch: nochmal analysieren = neue Beschreibung).

    ``model``: optional, sonst aus plugins/tools/vision/settings.json.
    """
    store = store or VisionStore()
    # Direkter Primary-Key-Lookup — der frühere Scan der jüngsten 1000
    # Events fand alles Ältere nicht („event not found") und ließ damit
    # das gesamte Backlog jenseits der letzten 1000 Events unbeschrieben.
    target = store.get_event(int(event_id))
    if not target:
        raise ValueError(f"event {event_id} not found")

    frame_path = target.get("frame_path") or ""
    if not frame_path:
        raise ValueError(f"event {event_id} has no frame_path on disk")

    image_bytes = _load_frame_bytes(frame_path)

    # VLM-Call via Ollama
    from ollama import AsyncClient
    from .config import VLM_NUM_CTX, resolve_vlm_host
    from .vision_prewarm import get_active_vlm_model
    import base64
    target_model = model or get_active_vlm_model()
    if not target_model:
        raise RuntimeError("no VLM model configured")
    target_prompt = (prompt or DEFAULT_EVENT_PROMPT).strip()
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    client = AsyncClient(host=resolve_vlm_host())
    response = await client.generate(
        model=str(target_model),
        prompt=target_prompt,
        images=[image_b64],
        options={"num_ctx": int(VLM_NUM_CTX)},
        keep_alive="30m",
        stream=False,
    )
    description = str(getattr(response, "response", "") or response.get("response", "")).strip()
    if not description:
        raise RuntimeError("VLM returned empty response")

    # Beschreibung in classification persistieren — wir mergen mit
    # bestehender classification (crop_url, bbox etc. bleiben erhalten).
    existing = dict(target.get("classification") or {})
    existing["description"] = description
    existing["analyzed_at"] = datetime.now().isoformat(timespec="seconds")
    existing["analyzed_by"] = str(target_model)
    _update_event_classification(store, int(event_id), existing)

    logger.info(
        "analyze_event_with_vlm: event=%d model=%s len=%d chars",
        event_id, target_model, len(description),
    )
    return description


def _update_event_classification(
    store: VisionStore, event_id: int, classification: dict[str, Any]
) -> None:
    """Direkt ins SQL — VisionStore.add_event ist append-only, wir
    brauchen ein UPDATE für classification."""
    import json
    with store._conn() as conn:  # noqa: SLF001
        conn.execute(
            "UPDATE events SET classification = ? WHERE id = ?",
            (json.dumps(classification), event_id),
        )
