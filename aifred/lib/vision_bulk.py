"""Headless bulk-describe orchestration — the single source of truth for
"give every undescribed vision event a VLM description".

The work is always the same: take motion/face events that have no
description yet, collapse near-duplicate frames into clusters (pHash +
time-bucket, see :mod:`vision_cluster`) so the VLM runs once per
*happening* instead of once per frame, describe the representative of
each cluster, and apply that text to all cluster members.

Three callers share this one function so the logic lives in exactly one
place:

* the Casus UI bulk button (passes callbacks to drive its progress bar
  and cancel flag),
* the nightly garbage-collection task (headless, no callbacks),
* the on-demand path from the conversational query tool.

State integration is kept out of here entirely — callers bridge progress
and cancellation via the two optional callbacks.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Awaitable, Callable

from .logging_utils import log_message

logger = logging.getLogger(__name__)

# progress_cb(processed, total, message) — message is None when only the
# counter advances, a string when the human-readable status changes.
ProgressCb = Callable[[int, int, "str | None"], Awaitable[None]]
# cancel_cb() -> True to stop after the current cluster.
CancelCb = Callable[[], Awaitable[bool]]

# Same event types the Casus worker analyses — only these carry a
# frame_path worth sending to the VLM.
DEFAULT_EVENT_TYPES = ["motion", "face_known", "face_unsure", "face_unknown"]


@dataclass
class BulkDescribeResult:
    """Outcome of a bulk-describe run, enough for any caller to render a
    status message without knowing the internals."""

    total_events: int = 0
    total_clusters: int = 0
    processed: int = 0
    failed: int = 0
    cancelled: bool = False
    aborted_vram: bool = False
    vram_message: str = ""
    skipped: bool = False  # another bulk run was already in flight


# Single-flight guard: only one bulk-describe at a time across all callers
# (Casus button, nightly run, on-demand). A second concurrent call would just
# duplicate VLM work and race on cluster_id writes — so it is skipped. Safe
# without a lock: asyncio doesn't preempt between the check and the set (no
# await in between), and the flag is always cleared in a finally.
_bulk_running = False


async def run_bulk_describe(
    *,
    store: object | None = None,
    source_id: str | None = None,
    event_types: list[str] | None = None,
    since: "object | None" = None,
    until: "object | None" = None,
    limit: int | None = None,
    check_vram: bool = True,
    progress_cb: ProgressCb | None = None,
    cancel_cb: CancelCb | None = None,
) -> BulkDescribeResult:
    """Describe all undescribed events (clustered) and return a summary.

    Single-flight: if a bulk run is already in progress this call returns
    immediately with ``skipped=True`` instead of duplicating the work.

    ``since`` / ``until`` (datetimes) scope the work to a time window — the
    on-demand chat hook passes the queried span. ``limit=None`` (default)
    describes everything in scope. ``check_vram`` runs a VRAM pre-check
    (only the interactive Casus button uses it; nightly/on-demand pass
    False and let Ollama manage its own VRAM). ``progress_cb`` /
    ``cancel_cb`` are optional — headless callers pass neither.
    """
    global _bulk_running
    if _bulk_running:
        log_message("🖌️ bulk-describe: already running — skipping concurrent run")
        return BulkDescribeResult(skipped=True)
    _bulk_running = True
    try:
        return await _bulk_describe_impl(
            store=store, source_id=source_id, event_types=event_types,
            since=since, until=until, limit=limit, check_vram=check_vram,
            progress_cb=progress_cb, cancel_cb=cancel_cb,
        )
    finally:
        _bulk_running = False


async def _bulk_describe_impl(
    *,
    store: object | None = None,
    source_id: str | None = None,
    event_types: list[str] | None = None,
    since: "object | None" = None,
    until: "object | None" = None,
    limit: int | None = None,
    check_vram: bool = True,
    progress_cb: ProgressCb | None = None,
    cancel_cb: CancelCb | None = None,
) -> BulkDescribeResult:
    from .vision_cluster import cluster_events, write_clusters
    from .vision_event_analysis import analyze_event_with_vlm
    from .vision_store import VisionStore

    store = store or VisionStore()
    event_types = event_types or list(DEFAULT_EVENT_TYPES)

    if check_vram:
        try:
            from .vision_vram_check import check_vlm_fits
            vram = await check_vlm_fits()
            if not vram.fits:
                return BulkDescribeResult(
                    aborted_vram=True, vram_message=vram.message
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("VRAM check failed (continuing anyway): %s", e)

    events = store.list_events_without_description(  # type: ignore[attr-defined]
        source_id=source_id, event_types=event_types,
        since=since, until=until, limit=limit,
    )
    if not events:
        return BulkDescribeResult()

    if progress_cb is not None:
        await progress_cb(0, 0, f"Clustere {len(events)} Events …")

    mapping = cluster_events(events)
    write_clusters(store, mapping)  # type: ignore[arg-type]

    # Unique clusters + representatives. Events without a cluster_id (no
    # readable pHash) get a synthetic solo-cluster keyed by event_id so
    # they are still described individually.
    cluster_members: dict[str, list[int]] = defaultdict(list)
    for eid, cid in mapping.items():
        cluster_members[cid or f"solo-{eid}"].append(eid)

    total = len(cluster_members)
    log_message(
        f"🖌️ bulk-describe: {len(events)} undescribed events → {total} clusters "
        f"(source={source_id or 'all'})"
    )
    if progress_cb is not None:
        await progress_cb(
            0, total,
            f"Analysiere {total} Cluster (Reduktion von {len(events)} Events)",
        )

    processed = 0
    failed = 0
    cancelled = False
    for cluster_id, member_ids in cluster_members.items():
        if cancel_cb is not None and await cancel_cb():
            cancelled = True
            break
        if progress_cb is not None:
            await progress_cb(processed, total, None)
        # Representative: first member = oldest (ORDER BY timestamp ASC).
        repr_id = member_ids[0]
        try:
            text = await analyze_event_with_vlm(repr_id, store=store)  # type: ignore[arg-type]
            # Real cluster (more than the representative): fan the
            # description out to all members. Solo-clusters stay single.
            if cluster_id and not cluster_id.startswith("solo-"):
                store.apply_cluster_description(  # type: ignore[attr-defined]
                    cluster_id, text, "bulk-worker",
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("bulk: cluster %s failed: %s", cluster_id, e)
            failed += 1
        processed += 1

    log_message(
        f"🖌️ bulk-describe done: {processed - failed}/{total} described, "
        f"{failed} failed" + (" (cancelled)" if cancelled else "")
    )
    return BulkDescribeResult(
        total_events=len(events),
        total_clusters=total,
        processed=processed,
        failed=failed,
        cancelled=cancelled,
    )
