"""Vision-Preview Mixin — multi-source live preview popup state.

State for the ``/vision-preview-popup`` page. Designed multi-source
from day one so we don't need to refactor when the user wants to watch
two webcams side by side. With a single source the popup degenerates
to a one-image view; with N sources it shows a CSS grid.

Per-source state:
* ``vision_preview_visible_sources``  — which sources the user wants
                                        rendered right now (subset of
                                        all registered sources)
* ``vision_preview_resolutions``      — ``source_id`` → ``"WxH"`` or
                                        ``"default"``; persisted in
                                        vision_store.sources.settings_json

Global state:
* ``vision_preview_fps``              — refresh rate for ALL visible
                                        sources (0 = manual single-shot)

Why FPS is global, resolution is per-source: a user typically wants a
common cadence ("show everything at 1 fps") but each cam needs its own
resolution tied to its hardware capability (a 4K overview cam vs a
640×480 entrance cam).
"""

from __future__ import annotations

import logging
from typing import Any

import reflex as rx

logger = logging.getLogger(__name__)


# Built-in resolution presets — user can pick any from this dropdown. The
# actual resolution the V4L2 driver delivers may differ if the hardware
# can't do the requested size; cv2 falls back to the nearest mode.
_RESOLUTION_PRESETS = [
    {"value": "default", "label": "Treiber-Default"},
    {"value": "320x240", "label": "320 × 240 (QVGA)"},
    {"value": "640x480", "label": "640 × 480 (VGA)"},
    {"value": "800x600", "label": "800 × 600 (SVGA)"},
    {"value": "1280x720", "label": "1280 × 720 (HD)"},
    {"value": "1920x1080", "label": "1920 × 1080 (Full HD)"},
    {"value": "2560x1440", "label": "2560 × 1440 (QHD)"},
    {"value": "3840x2160", "label": "3840 × 2160 (4K)"},
]


class VisionPreviewMixin(rx.State, mixin=True):
    """UI state for the multi-source vision preview popup."""

    # The complete list of detected sources (filled on page-load + rescan)
    vision_preview_sources: list[dict[str, Any]] = []  # [{id, label, available}]

    # Subset of source IDs that should be rendered (each gets an <img>)
    vision_preview_visible_sources: list[str] = []

    # Per-source resolution choice ("default" or "WIDTHxHEIGHT")
    vision_preview_resolutions: dict[str, str] = {}

    # Global FPS for all visible sources. 0 = manual single-shot mode.
    vision_preview_fps: float = 1.0

    # Bumped on every refresh request — appended to image URLs as cache-buster
    vision_preview_cache_buster: int = 0

    vision_preview_status: str = ""

    # --- Static dropdown options (read-only) -----------------------------

    vision_preview_fps_options: list[dict[str, str]] = [
        {"value": "0", "label": "Manuell (Einzelbild)"},
        {"value": "0.2", "label": "5 s"},
        {"value": "0.5", "label": "2 s"},
        {"value": "1", "label": "1 s"},
        {"value": "2", "label": "0.5 s"},
        {"value": "10", "label": "0.1 s"},
    ]
    vision_preview_resolution_options: list[dict[str, str]] = _RESOLUTION_PRESETS

    @rx.var
    def vision_preview_fps_value(self) -> str:
        """String-encoded fps for the rx.select.value binding."""
        if self.vision_preview_fps == int(self.vision_preview_fps):
            return str(int(self.vision_preview_fps))
        return str(self.vision_preview_fps)

    @rx.var
    def vision_preview_is_manual_mode(self) -> bool:
        return self.vision_preview_fps <= 0

    # --- Computed entries for rx.foreach in the UI ------------------------

    @rx.var
    def vision_preview_visible_entries(self) -> list[dict[str, str]]:
        """List of {id, label, image_url} for each visible source.

        ``image_url`` includes fps, cache-buster, and per-source resolution
        as query parameters so the browser issues the right request.
        Computed here (not in the UI render lambda) because Reflex Vars
        can't be string-concatenated inside rx.foreach lambdas.
        """
        entries: list[dict[str, str]] = []
        fps = self.vision_preview_fps
        cb = self.vision_preview_cache_buster
        all_sources = {s["id"]: s for s in self.vision_preview_sources}
        for sid in self.vision_preview_visible_sources:
            meta = all_sources.get(sid) or {"label": sid}
            res = self.vision_preview_resolutions.get(sid, "default")
            res_q = ""
            if res and res != "default" and "x" in res:
                try:
                    w_str, h_str = res.split("x", 1)
                    res_q = f"&width={int(w_str)}&height={int(h_str)}"
                except (ValueError, TypeError):
                    res_q = ""
            if fps <= 0:
                url = f"/api/vision/snapshot/{sid}?cb={cb}{res_q}"
            else:
                url = f"/api/vision/stream/{sid}?fps={fps}&cb={cb}{res_q}"
            entries.append({"id": sid, "label": str(meta.get("label", sid)), "image_url": url})
        return entries

    @rx.var
    def vision_preview_has_visible(self) -> bool:
        return len(self.vision_preview_visible_sources) > 0

    # --- Event handlers ---------------------------------------------------

    @rx.event
    def open_vision_preview(self) -> Any:
        """Triggered by the camera button in the input row — opens a real
        OS window via ``window.open()``. The popup page itself initializes
        its state from ``on_load_vision_preview``.

        Re-clicking the same button reuses the existing window because
        we pass a fixed ``windowName`` ("aifred-cam"); the browser
        focuses the open window instead of spawning a duplicate.

        URL is built from the ``frontend_path`` config (env-var
        ``AIFRED_FRONTEND_PATH`` — default ``""``). Reflex auto-mounts
        all rx.page routes under that prefix, but window.open() is raw
        JS and must use the prefixed URL or it hits a 404.
        """
        import os
        # Read the frontend_path same way rxconfig.py does — single source.
        prefix = (os.getenv("AIFRED_FRONTEND_PATH", "") or "").strip("/")
        url = f"/{prefix}/vision-preview-popup" if prefix else "/vision-preview-popup"
        return rx.call_script(
            f"window.open('{url}','aifred-cam',"
            "'popup=yes,width=900,height=820,left=180,top=100,"
            "menubar=no,toolbar=no,location=no,status=no')"
        )

    @rx.event
    def on_load_vision_preview(self) -> None:
        """Page-load handler for the popup window — populates the source
        list, loads persisted per-source resolutions, picks a sensible
        default visible-set."""
        self._refresh_sources()
        self.vision_preview_cache_buster += 1

    @rx.event
    def toggle_vision_preview_source(self, source_id: str) -> None:
        """Show or hide a source in the popup. Idempotent."""
        if not source_id:
            return
        if source_id in self.vision_preview_visible_sources:
            self.vision_preview_visible_sources = [
                s for s in self.vision_preview_visible_sources if s != source_id
            ]
        else:
            self.vision_preview_visible_sources = self.vision_preview_visible_sources + [
                source_id
            ]
        self.vision_preview_cache_buster += 1

    @rx.event
    def set_vision_preview_resolution(self, source_id: str, value: str) -> None:
        """Persist the resolution choice for a single source. ``value`` is
        either ``"default"`` or ``"WIDTHxHEIGHT"`` (from the dropdown)."""
        if not source_id:
            return
        # update reactive dict (Reflex needs assignment to trigger re-render)
        new_map = dict(self.vision_preview_resolutions)
        new_map[source_id] = value
        self.vision_preview_resolutions = new_map
        # Mirror the new resolution into the source-list entries (the UI
        # reads .resolution directly from each entry — see _refresh_sources).
        self.vision_preview_sources = [
            {**e, "resolution": value} if e["id"] == source_id else e
            for e in self.vision_preview_sources
        ]
        self._persist_source_resolution(source_id, value)
        self.vision_preview_cache_buster += 1

    @rx.event
    def set_vision_preview_fps(self, value: str) -> None:
        try:
            fps = float(value)
        except (TypeError, ValueError):
            return
        if fps < 0:
            fps = 0.0
        elif 0 < fps < 0.1:
            fps = 0.1
        elif fps > 30:
            fps = 30.0
        self.vision_preview_fps = fps
        self.vision_preview_cache_buster += 1

    @rx.event
    def refresh_vision_preview(self) -> None:
        """Manual-mode: bump cache-buster so each visible <img> re-fetches."""
        self.vision_preview_cache_buster += 1
        self.vision_preview_status = ""

    @rx.event
    def rescan_vision_preview_sources(self) -> None:
        try:
            from ..lib.frame_sources import rescan
            rescan()
        except Exception as e:  # noqa: BLE001
            logger.warning("vision-preview rescan failed: %s", e)
            self.vision_preview_status = f"⚠️ {e}"
            return
        self._refresh_sources()

    # --- Internals -------------------------------------------------------

    def _refresh_sources(self) -> None:
        """Pull current source list + persisted resolutions; choose a
        default visible-set (first available source) if none picked yet.

        Each entry in ``vision_preview_sources`` carries the current
        resolution as ``"resolution"`` field — needed because Reflex
        can't index dict-vars with foreach-loop vars in render lambdas.
        """
        try:
            from ..lib.frame_sources import list_all
            sources_raw = list_all()
        except Exception as e:  # noqa: BLE001
            logger.warning("vision-preview source listing failed: %s", e)
            self.vision_preview_sources = []
            self.vision_preview_status = f"⚠️ {e}"
            return

        # Load persisted per-source resolution from vision_store
        new_resolutions = dict(self.vision_preview_resolutions)
        try:
            from ..lib.vision_store import VisionStore
            store = VisionStore()
            for src in sources_raw:
                sid = src.source_id
                if sid in new_resolutions:
                    continue  # user already set in this session — don't clobber
                stored = store.get_source(sid)
                res: str = "default"
                if stored:
                    persisted = (stored.get("settings") or {}).get("resolution")
                    if isinstance(persisted, str):
                        res = persisted
                new_resolutions[sid] = res
        except Exception as e:  # noqa: BLE001
            logger.warning("vision-preview resolution load failed: %s", e)
        self.vision_preview_resolutions = new_resolutions

        # Build the source list with current resolution baked into each entry
        entries: list[dict[str, Any]] = []
        for src in sources_raw:
            src_info = src.info()
            base_label = src_info.display_name or src_info.source_id
            display = base_label if src_info.available else f"{base_label}  ✗"
            entries.append({
                "id": src_info.source_id,
                "label": display,
                "available": bool(src_info.available),
                "resolution": new_resolutions.get(src_info.source_id, "default"),
            })
        self.vision_preview_sources = entries

        # Pick a sensible default visible-set if the user hasn't yet
        if not self.vision_preview_visible_sources:
            available = [e["id"] for e in self.vision_preview_sources if e["available"]]
            if available:
                self.vision_preview_visible_sources = [available[0]]

    def _persist_source_resolution(self, source_id: str, resolution: str) -> None:
        """Write the per-source resolution choice to vision_store.sources."""
        try:
            from ..lib.frame_sources import get as get_source
            from ..lib.vision_store import VisionStore
            store = VisionStore()
            src = get_source(source_id)
            existing = store.get_source(source_id)
            display_name = existing.get("display_name") if existing else (
                src.display_name if src else source_id
            )
            kind = existing.get("kind") if existing else (
                src.kind if src else "webcam"
            )
            settings = dict(existing.get("settings", {})) if existing else {}
            settings["resolution"] = resolution
            store.upsert_source(
                source_id=source_id,
                display_name=str(display_name or source_id),
                kind=str(kind or "webcam"),
                prompt_context=str(existing.get("prompt_context", "")) if existing else "",
                position=str(existing.get("position", "")) if existing else "",
                auto_start=bool(existing.get("auto_start", False)) if existing else False,
                sensitivity=str(existing.get("sensitivity", "medium")) if existing else "medium",
                settings=settings,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("vision-preview resolution persist failed: %s", e)
            self.vision_preview_status = f"⚠️ Persistierung fehlgeschlagen: {e}"
