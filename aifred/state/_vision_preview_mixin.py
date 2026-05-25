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


# Common resolutions for the dropdown labeller. Only those the driver
# actually honours during the probe end up in the per-source dropdown
# (see ``_build_resolution_options`` below).
_RESOLUTION_LABELS: dict[tuple[int, int], str] = {
    (320, 240): "320 × 240 (QVGA)",
    (640, 480): "640 × 480 (VGA)",
    (800, 600): "800 × 600 (SVGA)",
    (1024, 768): "1024 × 768 (XGA)",
    (1280, 720): "1280 × 720 (HD)",
    (1280, 960): "1280 × 960 (SXGA-)",
    (1600, 1200): "1600 × 1200 (UXGA)",
    (1920, 1080): "1920 × 1080 (Full HD)",
    (2560, 1440): "2560 × 1440 (QHD)",
    (3840, 2160): "3840 × 2160 (4K)",
}


_VISION_SSE_MANAGER_SCRIPT = r"""
(() => {
  // Single SSE manager per page. Watches for elements with the
  // .vlm-event-target class and a data-vlm-source attribute; opens
  // an EventSource per unique source and pipes VLM analysis events
  // into the matching DOM target as a 1-per-line teleprompter feed.
  if (window.__aifredVLMSSEInit) return;
  window.__aifredVLMSSEInit = true;
  console.log('[AIfred-VLM] SSE manager booting');
  const streams = {};
  const lines = {};
  const MAX_LINES = 8;

  function render(sid) {
    const items = lines[sid] || [];
    document.querySelectorAll('.vlm-event-target[data-vlm-source="' + sid + '"]')
      .forEach(function (el) {
        if (items.length === 0) {
          el.textContent = el.dataset.idleText || '';
          el.style.fontStyle = 'italic';
          return;
        }
        el.style.fontStyle = 'normal';
        el.textContent = items.join('\n');
        el.scrollTop = el.scrollHeight;
      });
  }

  function openStream(sid) {
    if (streams[sid] && streams[sid].readyState !== 2) return;
    const url = '/api/vision/events/' + sid;
    console.log('[AIfred-VLM] EventSource opening:', url);
    const es = new EventSource(url);
    streams[sid] = es;
    lines[sid] = lines[sid] || [];
    es.onopen = function () { console.log('[AIfred-VLM] EventSource open:', sid); };
    es.onmessage = function (ev) {
      try {
        const data = JSON.parse(ev.data);
        const ts = (data.timestamp || '').split('T')[1] || '';
        const desc = (data.description || '').replace(/\s+/g, ' ').trim();
        const line = ts + '  ' + desc;
        lines[sid].push(line);
        if (lines[sid].length > MAX_LINES) lines[sid].shift();
        render(sid);
      } catch (e) {}
    };
    es.onerror = function (e) {
      console.warn('[AIfred-VLM] EventSource error:', sid, e);
    };
  }

  function scan() {
    const targets = document.querySelectorAll('.vlm-event-target[data-vlm-source]');
    const seen = new Set();
    targets.forEach(function (el) {
      const sid = el.dataset.vlmSource;
      if (!sid) return;
      if (!el.dataset.idleText) el.dataset.idleText = el.textContent;
      seen.add(sid);
      openStream(sid);
      render(sid);
    });
    Object.keys(streams).forEach(function (sid) {
      if (!seen.has(sid) && streams[sid]) {
        streams[sid].close();
        delete streams[sid];
      }
    });
  }

  const obs = new MutationObserver(function () { scan(); });
  obs.observe(document.body, { childList: true, subtree: true });
  scan();
})();
"""


def _label_from(entry: dict[str, Any], alias: str) -> str:
    """Recompute a source-list label given a fresh alias. Used by the
    alias-setter to keep the label in sync without re-running the full
    _refresh_sources cycle.

    Pattern matches what _refresh_sources writes: alias if set, else
    the hardware display name, suffixed with ``✗`` for unavailable cams.
    """
    base = alias or str(entry.get("hardware_name") or entry.get("label") or entry["id"])
    return base if entry.get("available") else f"{base}  ✗"


def _build_resolution_options(src: Any, available: bool) -> list[dict[str, str]]:
    """Compose the per-source dropdown options.

    Always starts with ``"default"`` (treiber default = whatever the
    driver picks on cv2.VideoCapture without explicit width/height).
    Below that, only the modes that the camera ACTUALLY supports
    according to ``detect_resolutions()`` are listed.

    For sources without a ``detect_resolutions()`` method (future RTSP /
    file / screen sources) we just expose the default — they have their
    own resolution semantics anyway.
    """
    opts: list[dict[str, str]] = [
        {"value": "default", "label": "Treiber-Default"},
    ]
    if not available:
        return opts
    detector = getattr(src, "detect_resolutions", None)
    if not callable(detector):
        return opts
    try:
        supported = detector()
    except Exception as e:  # noqa: BLE001
        logger.warning("resolution detect failed for %s: %s", src.source_id, e)
        return opts
    for w, h in supported:
        key = (w, h)
        label = _RESOLUTION_LABELS.get(key, f"{w} × {h}")
        opts.append({"value": f"{w}x{h}", "label": label})
    return opts


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

    # Teleprompter layout: "overlay" (subtitle-style on the image) or
    # "below" (full-width block below the image). Global because users
    # tend to settle on one style. Persisted in vision_preview.json.
    vision_preview_teleprompter_mode: str = "overlay"

    # Bumped on every refresh request — appended to image URLs as cache-buster
    vision_preview_cache_buster: int = 0

    # Source IDs with an active continuous-VLM watcher. Drives the
    # watch-toggle switch in each cam-tile and which SSE streams the
    # frontend opens.
    vision_preview_watching: list[str] = []

    # Global cooldown between VLM calls in continuous watch-mode.
    # Seconds; persisted in vision_preview.json. 1s is the minimum
    # sane value (the VLM itself answers in ~0.4s for short prompts).
    vision_preview_vlm_cooldown_sec: float = 5.0

    # NB: ein eigenes ``vision_preview_vlm_model`` gibt es nicht mehr.
    # Das Modell-Dropdown im Popup-Header bindet direkt an
    # ``vision_model_value`` aus _vision_settings_mixin (SSOT in
    # plugins/tools/vision/settings.json). Ein Wert, zwei UIs.

    vision_preview_cooldown_options: list[dict[str, str]] = [
        {"value": "1", "label": "1 s"},
        {"value": "2", "label": "2 s"},
        {"value": "3", "label": "3 s"},
        {"value": "5", "label": "5 s"},
        {"value": "10", "label": "10 s"},
        {"value": "30", "label": "30 s"},
        {"value": "60", "label": "60 s"},
    ]

    @rx.var
    def vision_preview_vlm_cooldown_value(self) -> str:
        v = self.vision_preview_vlm_cooldown_sec
        return str(int(v)) if v == int(v) else str(v)

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
    # Resolution options are per-source now (built in _refresh_sources from
    # the camera's actual supported modes) — no global field anymore.

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

        URL stays bare ``/api/...`` — NO ``frontend_path`` prefix. nginx
        on this host routes ``/api/...`` directly to the backend (port
        8002); the ``/aifred/...`` location goes to the Vite frontend
        (port 3002), and Vite has no API proxy, so a prefixed URL would
        get the SPA fallback HTML instead of the JPEG.
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
    def on_load_vision_preview(self) -> Any:
        """Page-load handler for the popup window — populates the source
        list, loads persisted per-source resolutions + global FPS, picks
        a sensible default visible-set, injiziert den VLM-SSE-Manager
        und schreibt die persistierten Briefings via JS in die
        Textareas.

        Zwei JS-Tasks:

        * SSE-Manager-Injection: ``rx.script(src=…)`` und
          ``rx.el.script(src=…)`` in Reflex-0.8-Lazy-Bundles landen im
          virtuellen DOM und werden vom Browser nicht ausgeführt
          (React-DOM-Restriktion). Daher hängen wir den Tag dynamisch
          ans ``<head>``.
        * Briefing-Initial-Set: Reflex propagiert weder ``value=``
          noch ``default_value=`` aus rx.foreach-entries auf das
          Radix-TextArea. Wir suchen per ``data-vlm-briefing-source``
          und schreiben den State-Wert direkt ins DOM.
        """
        import json as _json

        from ..lib.logging_utils import log_message
        log_message("🎬 on_load_vision_preview firing")
        self._load_preview_fps()
        # Plugin-Settings (Modell + verfügbare Modelle) auch im Popup-
        # Kontext laden — sonst ist das Header-Dropdown leer, wenn das
        # Settings-Modal noch nie geöffnet wurde.
        self._refresh_vision_settings()
        self._refresh_sources()
        self.vision_preview_cache_buster += 1
        briefings_map = {
            str(e["id"]): str(e.get("prompt_context", ""))
            for e in self.vision_preview_sources
        }
        briefings_json = _json.dumps(briefings_map)
        return rx.call_script(
            "(function(){"
            # SSE-Manager idempotent injecten
            "if (!window.__aifredVLMSSEInjected) {"
            "  window.__aifredVLMSSEInjected = true;"
            "  var s = document.createElement('script');"
            "  s.src = '/vlm_sse_manager.js?v=3';"
            "  s.async = true;"
            "  document.head.appendChild(s);"
            "  console.log('[AIfred-VLM] injected script tag');"
            "}"
            # Briefings ins DOM schreiben — polling, weil die Textareas
            # erst nach Reflex-Hydration im DOM erscheinen.
            f"var briefings = {briefings_json};"
            "var attempts = 0;"
            "var iv = setInterval(function(){"
            "  attempts++;"
            "  var allWritten = true;"
            "  Object.keys(briefings).forEach(function(sid){"
            "    var sel = 'textarea[data-vlm-briefing-source=\"' + sid + '\"]';"
            "    var el = document.querySelector(sel);"
            "    if (!el) { allWritten = false; return; }"
            "    if (el.dataset.briefingInitialized) return;"
            "    el.value = briefings[sid] || '';"
            "    el.dataset.briefingInitialized = '1';"
            "    console.log('[AIfred-VLM] briefing set for', sid);"
            "  });"
            "  if (allWritten || attempts > 40) clearInterval(iv);"
            "}, 100);"
            "})();"
        )

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
    def set_vision_preview_briefing_text(self, source_id: str, value: str) -> None:
        """Live update beim Tippen — synchronisiert nur den State, ohne
        DB-Write. Persistiert wird erst bei on_blur via
        ``set_vision_preview_prompt_context``. Ohne diese Trennung würde
        bei jedem Tastenanschlag ein DB-Write stattfinden.
        """
        if not source_id:
            return
        text = value if isinstance(value, str) else ""
        self.vision_preview_sources = [
            {**e, "prompt_context": text}
            if e["id"] == source_id else e
            for e in self.vision_preview_sources
        ]

    @rx.event
    def set_vision_preview_prompt_context(self, source_id: str, value: str) -> None:
        """Per-camera briefing text for the VLM. Persists to
        vision_store.sources.prompt_context (top-level column, not in
        the settings json — schema has had it from day one). The
        vision_analyze tool prepends this to the user-given prompt so
        the VLM gets context about what it's actually looking at."""
        if not source_id:
            return
        new_text = value.strip() if isinstance(value, str) else ""
        self.vision_preview_sources = [
            {**e, "prompt_context": new_text}
            if e["id"] == source_id else e
            for e in self.vision_preview_sources
        ]
        self._persist_source_prompt_context(source_id, new_text)

    @rx.event
    def set_vision_preview_alias(self, source_id: str, value: str) -> None:
        """User-given camera name. Persists to vision_store.sources.settings.alias.
        Empty string clears the alias and the source falls back to its
        hardware display name."""
        if not source_id:
            return
        new_alias = value.strip() if isinstance(value, str) else ""
        self.vision_preview_sources = [
            {**e, "alias": new_alias, "label": _label_from(e, new_alias)}
            if e["id"] == source_id else e
            for e in self.vision_preview_sources
        ]
        self._persist_source_alias(source_id, new_alias)
        # No cache-buster bump — the image URL doesn't change with the
        # alias, only the label/overlay does.

    @rx.event
    def set_vision_preview_resolution(self, source_id: str, value: str) -> None:
        """Persist the resolution choice for a single source. ``value`` is
        either ``"default"`` or ``"WIDTHxHEIGHT"`` (from the dropdown).
        Cache-buster bump forces ``<img>`` to actually reload — Reflex
        sometimes skips DOM updates if only a query param changes."""
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
    async def toggle_vision_preview_watch(self, source_id: str) -> None:
        """Start or stop the continuous-VLM watcher for a single source.

        Wires through to VisionWatcher — the actual VLM-on-tick logic
        lives in vision_watcher.py. We only manage UI state here.
        """
        if not source_id:
            return
        from ..lib.vision_watcher import WatchConfig, get_default_watcher
        watcher = get_default_watcher()
        if source_id in self.vision_preview_watching:
            await watcher.stop(source_id)
            self.vision_preview_watching = [
                s for s in self.vision_preview_watching if s != source_id
            ]
            # Cache-buster bump → image URL changes → browser reconnects
            # the MJPEG stream. Without this the <img> stays frozen on
            # the last frame because the previous stream got evicted
            # when the watcher took/released the cam.
            self.vision_preview_cache_buster += 1
            return
        # Start: pull cooldown + fps from defaults; the per-cam briefing
        # is read by the watcher itself from vision_store.prompt_context
        # so we don't have to plumb it through here.
        #
        # Watcher-fps muss höher als die Cooldown-fps sein, denn die
        # gleichen Frames werden vom MJPEG-Live-Stream über den
        # FrameBus konsumiert (api.py:_mjpeg_stream fällt bei aktivem
        # Watcher auf bus.subscribe zurück). Bei cooldown=5s wäre 0.4
        # fps die "VLM-natürliche" Rate — das wäre die Diashow im
        # Browser. Stattdessen orientieren wir uns an der gewünschten
        # Vorschau-fps des Users und nehmen mindestens 2 fps, damit
        # die Live-Vorschau flüssig bleibt und die VLM trotzdem nur
        # alle ``vlm_cooldown_sec`` einen Frame analysiert.
        cd = max(0.5, float(self.vision_preview_vlm_cooldown_sec))
        preview_fps = float(self.vision_preview_fps or 0.0)
        stream_fps = max(2.0, preview_fps)
        cfg = WatchConfig(
            fps=stream_fps,
            run_vlm_on_motion=False,
            run_vlm_continuous=True,
            vlm_cooldown_sec=cd,
            run_face_detect_on_motion=False,
        )
        try:
            await watcher.start(source_id, cfg)
            self.vision_preview_watching = self.vision_preview_watching + [source_id]
            # Same reason as on the stop path — bump cache-buster so the
            # browser reconnects to the MJPEG stream. The previous live-
            # stream got evicted when the watcher took the cam, and the
            # new stream needs a fresh URL so the <img> requests it.
            self.vision_preview_cache_buster += 1
            # SSE manager is loaded as an asset script in the page itself,
            # so we don't need to fire it here. The MutationObserver inside
            # the script will pick up DOM changes from this toggle and open
            # the EventSource automatically.
        except Exception as e:  # noqa: BLE001
            logger.warning("watcher start failed for %s: %s", source_id, e)
            self.vision_preview_status = f"⚠️ Watcher: {e}"

    @rx.event
    def set_vision_preview_vlm_cooldown(self, value: str) -> None:
        """Cooldown between continuous-VLM calls (seconds). Takes effect
        on the next watcher start — running watchers keep their current
        cooldown until restarted."""
        try:
            cd = float(value)
        except (TypeError, ValueError):
            return
        if cd < 0.5 or cd > 300:
            return
        self.vision_preview_vlm_cooldown_sec = cd
        self._persist_preview_setting("vlm_cooldown_sec", cd)

    # set_vision_preview_vlm_model entfernt — das Popup-Header-Dropdown
    # ruft direkt set_vision_model_value aus _vision_settings_mixin
    # (SSOT, schreibt in plugins/tools/vision/settings.json).

    @rx.event
    def set_vision_preview_teleprompter_mode(self, value: str) -> None:
        """Toggle between overlay (subtitle on image) and below (full-
        width block below image) for the VLM teleprompter."""
        if value not in ("overlay", "below"):
            return
        self.vision_preview_teleprompter_mode = value
        self._persist_preview_setting("teleprompter_mode", value)

    @rx.event
    def set_vision_preview_fps(self, value: str) -> None:
        """FPS-Setter. Structurally identical to
        :meth:`set_vision_preview_resolution` because that path is the
        ONLY one Reflex's foreach diff reliably propagates into the
        ``<img src>`` attribute in nested tiles. A pure float change
        on ``vision_preview_fps`` plus a counter bump apparently isn't
        enough — the diff stays at the outer list level and never
        reaches the per-tile DOM update. Mirroring the resolution path
        (touch ``vision_preview_resolutions`` + rebuild each source
        dict with ``{**e}``) makes the foreach see fresh inner items
        and rebuild every tile.

        FPS is persisted globally for the preview popup (not per-source)
        in ``data/vision_preview.json``.
        """
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
        # Mirror the re-render pattern from set_vision_preview_resolution:
        self.vision_preview_resolutions = dict(self.vision_preview_resolutions)
        self.vision_preview_sources = [
            {**e} for e in self.vision_preview_sources
        ]
        self._persist_preview_fps(fps)
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
        from ..lib.logging_utils import log_message
        try:
            from ..lib.frame_sources import list_all
            sources_raw = list_all()
        except Exception as e:  # noqa: BLE001
            log_message(f"⚠️ vision-preview source listing failed: {e}")
            self.vision_preview_sources = []
            self.vision_preview_status = f"⚠️ {e}"
            return
        log_message(f"🎬 _refresh_sources: list_all returned {len(sources_raw)} source(s)")

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

        # Load persisted aliases + prompt-contexts the same way as resolutions.
        stored_aliases: dict[str, str] = {}
        stored_prompts: dict[str, str] = {}
        try:
            from ..lib.vision_store import VisionStore
            store = VisionStore()
            for src in sources_raw:
                sid = src.source_id
                stored = store.get_source(sid)
                if not stored:
                    continue
                a = (stored.get("settings") or {}).get("alias")
                if isinstance(a, str) and a.strip():
                    stored_aliases[sid] = a.strip()
                pc = stored.get("prompt_context")
                if isinstance(pc, str) and pc.strip():
                    stored_prompts[sid] = pc.strip()
        except Exception as e:  # noqa: BLE001
            logger.warning("vision-preview alias/prompt load failed: %s", e)

        # Build the source list with current resolution + per-source
        # resolution options baked into each entry.
        entries: list[dict[str, Any]] = []
        for src in sources_raw:
            try:
                src_info = src.info()
            except Exception as e:  # noqa: BLE001
                log_message(f"⚠️ source.info() failed for {src.source_id}: {e}")
                continue
            hardware_name = src_info.display_name or src_info.source_id
            alias = stored_aliases.get(src_info.source_id, "")
            # Label = alias if set, else hardware name; with availability marker.
            base_label = alias or hardware_name
            display = base_label if src_info.available else f"{base_label}  ✗"
            options = _build_resolution_options(src, src_info.available)
            entries.append({
                "id": src_info.source_id,
                "label": display,
                "alias": alias,
                "hardware_name": hardware_name,
                "available": bool(src_info.available),
                "resolution": new_resolutions.get(src_info.source_id, "default"),
                "resolution_options": options,
                "prompt_context": stored_prompts.get(src_info.source_id, ""),
            })
        self.vision_preview_sources = entries
        log_message(
            f"🎬 _refresh_sources done: {len(entries)} entries, "
            f"available={[e['id'] for e in entries if e['available']]}"
        )
        for e in entries:
            log_message(
                f"  entry id={e['id']} alias='{e.get('alias','')}' "
                f"prompt_context='{e.get('prompt_context','')[:50]}'"
            )

        # Pick a sensible default visible-set if the user hasn't yet
        if not self.vision_preview_visible_sources:
            available = [e["id"] for e in self.vision_preview_sources if e["available"]]
            if available:
                self.vision_preview_visible_sources = [available[0]]
                log_message(f"🎬 default visible source set to: {available[0]}")

    # ----- Global preview-settings persistence -------------------------
    # Stored separately from per-source state because FPS is a global
    # user preference for the live-preview popup, not a per-cam attribute.

    @staticmethod
    def _preview_settings_path() -> Any:
        from ..lib.config import DATA_DIR
        return DATA_DIR / "vision_preview.json"

    def _load_preview_fps(self) -> None:
        """Load all persisted preview settings on popup open."""
        try:
            import json
            path = self._preview_settings_path()
            data: dict[str, Any] = {}
            if path.exists():
                with open(path, encoding="utf-8") as f:
                    data = json.load(f) or {}
            fps = data.get("fps")
            if isinstance(fps, (int, float)) and 0 <= fps <= 30:
                self.vision_preview_fps = float(fps)
            mode = data.get("teleprompter_mode")
            if isinstance(mode, str) and mode in ("overlay", "below"):
                self.vision_preview_teleprompter_mode = mode
            cd = data.get("vlm_cooldown_sec")
            if isinstance(cd, (int, float)) and 0.5 <= cd <= 300:
                self.vision_preview_vlm_cooldown_sec = float(cd)
        except Exception as e:  # noqa: BLE001
            logger.warning("vision-preview settings load failed: %s", e)

    def _persist_preview_setting(self, key: str, value: Any) -> None:
        """Merge-update a single key in vision_preview.json — other
        keys stay untouched. Used by both the FPS setter and the
        teleprompter-mode setter."""
        try:
            import json
            path = self._preview_settings_path()
            data: dict[str, Any] = {}
            if path.exists():
                with open(path, encoding="utf-8") as f:
                    data = json.load(f) or {}
            data[key] = value
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:  # noqa: BLE001
            logger.warning("vision-preview setting '%s' persist failed: %s", key, e)

    def _persist_preview_fps(self, fps: float) -> None:
        """Backwards-compat wrapper — delegates to _persist_preview_setting."""
        self._persist_preview_setting("fps", fps)

    def _persist_source_prompt_context(self, source_id: str, prompt_context: str) -> None:
        """Write the per-source briefing text to vision_store.sources.prompt_context.
        That's a top-level column in the schema (already there for the
        original day-one design), not buried in settings_json."""
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
            store.upsert_source(
                source_id=source_id,
                display_name=str(display_name or source_id),
                kind=str(kind or "webcam"),
                prompt_context=prompt_context,
                position=str(existing.get("position", "")) if existing else "",
                auto_start=bool(existing.get("auto_start", False)) if existing else False,
                sensitivity=str(existing.get("sensitivity", "medium")) if existing else "medium",
                settings=dict(existing.get("settings", {})) if existing else {},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("vision-preview prompt-context persist failed: %s", e)
            self.vision_preview_status = f"⚠️ Persistierung fehlgeschlagen: {e}"

    def _persist_source_alias(self, source_id: str, alias: str) -> None:
        """Write the per-source user alias to vision_store.sources.settings.alias.
        Empty string removes the alias (source falls back to hardware name)."""
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
            if alias:
                settings["alias"] = alias
            else:
                settings.pop("alias", None)
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
            logger.warning("vision-preview alias persist failed: %s", e)
            self.vision_preview_status = f"⚠️ Persistierung fehlgeschlagen: {e}"

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
