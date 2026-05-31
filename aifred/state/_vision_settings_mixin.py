"""Vision-Plugin Settings-Mixin — UI state for the vision-settings modal.

Reactive state for the Vision-Plugin settings that the user can change
at runtime via the gear-icon modal in the Plugin tab:

* ``vision_mode_value``    — off / on-demand / live
* ``vision_model_value``   — which Ollama VLM tag to use for the
                              webcam pipeline (watch + side-channel)

The settings.json file under ``aifred/plugins/tools/vision/`` is the
single source of truth — this mixin reads on modal-open and writes on
each change. The plugin re-reads the file fresh per call (see
``_load_settings`` in ``aifred/plugins/tools/vision/__init__.py``), so
edits propagate without a restart.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import reflex as rx

logger = logging.getLogger(__name__)


_VISION_SETTINGS_PATH = (
    Path(__file__).parent.parent / "plugins" / "tools" / "vision" / "settings.json"
)


def _load_settings() -> dict[str, Any]:
    if not _VISION_SETTINGS_PATH.exists():
        return {}
    try:
        parsed = json.loads(_VISION_SETTINGS_PATH.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("vision settings.json unreadable: %s", e)
        return {}


def _save_settings(data: dict[str, Any]) -> None:
    _VISION_SETTINGS_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


class VisionSettingsMixin(rx.State, mixin=True):
    """UI state for the Vision-Plugin settings modal."""

    vision_settings_open: bool = False
    vision_mode_value: str = "on-demand"
    vision_model_value: str = "qwen3-vl:4b-instruct-q8_0"
    vision_available_models: list[str] = []
    # Gesichts­erkennung an/aus — SSOT in
    # ``plugins/tools/vision/settings.json`` ``face_recognition.enabled``.
    # Wirkt auf den Watcher (run_face_detect_on_motion).
    face_recognition_enabled: bool = True
    # Kontinuierliche (motion-unabhängige) Gesichtserkennung. Default
    # OFF — die Detection läuft dann nur, wenn der Motion-Detector
    # anschlägt (GPU-schonend, für Türsteher-Cam). User schaltet ihn
    # ein, wenn die Cam einen ruhigen Schreibtisch zeigt und Motion
    # nicht zuverlässig triggert.
    face_recognition_continuous: bool = False
    # Aufbewahrungsdauer der Face-Crops + Motion-Frames + Vision-DB-
    # Events in Tagen. Cleanup-Task läuft täglich um 03:00 lokal.
    face_retention_days: int = 14
    # Master-Schalter — wie scharf/unscharf an einer Alarmanlage.
    # Erst wenn ``vigilantia_armed=True`` UND eine Source
    # ``auto_start=True`` hat, läuft beim Boot ein Hintergrund-Watcher.
    # Default OFF — der User schaltet bewusst scharf.
    vigilantia_armed: bool = False
    # Liste der Cams für die „Quellen"-Sektion im Settings-Modal —
    # ein Dict pro Source mit ``id``, ``label``, ``auto_start``,
    # ``motion_min_area_ratio``. Wird beim Modal-Open befüllt aus
    # vision_store + frame_sources.
    vigilantia_sources: list[dict[str, Any]] = []
    # Ob das aktuell konfigurierte VLM-Modell im Ollama-VRAM liegt.
    # Wird beim Page-Load + nach jedem Load/Unload-Toggle frisch von
    # Ollama abgefragt — keine Annahme, dass der State stimmt, wenn
    # jemand außerhalb von AIfred mit Ollama gespielt hat.
    vlm_model_loaded: bool = False
    # Lade/Entlade-Vorgang läuft gerade → Spinner-Optik am Button.
    vlm_model_busy: bool = False

    def _refresh_vision_settings(self) -> None:
        """Lade Plugin-Settings + Ollama-Modellliste in den State.
        Wird sowohl vom Settings-Modal als auch vom Live-Preview-Popup
        gerufen, damit das Modell-Dropdown im Popup-Header auch ohne
        vorheriges Öffnen des Settings-Modals befüllt ist."""
        settings = _load_settings()
        self.vision_mode_value = str(settings.get("vision_mode", "on-demand"))
        vlm = settings.get("vlm", {})
        self.vision_model_value = str(vlm.get("model", "qwen3-vl:4b-instruct-q8_0"))
        fr = settings.get("face_recognition", {}) or {}
        self.face_recognition_enabled = bool(fr.get("enabled", True))
        self.face_recognition_continuous = bool(fr.get("continuous", False))
        rd = fr.get("retention_days")
        if isinstance(rd, (int, float)) and 1 <= rd <= 3650:
            self.face_retention_days = int(rd)
        self.vigilantia_armed = bool(settings.get("vigilantia_armed", False))
        self._reload_vigilantia_sources()
        try:
            from ..lib.ollama_models import list_ollama_vlm_models
            models = [m.name for m in list_ollama_vlm_models()]
            # Only show models actually pulled in Ollama. If discovery
            # returned a real list and the saved selection isn't in it, the
            # model is gone → clear the selection instead of prepending a
            # phantom entry. An empty list means Ollama was unreachable —
            # leave the selection alone, we can't verify it right now.
            if models and self.vision_model_value and self.vision_model_value not in models:
                self.vision_model_value = ""
            self.vision_available_models = models
        except Exception as e:  # noqa: BLE001
            logger.warning("vision settings: ollama discovery failed: %s", e)
            self.vision_available_models = (
                [self.vision_model_value] if self.vision_model_value else []
            )

    @rx.event
    def open_vision_settings(self) -> None:
        """Open the modal (called from the Plugin-Tab gear icon)."""
        self._refresh_vision_settings()
        self.vision_settings_open = True

    @rx.event
    def close_vision_settings(self) -> None:
        """Close the modal (backdrop click or close button)."""
        self.vision_settings_open = False

    @rx.event
    async def set_vision_mode_value(self, value: str) -> None:
        if value not in ("off", "on-demand", "live"):
            return
        self.vision_mode_value = value
        settings = _load_settings()
        settings["vision_mode"] = value
        _save_settings(settings)
        # When the user flips to "live", honour the contract immediately:
        # load the VLM into VRAM with keep_alive=-1 so it stays there.
        # Without this, the model wouldn't appear in nvidia-smi until the
        # first vision_analyze call (or the next calibration run).
        if value == "live":
            try:
                from ..lib.vision_prewarm import prewarm_vlm
                await prewarm_vlm()
            except Exception as e:  # noqa: BLE001
                logger.warning("prewarm on mode-switch failed: %s", e)

    @rx.event
    async def set_vision_model_value(self, value: str) -> None:
        """Modell wechseln + altes Modell aus dem VRAM entladen.
        Sonst hängen beim Hin-und-Her-Schalten zwei Modelle parallel
        im Speicher, bis Ollamas keep_alive abläuft."""
        if not value:
            return
        old_model = self.vision_model_value
        self.vision_model_value = value
        settings = _load_settings()
        settings.setdefault("vlm", {})["model"] = value
        _save_settings(settings)
        if old_model and old_model != value:
            try:
                from ..lib.vision_prewarm import unload_vlm_model
                await unload_vlm_model(old_model)
            except Exception as e:  # noqa: BLE001
                logger.warning("unload of old VLM model failed: %s", e)

    @rx.event
    def set_face_recognition_enabled(self, value: bool) -> None:
        """Toggle für Face-Recognition. Schreibt in
        ``plugins/tools/vision/settings.json`` unter
        ``face_recognition.enabled``. Wirkt beim nächsten
        Watcher-Start (config.run_face_detect_on_motion)."""
        self.face_recognition_enabled = bool(value)
        settings = _load_settings()
        settings.setdefault("face_recognition", {})["enabled"] = bool(value)
        _save_settings(settings)

    @rx.event
    def set_face_recognition_continuous(self, value: bool) -> None:
        """Toggle: Gesichtserkennung kontinuierlich (motion-unabhängig)
        oder nur bei Bewegung. Schreibt in
        ``plugins/tools/vision/settings.json`` unter
        ``face_recognition.continuous``. Wirkt beim nächsten Watcher-
        Start (config.face_recognition_continuous)."""
        self.face_recognition_continuous = bool(value)
        settings = _load_settings()
        settings.setdefault("face_recognition", {})["continuous"] = bool(value)
        _save_settings(settings)

    @rx.event
    def set_face_retention_days(self, value: str) -> None:
        """Tage, die Face-Crops + Motion-Frames + Vision-DB-Events
        aufbewahrt werden. Wirkt beim nächsten Cleanup-Lauf
        (täglich 03:00 lokal)."""
        try:
            days = int(value)
        except (TypeError, ValueError):
            return
        if days < 1 or days > 3650:
            return
        self.face_retention_days = days
        settings = _load_settings()
        settings.setdefault("face_recognition", {})["retention_days"] = days
        _save_settings(settings)

    @rx.var
    def vigilantia_has_armed_source(self) -> bool:
        """True wenn mindestens eine Source ``auto_start=True`` hat —
        die Live-Card zeigt sonst „Keine Cams scharfgeschaltet" statt
        „Ruhig", damit der User nicht denkt, alles laufe schon."""
        for c in self.vigilantia_sources:
            if c.get("auto_start"):
                return True
        return False

    def _reload_vigilantia_sources(self) -> None:
        """Aktuelle Cam-Liste mit auto_start/min_area_ratio aus dem
        Store laden — Quelle für die „Quellen"-Sektion im Settings-
        Modal."""
        try:
            from ..lib.frame_sources import list_all
            from ..lib.vision_store import VisionStore
            store = VisionStore()
            cams: list[dict[str, Any]] = []
            for src in list_all():
                try:
                    info = src.info()
                except Exception:  # noqa: BLE001
                    continue
                stored = store.get_source(info.source_id) or {}
                s = stored.get("settings") or {}
                alias = str(s.get("alias") or "").strip()
                label = alias or info.display_name or info.source_id
                mma = s.get("motion_min_area_ratio")
                mma_val = (
                    float(mma)
                    if isinstance(mma, (int, float)) and 0.001 <= mma <= 0.5
                    else 0.02
                )
                cams.append({
                    "id": info.source_id,
                    "label": label,
                    "available": bool(info.available),
                    "auto_start": bool(stored.get("auto_start", False)),
                    "motion_min_area_ratio": mma_val,
                    "resolution": str(s.get("resolution") or "default"),
                })
            self.vigilantia_sources = cams
        except Exception as e:  # noqa: BLE001
            logger.warning("vigilantia sources load failed: %s", e)
            self.vigilantia_sources = []

    def _upsert_source_with(
        self, source_id: str,
        *, auto_start: bool | None = None,
        settings_patch: dict[str, Any] | None = None,
    ) -> None:
        """Generischer Source-Patch: erhält bestehende Felder, ändert
        nur das was übergeben wurde. Wird vom auto_start- und
        motion_min-Setter genutzt."""
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
        new_settings = dict(existing.get("settings", {})) if existing else {}
        if settings_patch:
            new_settings.update(settings_patch)
        store.upsert_source(
            source_id=source_id,
            display_name=str(display_name or source_id),
            kind=str(kind or "webcam"),
            prompt_context=str(existing.get("prompt_context", "")) if existing else "",
            position=str(existing.get("position", "")) if existing else "",
            auto_start=bool(
                auto_start if auto_start is not None
                else (existing.get("auto_start", False) if existing else False)
            ),
            sensitivity=str(existing.get("sensitivity", "medium")) if existing else "medium",
            settings=new_settings,
        )

    @rx.event
    def open_zone_editor(self, source_id: str):
        """Öffnet den standalone JS-Canvas-Zonen-Editor als eigenständiges
        Popup-Fenster (gleiche Mechanik wie die Vigilantia-Live-Vorschau:
        verschiebbares OS-Fenster, fixer Name fokussiert es beim Reklick).
        Ausgeliefert über /api (prefix-unabhängig); source_id als Query-
        Param, der Editor redet per /api/vision/* mit dem Backend."""
        import json
        sid = json.dumps(source_id or "")
        return rx.call_script(
            "window.open('/api/vision/zone-editor?source_id=' + "
            f"encodeURIComponent({sid}),'aifred-zone-editor',"
            "'popup=yes,width=960,height=900,left=200,top=70,"
            "menubar=no,toolbar=no,location=no,status=no')"
        )

    @rx.event
    async def set_vigilantia_source_auto_start(
        self, source_id: str, value: bool
    ) -> None:
        """Pro Cam Hintergrund-Toggle. Schreibt in DB + State,
        startet/stoppt den Watcher live wenn ``armed=True``."""
        if not source_id:
            return
        active = bool(value)
        try:
            self._upsert_source_with(source_id, auto_start=active)
        except Exception as e:  # noqa: BLE001
            logger.warning("auto_start persist failed for %s: %s", source_id, e)
            return
        self.vigilantia_sources = [
            {**c, "auto_start": active} if c["id"] == source_id else c
            for c in self.vigilantia_sources
        ]
        # Live-Effekt nur wenn Master scharf ist.
        if not self.vigilantia_armed:
            return
        try:
            if active:
                from ..lib.vision_autostart import start_background_watcher
                await start_background_watcher(source_id)
            else:
                from ..lib.vision_watcher import get_default_watcher
                await get_default_watcher().stop(source_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("watcher live-toggle failed for %s: %s", source_id, e)

    @rx.event
    def set_vigilantia_source_motion_min(
        self, source_id: str, value: str
    ) -> None:
        """Pro Cam Min-Bewegung in Prozent (0,1–50). Greift beim
        nächsten Watcher-Start."""
        if not source_id:
            return
        try:
            pct = float(str(value).replace(",", "."))
        except (TypeError, ValueError):
            return
        if pct < 0.1:
            pct = 0.1
        if pct > 50:
            pct = 50.0
        mma = pct / 100.0
        try:
            self._upsert_source_with(
                source_id, settings_patch={"motion_min_area_ratio": mma}
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("motion_min persist failed for %s: %s", source_id, e)
            return
        self.vigilantia_sources = [
            {**c, "motion_min_area_ratio": mma} if c["id"] == source_id else c
            for c in self.vigilantia_sources
        ]

    @rx.event
    async def toggle_vigilantia_armed(self) -> None:
        """Master-Schalter umlegen — wie scharf/unscharf an einer
        Alarmanlage. Startet bzw. stoppt alle Hintergrund-Watcher der
        Sources mit ``auto_start=True``.

        Auf der gegenüberliegenden Flanke (User schaltet scharf): die
        Watcher werden mit den aktuellen Plugin-Settings hochgezogen
        (face_recognition.enabled / continuous). Beim Entschärfen wird
        alles gestoppt — auch Watcher die durch UI-Toggles im Vorschau-
        Popup laufen, weil ``armed`` als Master gilt."""
        new_value = not self.vigilantia_armed
        self.vigilantia_armed = new_value
        settings = _load_settings()
        settings["vigilantia_armed"] = new_value
        _save_settings(settings)
        try:
            if new_value:
                from ..lib.vision_autostart import start_all_background_watchers
                await start_all_background_watchers()
            else:
                from ..lib.vision_autostart import stop_all_background_watchers
                await stop_all_background_watchers()
        except Exception as e:  # noqa: BLE001
            logger.warning("vigilantia armed toggle side-effect failed: %s", e)

    @rx.event
    async def refresh_vlm_loaded(self) -> None:
        """Status frisch von Ollama abfragen. Wird vom on_load des
        Vorschau-Popups + vom Open des Vigilantia-Settings-Modals
        gerufen, damit der Power-Button den realen Zustand zeigt."""
        try:
            from ..lib.vision_prewarm import is_vlm_loaded
            self.vlm_model_loaded = await is_vlm_loaded(self.vision_model_value)
        except Exception as e:  # noqa: BLE001
            logger.debug("refresh_vlm_loaded failed: %s", e)
            self.vlm_model_loaded = False

    @rx.event
    async def toggle_vlm_model_loaded(self) -> None:
        """Power-Toggle: Modell laden ↔ entladen via Ollama. Vor dem
        Toggle wird der echte Status abgefragt (idempotent — wenn der
        State falsch war, gleicht er sich aus)."""
        if self.vlm_model_busy:
            return
        model = self.vision_model_value
        if not model:
            return
        self.vlm_model_busy = True
        try:
            from ..lib.vision_prewarm import (
                is_vlm_loaded, prewarm_vlm, unload_vlm_model,
            )
            currently_loaded = await is_vlm_loaded(model)
            if currently_loaded:
                await unload_vlm_model(model)
                self.vlm_model_loaded = False
            else:
                ok = await prewarm_vlm()
                self.vlm_model_loaded = bool(ok)
        except Exception as e:  # noqa: BLE001
            logger.warning("vlm toggle failed: %s", e)
        finally:
            self.vlm_model_busy = False

    @rx.event
    def rescan_vision_models(self) -> None:
        """Re-run Ollama discovery (after the user did `ollama pull` externally)."""
        try:
            from ..lib.ollama_models import list_ollama_vlm_models
            models = [m.name for m in list_ollama_vlm_models()]
            # See _refresh_vision_settings: only show pulled models, clear a
            # stale selection, never prepend a phantom. Empty list (Ollama
            # unreachable) leaves the selection untouched.
            if models and self.vision_model_value and self.vision_model_value not in models:
                self.vision_model_value = ""
            self.vision_available_models = models
        except Exception as e:  # noqa: BLE001
            logger.warning("vision settings rescan failed: %s", e)
