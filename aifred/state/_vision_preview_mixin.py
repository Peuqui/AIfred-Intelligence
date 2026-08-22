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

    # Source-IDs mit aktiver Gesichts­erkennung im Watch-Mode. Lebt
    # parallel zu ``vision_preview_watching`` — beide können
    # unabhängig an sein, die Watcher-Engine läuft solange
    # mindestens einer der beiden Modi für eine Source aktiv ist.
    vision_preview_face_active: list[str] = []

    # Global cooldown between VLM calls in continuous watch-mode.
    # Seconds; persisted in vision_preview.json. 1s is the minimum
    # sane value (the VLM itself answers in ~0.4s for short prompts).
    vision_preview_vlm_cooldown_sec: float = 5.0

    # Min-Abstand zwischen Face-Recognition-Events (motion-getriggert).
    # Default 1 s reicht für Eingangs-Überwachung — bei Bedarf im
    # Header-Dropdown anpassbar. Läuft unabhängig vom VLM-Cycle.
    vision_preview_face_throttle_sec: float = 1.0

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
    # Face-Throttle-Werte: Detection läuft GPU-seitig in ~10 ms,
    # selbst 10 Events/s lasten die Karte nicht aus. Die schnellen
    # Werte werden zusätzlich als „N/s" gelabelt (intuitiver für
    # Türsteher-Modus), die langsamen als Sekunden.
    vision_preview_face_throttle_options: list[dict[str, str]] = [
        {"value": "0.1", "label": "0,1 s · 10/s"},
        {"value": "0.2", "label": "0,2 s · 5/s"},
        {"value": "0.5", "label": "0,5 s · 2/s"},
        {"value": "1", "label": "1 s"},
        {"value": "2", "label": "2 s"},
        {"value": "3", "label": "3 s"},
    ]

    @rx.var
    def vision_preview_vlm_cooldown_value(self) -> str:
        v = self.vision_preview_vlm_cooldown_sec
        return str(int(v)) if v == int(v) else str(v)

    @rx.var
    def vision_preview_face_throttle_value(self) -> str:
        v = self.vision_preview_face_throttle_sec
        if v == int(v):
            return str(int(v))
        # 0.1/0.2/0.5 sauber als „0.1" rendern (statt 0.10000…0001)
        return f"{v:g}"

    vision_preview_status: str = ""

    # --- Static dropdown options (read-only) -----------------------------

    # Bildrate-Optionen in fps. Sub-fps-Werte zusätzlich mit Sekunden
    # gelabelt, damit „0,2 fps" nicht abstrakt wirkt.
    vision_preview_fps_options: list[dict[str, str]] = [
        {"value": "0", "label": "Manuell (Einzelbild)"},
        {"value": "0.2", "label": "0,2 fps · 1 Bild / 5 s"},
        {"value": "0.5", "label": "0,5 fps · 1 Bild / 2 s"},
        {"value": "1", "label": "1 fps"},
        {"value": "2", "label": "2 fps"},
        {"value": "5", "label": "5 fps"},
        {"value": "10", "label": "10 fps"},
        {"value": "15", "label": "15 fps"},
        {"value": "30", "label": "30 fps"},
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
    async def on_load_vision_preview(self) -> Any:
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
        # Echten Ollama-Status für das Power-Toggle abfragen — ohne
        # das zeigt der Button immer „nicht geladen", selbst wenn ein
        # anderes Tool das VLM schon im VRAM hat.
        await self.refresh_vlm_loaded()  # type: ignore[attr-defined]
        self._refresh_sources()
        self.vision_preview_cache_buster += 1
        briefings_map = {
            str(e["id"]): str(e.get("prompt_context", ""))
            for e in self.vision_preview_sources
        }
        briefings_json = _json.dumps(briefings_map)
        # i18n-Strings ans Frontend, damit der Enroll-Button in der
        # User-Sprache erscheint. vlm_sse_manager.js liest die aus
        # window.__aifredFaceEnrollLabel / __aifredFaceDiscardLabel.
        # ``ui.helpers.t()`` returnt rx.Var (für Frontend-Rendering) —
        # hier brauchen wir aber den rohen String, also direkt aus
        # der Translation-Quelle holen.
        from ..lib.i18n import TranslationManager
        ui_lang = getattr(self, "ui_language", "de") or "de"
        translations = TranslationManager._translations.get(ui_lang, {})
        enroll_label = translations.get(
            "vision_preview_face_enroll_button", "+ tag"
        )
        discard_label = translations.get(
            "vision_preview_face_discard_button", "Discard"
        )
        enroll_label_json = _json.dumps(enroll_label)
        discard_label_json = _json.dumps(discard_label)
        return rx.call_script(
            "(function(){"
            f"window.__aifredFaceEnrollLabel = {enroll_label_json};"
            f"window.__aifredFaceDiscardLabel = {discard_label_json};"
            # SSE-Manager idempotent injecten
            "if (!window.__aifredVLMSSEInjected) {"
            "  window.__aifredVLMSSEInjected = true;"
            "  var s = document.createElement('script');"
            "  s.src = '/vlm_sse_manager.js?v=11';"
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
        # Sichtbare Quellen-Menge persistieren, damit die Auswahl beim
        # nächsten Öffnen des Popups erhalten bleibt.
        self._persist_preview_setting(
            "visible_sources", list(self.vision_preview_visible_sources)
        )


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

    # Hinweis: Der Kameranamen (Alias) wird jetzt in den Vigilantia-
    # Einstellungen editiert (set_vigilantia_source_alias), die Live-
    # Vorschau zeigt ihn nur noch als read-only Schild. Der frühere
    # set_vision_preview_alias-Handler ist damit entfallen.


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

    async def _apply_watch_mode(self, source_id: str) -> None:
        """Watcher anhand der zwei UI-Toggles (Bild-Analyse +
        Gesichts-Erkennung) konfigurieren. Beide Modi laufen über
        denselben Watcher-Task — die Combo wird hier aus dem State
        zusammengebaut, der Watcher wird bei jeder Toggle-Aktion
        komplett neu gestartet (stop → start), weil WatchConfig
        unveränderlich ist.

        Wenn beide aus → Watcher wird gestoppt.
        Wenn mindestens einer an → Watcher mit kombinierter Config.
        """
        from ..lib.vision_watcher import WatchConfig, get_default_watcher
        watcher = get_default_watcher()

        vlm_on = source_id in self.vision_preview_watching
        face_on = source_id in self.vision_preview_face_active

        # Cache-buster: ein Toggle bedeutet potenzielle Cam-Eviction
        # — der Browser muss den MJPEG-Stream neu öffnen.
        self.vision_preview_cache_buster += 1

        if not vlm_on and not face_on:
            await watcher.stop(source_id)
            return

        cd = max(0.5, float(self.vision_preview_vlm_cooldown_sec))
        preview_fps = float(self.vision_preview_fps or 0.0)
        stream_fps = max(2.0, preview_fps)
        # Continuous-Flag aus den Plugin-Settings — der Popup-Toggle
        # aktiviert Face-Erkennung; ob die kontinuierlich oder nur
        # motion-gated läuft, entscheidet der Settings-Schalter
        # ``face_recognition.continuous`` (SSOT in settings.json).
        # Default OFF = Türsteher-Modus (GPU-schonend), ON = Schreib-
        # tisch-Modus (auch ruhige Personen werden erkannt).
        continuous = face_on and bool(
            getattr(self, "face_recognition_continuous", False)
        )
        # Per-Cam Motion-Schwelle aus der Source-Liste lesen (vom
        # _refresh_sources eingefüttert). Default 0.02 wenn nichts
        # gesetzt — Outdoor-Cams können das hochschrauben, um Wind
        # in den Blättern zu ignorieren.
        mma = 0.02
        for e in self.vision_preview_sources:
            if e.get("id") == source_id:
                try:
                    mma = float(e.get("motion_min_area_ratio", 0.02) or 0.02)
                except (TypeError, ValueError):
                    mma = 0.02
                break
        # Personenerkennung läuft parallel zur Gesichtserkennung — wie
        # beim Auge (Hintergrund). Gekoppelt an den Face-Button (die
        # Überwachungs-Paarung „wer ist da"), gesteuert von derselben
        # SSoT wie der Hintergrund (settings.json).
        person_on = face_on and bool(
            getattr(self, "person_detect_enabled", False)
        )
        cfg = WatchConfig(
            fps=stream_fps,
            motion_min_area_ratio=mma,
            run_vlm_on_motion=False,
            run_vlm_continuous=vlm_on,
            vlm_cooldown_sec=cd,
            run_face_detect_on_motion=face_on,
            run_person_detect_on_motion=person_on,
            face_recognition_continuous=continuous,
            min_event_interval_sec=max(
                0.1, float(self.vision_preview_face_throttle_sec)
            ),
        )
        # Kamera-Profil-Constraints (SSoT mit Autostart + Tool-Pfad): eine
        # ai_camera erkennt on-device — MOG2-Gating/YOLO aus, Edge-AI-Poll
        # triggert. Ohne das lief dieselbe Kamera im Popup mit Pixel-Motion,
        # im Hintergrund aber mit Edge-AI-Trigger.
        from ..lib.vision_autostart import (
            _load_plugin_settings,
            profile_watch_overrides,
        )
        record = None
        try:
            from ..lib.vision_store import VisionStore
            record = VisionStore().get_source(source_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("preview: source lookup failed for %s: %s", source_id, e)
        overrides = profile_watch_overrides(
            source_id, (record or {}).get("settings") or {}, _load_plugin_settings()
        )
        if overrides:
            import dataclasses
            cfg = dataclasses.replace(cfg, **overrides)
        # Stop + Start, damit die neue Config greift. start() ist
        # idempotent — wenn schon was läuft, returnt es; deshalb
        # stoppen wir explizit vorher.
        await watcher.stop(source_id)
        try:
            await watcher.start(source_id, cfg)
        except Exception as e:  # noqa: BLE001
            logger.warning("watcher start failed for %s: %s", source_id, e)
            self.vision_preview_status = f"⚠️ Watcher: {e}"

    @rx.event
    async def toggle_vision_preview_watch(self, source_id: str) -> None:
        """Toggle Bild-Analyse (continuous-VLM) für die Source."""
        if not source_id:
            return
        if source_id in self.vision_preview_watching:
            self.vision_preview_watching = [
                s for s in self.vision_preview_watching if s != source_id
            ]
        else:
            self.vision_preview_watching = self.vision_preview_watching + [source_id]
        await self._apply_watch_mode(source_id)

    @rx.event
    async def toggle_vision_preview_face_recognition(self, source_id: str) -> None:
        """Toggle Gesichts-Erkennung für die Source. Läuft im
        Continuous-Modus (motion-unabhängig) parallel zur Bild-Analyse.
        """
        if not source_id:
            return
        if source_id in self.vision_preview_face_active:
            self.vision_preview_face_active = [
                s for s in self.vision_preview_face_active if s != source_id
            ]
        else:
            self.vision_preview_face_active = (
                self.vision_preview_face_active + [source_id]
            )
        await self._apply_watch_mode(source_id)

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

    @rx.event
    def set_vision_preview_face_throttle(self, value: str) -> None:
        """Min-Abstand zwischen Face-Events (Sekunden). Greift beim
        nächsten Watcher-Start."""
        try:
            t = float(value)
        except (TypeError, ValueError):
            return
        if t < 0.1 or t > 60:
            return
        self.vision_preview_face_throttle_sec = t
        self._persist_preview_setting("face_throttle_sec", t)

    # set_vision_preview_vlm_model entfernt — das Popup-Header-Dropdown
    # ruft direkt set_vision_model_value aus _vision_settings_mixin
    # (SSOT, schreibt in plugins/tools/vision/settings.json).

    @rx.event
    def clear_vlm_teleprompter(self, source_id: str) -> Any:
        """Buffer der Live-Analyse-Box im Frontend leeren. Ruft die
        JS-Funktion ``window.clearVlmTeleprompter(sid)`` aus
        vlm_sse_manager.js — die hält die Zeilen client-seitig.
        Der EventSource bleibt offen, neue VLM-Antworten füllen die
        Box wieder auf."""
        if not source_id:
            return None
        # Einfache Sanitisierung — source_id ist sonst kontrolliert
        # ("cam/v4l2_0"), aber Anführungszeichen filtern wir aus
        # Vorsicht raus, damit kein JS-String aufgerissen werden kann.
        safe = source_id.replace("'", "").replace("\\", "")
        return rx.call_script(
            f"window.clearVlmTeleprompter && window.clearVlmTeleprompter('{safe}')"
        )

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
        stored_auto_start: dict[str, bool] = {}
        stored_motion_min: dict[str, float] = {}
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
                # Per-Source Hintergrund-Watch + Motion-Schwelle.
                # ``auto_start`` ist Top-Level-Spalte (Schema-Default),
                # ``motion_min_area_ratio`` lebt im settings_json.
                stored_auto_start[sid] = bool(stored.get("auto_start", False))
                s = stored.get("settings") or {}
                mma = s.get("motion_min_area_ratio")
                if isinstance(mma, (int, float)) and 0.001 <= mma <= 0.5:
                    stored_motion_min[sid] = float(mma)
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
                "auto_start": stored_auto_start.get(src_info.source_id, False),
                "motion_min_area_ratio": stored_motion_min.get(
                    src_info.source_id, 0.02
                ),
            })
        self.vision_preview_sources = entries
        log_message(
            f"🎬 _refresh_sources done: {len(entries)} entries, "
            f"available={[e['id'] for e in entries if e['available']]}"
        )
        for entry in entries:
            log_message(
                f"  entry id={entry['id']} alias='{entry.get('alias','')}' "
                f"prompt_context='{entry.get('prompt_context','')[:50]}'"
            )

        # Sichtbare Quellen-Menge wiederherstellen:
        #   * persistiert vorhanden → daraus (auf noch existierende Quellen
        #     gefiltert), respektiert auch die bewusst leere Auswahl;
        #   * noch nie gesetzt (None) → sinnvoller Default: erste verfügbare.
        persisted = self._load_persisted_visible_sources()
        if persisted is not None:
            valid_ids = {e["id"] for e in self.vision_preview_sources}
            self.vision_preview_visible_sources = [
                s for s in persisted if s in valid_ids
            ]
            log_message(
                f"🎬 restored visible sources: {self.vision_preview_visible_sources}"
            )
        elif not self.vision_preview_visible_sources:
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
            ft = data.get("face_throttle_sec")
            if isinstance(ft, (int, float)) and 0.1 <= ft <= 60:
                self.vision_preview_face_throttle_sec = float(ft)
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

    def _load_persisted_visible_sources(self) -> list[str] | None:
        """Persistierte sichtbare Quellen-Menge aus vision_preview.json lesen.

        ``None`` = der Schlüssel fehlt (noch nie gesetzt → Default-Logik im
        Aufbau greift). Leere Liste = der User hat bewusst alle abgeschaltet
        (wird respektiert, kein Default)."""
        try:
            import json
            path = self._preview_settings_path()
            if not path.exists():
                return None
            with open(path, encoding="utf-8") as f:
                data = json.load(f) or {}
            vis = data.get("visible_sources")
            if not isinstance(vis, list):
                return None
            return [str(s) for s in vis]
        except Exception as e:  # noqa: BLE001
            logger.warning("vision-preview visible-sources load failed: %s", e)
            return None

    def _persist_source_prompt_context(self, source_id: str, prompt_context: str) -> None:
        """Write the per-source briefing text to vision_store.sources.prompt_context.
        That's a top-level column in the schema (already there for the
        original day-one design), not buried in settings_json."""
        try:
            from ..lib.frame_sources import get as get_source
            from ..lib.vision_store import VisionStore
            src = get_source(source_id)
            VisionStore().update_source_fields(
                source_id,
                fallback_display_name=src.display_name if src else source_id,
                fallback_kind=src.kind if src else "webcam",
                prompt_context=prompt_context,
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
            settings = dict(existing.get("settings", {})) if existing else {}
            if alias:
                settings["alias"] = alias
            else:
                settings.pop("alias", None)
            store.update_source_fields(
                source_id,
                fallback_display_name=src.display_name if src else source_id,
                fallback_kind=src.kind if src else "webcam",
                settings=settings,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("vision-preview alias persist failed: %s", e)
            self.vision_preview_status = f"⚠️ Persistierung fehlgeschlagen: {e}"

    def _persist_source_resolution(self, source_id: str, resolution: str) -> None:
        """Write the per-source resolution choice to vision_store.sources."""
        try:
            self._persist_source_setting(source_id, "resolution", resolution)  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            logger.warning("vision-preview resolution persist failed: %s", e)
            self.vision_preview_status = f"⚠️ Persistierung fehlgeschlagen: {e}"
