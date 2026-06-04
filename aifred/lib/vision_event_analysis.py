"""Vigilantia — VLM-Analyse für bereits gespeicherte Events.

Nimmt ein Event aus dem VisionStore, lädt das zugehörige Frame-JPEG
von Disk, schickt es durch das VLM und schreibt die Beschreibung
zurück in ``events.classification["description"]``.

Wird vom „VLM analysieren"-Button im Casus aufgerufen (Single-Event)
und vom Bulk-Worker für Cluster-Repräsentanten.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .vision_phash import hamming_distance, phash_bytes
from .vision_store import VisionStore

logger = logging.getLogger(__name__)

# Prompts liegen NICHT im Code, sondern in prompts/{lang}/vision/ und werden
# über prompt_loader geladen (vision_event_single.txt / _sequence.txt).


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
    from .prompt_loader import get_vision_event_single_prompt
    from .config import VISION_VLM_MAX_PIXELS
    from .vision_analyzer import downscale_for_vlm
    target_prompt = (prompt or get_vision_event_single_prompt()).strip()
    image_b64 = base64.b64encode(
        downscale_for_vlm(image_bytes, VISION_VLM_MAX_PIXELS)
    ).decode("ascii")
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


def _select_keyframes(
    items: list[tuple[datetime, int, bytes, str]], max_frames: int
) -> list[tuple[datetime, int, bytes, str]]:
    """Wählt bis zu ``max_frames`` Keyframes: die Zeitspanne wird in
    ``max_frames`` gleich große Fächer geteilt, aus jedem (nicht-leeren) Fach
    das Frame mit der größten pHash-Distanz zum zuletzt gewählten genommen.
    Regelmäßig über die Zeit verteilt UND an den Änderungspunkten. Items
    müssen nach Zeit aufsteigend sortiert sein; Reihenfolge bleibt erhalten."""
    if max_frames <= 0:
        return []
    if len(items) <= max_frames:
        return items
    t0, t1 = items[0][0], items[-1][0]
    span = (t1 - t0).total_seconds() or 1.0
    selected: list[tuple[datetime, int, bytes, str]] = []
    last_phash: int | None = None
    for b in range(max_frames):
        lo = t0 + timedelta(seconds=span * b / max_frames)
        hi = t0 + timedelta(seconds=span * (b + 1) / max_frames)
        # Letztes Fach inklusiv bis t1, sonst halb-offen [lo, hi).
        if b == max_frames - 1:
            bucket = [it for it in items if it[0] >= lo]
        else:
            bucket = [it for it in items if lo <= it[0] < hi]
        if not bucket:
            continue
        if last_phash is None:
            pick = bucket[0]
        else:
            pick = max(bucket, key=lambda it: hamming_distance(it[1], last_phash))  # type: ignore[arg-type]
        selected.append(pick)
        last_phash = pick[1]
    return selected


async def analyze_cluster_with_vlm(
    event_ids: list[int],
    *,
    prompt: str | None = None,
    store: VisionStore | None = None,
    model: str | None = None,
    max_frames: int | None = None,
) -> str:
    """Beschreibt ein Vorkommnis (Cluster) als zeitliche Bildfolge.

    Lädt die Frames der Cluster-Mitglieder, wählt pHash-basiert die
    aussagekräftigsten Keyframes (``_select_keyframes``) und gibt sie als
    Sequenz ans VLM — so sieht es den Ablauf (Tür auf, Personen kommen),
    nicht nur ein statisches Einzelbild. Bei einem Mitglied identisch zur
    Einzelbild-Analyse. Persistiert die Beschreibung am Repräsentanten
    (``event_ids[0]``); der Bulk-Worker verteilt sie auf alle Mitglieder.
    """
    from .config import VISION_DESCRIBE_MAX_FRAMES
    from .frame_sources import Frame
    from .prompt_loader import get_vision_event_sequence_prompt
    from .vision_analyzer import analyze_sequence
    from .vision_prewarm import get_active_vlm_model

    if not event_ids:
        raise ValueError("analyze_cluster_with_vlm requires at least one event")
    store = store or VisionStore()
    cap = max_frames or VISION_DESCRIBE_MAX_FRAMES

    loaded: list[tuple[datetime, int, bytes, str]] = []
    for eid in event_ids:
        ev = store.get_event(int(eid))
        if not ev:
            continue
        fp = str(ev.get("frame_path") or "")
        if not fp or not Path(fp).exists():
            continue
        try:
            data = Path(fp).read_bytes()
            ph = phash_bytes(data)
        except Exception:  # noqa: BLE001
            continue
        try:
            ts = datetime.fromisoformat(str(ev["timestamp"]))
        except (TypeError, ValueError):
            ts = datetime.now()
        loaded.append((ts, ph, data, str(ev["source_id"])))

    if not loaded:
        raise ValueError("cluster has no readable frames on disk")
    loaded.sort(key=lambda x: x[0])
    keyframes = _select_keyframes(loaded, cap)

    frames = [
        Frame(source_id=src, timestamp=ts, image_bytes=data)
        for (ts, _ph, data, src) in keyframes
    ]

    target_model = model or get_active_vlm_model()
    if not target_model:
        raise RuntimeError("no VLM model configured")
    target_prompt = (prompt or get_vision_event_sequence_prompt()).strip()

    result = await analyze_sequence(frames, target_prompt, model=str(target_model))
    description = (result.text or "").strip()
    if not description:
        raise RuntimeError("VLM returned empty response")

    repr_id = int(event_ids[0])
    target = store.get_event(repr_id)
    existing = dict((target or {}).get("classification") or {})
    existing["description"] = description
    existing["analyzed_at"] = datetime.now().isoformat(timespec="seconds")
    existing["analyzed_by"] = str(target_model)
    existing["frames_analyzed"] = len(frames)
    _update_event_classification(store, repr_id, existing)

    logger.info(
        "analyze_cluster_with_vlm: repr=%d members=%d keyframes=%d model=%s len=%d",
        repr_id, len(event_ids), len(frames), target_model, len(description),
    )
    return description
