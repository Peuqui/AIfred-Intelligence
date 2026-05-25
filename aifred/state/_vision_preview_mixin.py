"""Vision-Preview Mixin — live snapshot modal state.

Lightweight state for the Live-Preview-Modal:

* List of available frame sources (refreshed when modal opens)
* Selected source ID
* Cache-buster counter for the ``<img>`` tag — incremented on each
  user-triggered refresh so the browser re-fetches the JPEG instead
  of pulling it from cache.

The actual JPEG bytes are served by the backend endpoint
``/api/vision/snapshot/{source_id:path}`` (see ``aifred/lib/api.py``).
"""

from __future__ import annotations

import logging
from typing import Any

import reflex as rx

logger = logging.getLogger(__name__)


class VisionPreviewMixin(rx.State, mixin=True):
    """UI state for the vision live-preview modal."""

    vision_preview_open: bool = False
    vision_preview_source: str = ""
    vision_preview_sources: list[dict[str, Any]] = []  # [{id, label, available}]
    vision_preview_cache_buster: int = 0
    vision_preview_status: str = ""

    @rx.var
    def vision_preview_image_url(self) -> str:
        """Browser-URL for the currently-selected source's snapshot,
        with cache-buster to force re-fetch on each refresh."""
        if not self.vision_preview_source:
            return ""
        return (
            f"/api/vision/snapshot/{self.vision_preview_source}"
            f"?cb={self.vision_preview_cache_buster}"
        )

    @rx.event
    def open_vision_preview(self) -> None:
        """Open the modal. Re-discovers sources to reflect hot-plugged
        webcams."""
        self._refresh_sources()
        self.vision_preview_open = True
        # Immediately bump the cache-buster so the first image fetch is
        # not from any prior browser cache.
        self.vision_preview_cache_buster += 1

    @rx.event
    def close_vision_preview(self) -> None:
        self.vision_preview_open = False

    @rx.event
    def set_vision_preview_source(self, source_id: str) -> None:
        if not source_id:
            return
        self.vision_preview_source = source_id
        self.vision_preview_cache_buster += 1
        self.vision_preview_status = ""

    @rx.event
    def refresh_vision_preview(self) -> None:
        """User clicked the refresh icon — force re-fetch."""
        self.vision_preview_cache_buster += 1
        self.vision_preview_status = ""

    @rx.event
    def rescan_vision_preview_sources(self) -> None:
        """User clicked the rescan icon — re-run device discovery + reload list."""
        try:
            from ..lib.frame_sources import rescan
            rescan()
        except Exception as e:  # noqa: BLE001
            logger.warning("vision-preview rescan failed: %s", e)
            self.vision_preview_status = f"⚠️ {e}"
            return
        self._refresh_sources()

    # ── internals ───────────────────────────────────────────────────

    def _refresh_sources(self) -> None:
        """Pull the current source list and pick a sensible default."""
        try:
            from ..lib.frame_sources import list_all
            entries: list[dict[str, Any]] = []
            for src in list_all():
                info = src.info()
                entries.append({
                    "id": info.source_id,
                    "label": info.display_name or info.source_id,
                    "available": bool(info.available),
                })
            self.vision_preview_sources = entries
        except Exception as e:  # noqa: BLE001
            logger.warning("vision-preview source listing failed: %s", e)
            self.vision_preview_sources = []
            self.vision_preview_status = f"⚠️ {e}"
            return

        # Keep the current selection if still present; otherwise pick the
        # first available source, falling back to the first listed one,
        # finally empty string if there's nothing.
        ids = [e["id"] for e in self.vision_preview_sources]
        if self.vision_preview_source in ids:
            return
        available = [e["id"] for e in self.vision_preview_sources if e["available"]]
        if available:
            self.vision_preview_source = available[0]
        elif ids:
            self.vision_preview_source = ids[0]
        else:
            self.vision_preview_source = ""
            self.vision_preview_status = ""
