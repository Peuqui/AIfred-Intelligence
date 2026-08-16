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


def _hhmm(value: Any) -> str:
    """Zeitfenster-Wert auf ``"HH:MM"`` normalisieren (Legacy-Stunden-Ints →
    ``"HH:00"``). Nutzt die SSoT :func:`vision_autostart.schedule_minutes`."""
    from ..lib.vision_autostart import schedule_minutes
    mins = schedule_minutes(value)
    return f"{mins // 60:02d}:{mins % 60:02d}"


def _fmt_interval(value: Any) -> str:
    """Sekunden-Wert als Select-Value formatieren (``1.0`` -> ``"1"``,
    passend zu den ``value``-Strings in ``vision_event_interval_options``)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "1"
    return str(int(v)) if v == int(v) else f"{v:g}"


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


# Alarmierbare Event-Typen pro Kamera (Default: alle aktiv). SSoT auch für die
# Reihenfolge der Checkboxen in der UI.
_DEFAULT_ALERT_TYPES = ["person", "vehicle", "animal", "face"]

# Alert-Routing-Kategorien für die Regeln-UI (Kategorie → i18n-Label). SSoT für
# Reihenfolge + welche Kategorien überhaupt konfigurierbar sind. Diese matchen
# die ``category`` der AlertEvents (siehe vision_alerts).
_ALERT_RULE_CATEGORIES = [
    ("person", "alert_cat_person"),
    ("vehicle", "alert_cat_vehicle"),
    ("animal", "alert_cat_animal"),
    ("face_known", "alert_cat_face_known"),
    ("face_unsure", "alert_cat_face_unsure"),
    ("face_unknown", "alert_cat_face_unknown"),
]


def _alert_rules_path() -> Path:
    from ..lib.config import DATA_DIR
    return DATA_DIR / "alert_rules.json"


def _load_alert_rules() -> list[dict[str, Any]]:
    path = _alert_rules_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("alert_rules.json unreadable: %s", e)
        return []


def _save_alert_rules(rules: list[dict[str, Any]]) -> None:
    _alert_rules_path().write_text(
        json.dumps(rules, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


class VisionSettingsMixin(rx.State, mixin=True):
    """UI state for the Vision-Plugin settings modal."""

    vision_settings_open: bool = False
    # Presets für den Pro-Kamera "Min. Event-Abstand"-Regler (Hintergrund-
    # Watcher). Bewusst eigene, gröbere Stufen als das Live-Vorschau-Popup
    # (``vision_preview_face_throttle_options``, 0,1-3s für Türsteher-
    # Modus) — der Hintergrund läuft dauerhaft, hier ist der sinnvolle
    # Bereich 1-10s statt Zehntelsekunden.
    vision_event_interval_options: list[dict[str, str]] = [
        {"value": "1", "label": "1 s"},
        {"value": "2", "label": "2 s"},
        {"value": "3", "label": "3 s"},
        {"value": "5", "label": "5 s"},
        {"value": "10", "label": "10 s"},
    ]
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
    # YOLO-Körper-/Personen-Erkennung pro Motion-Event. SSoT aus
    # ``settings.json`` ``watch.run_person_detect_on_motion``. Läuft
    # parallel zur Gesichtserkennung (kein Gate) — sowohl im Hintergrund-
    # Watcher (Auge) als auch im Live-Preview, wenn Face aktiv ist.
    person_detect_enabled: bool = False
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
    # ``resolution``. Wird beim Modal-Open befüllt aus vision_store +
    # frame_sources. Bewegungs-Schwelle wird im Zonen-Editor getunt.
    vigilantia_sources: list[dict[str, Any]] = []
    # Plain var, deliberately NOT a computed var: a computed iterating the
    # mutable sources list re-marks the list dirty on every evaluation
    # (MutableProxy), creating a self-sustaining dirty cycle — both vars
    # were pushed in EVERY 500ms tick delta to all tabs, wiping any text
    # selection in chat bubbles. Updated via _update_has_armed_source().
    vigilantia_has_armed_source: bool = False
    # Ob das aktuell konfigurierte VLM-Modell im Ollama-VRAM liegt.
    # Wird beim Page-Load + nach jedem Load/Unload-Toggle frisch von
    # Ollama abgefragt — keine Annahme, dass der State stimmt, wenn
    # jemand außerhalb von AIfred mit Ollama gespielt hat.
    vlm_model_loaded: bool = False
    # Lade/Entlade-Vorgang läuft gerade → Spinner-Optik am Button.
    vlm_model_busy: bool = False

    # ── RTSP-Kamera-Verwaltung ───────────────────────────────────────
    # Bestehende RTSP-Kameras erscheinen als normale Quellen-Karte (mit
    # Verbindungs-Stecker); dieses Formular legt neue an / bearbeitet die
    # Verbindung einer bestehenden.
    # Add/Edit-Formular — alle Felder als String/Bool fürs Binding.
    rtsp_form: dict[str, Any] = {}
    rtsp_form_open: bool = False
    # Name der gerade bearbeiteten Kamera ("" = neue Kamera anlegen).
    rtsp_form_editing: str = ""
    rtsp_form_error: str = ""

    # ── Alert-Routing-Regeln (Kategorie → Kanäle/VLM/Cooldown) ────────
    # Eine Zeile je Kategorie für die Regeln-UI; gespiegelt aus
    # data/alert_rules.json. {category, label_key, sinks(list), vlm, cooldown}.
    alert_rules_ui: list[dict[str, Any]] = []
    # Alle verfügbaren Channel-Plugins (kanal-agnostisch via plugin_registry):
    # {name, display_name}. Neue Channels tauchen automatisch auf.
    alert_channels: list[dict[str, str]] = []

    def _refresh_vision_settings(self) -> None:
        """Lade Plugin-Settings + Ollama-Modellliste in den State.
        Wird sowohl vom Settings-Modal als auch vom Live-Preview-Popup
        gerufen, damit das Modell-Dropdown im Popup-Header auch ohne
        vorheriges Öffnen des Settings-Modals befüllt ist."""
        settings = _load_settings()
        # vision_mode is now only on-demand/live — the on/off master is the
        # Watcher (vigilantia_armed). Migrate any legacy "off" to on-demand.
        _vm = str(settings.get("vision_mode", "on-demand")).lower().strip()
        self.vision_mode_value = "on-demand" if _vm not in ("on-demand", "live") else _vm
        vlm = settings.get("vlm", {})
        self.vision_model_value = str(vlm.get("model", "qwen3-vl:4b-instruct-q8_0"))
        fr = settings.get("face_recognition", {}) or {}
        self.face_recognition_enabled = bool(fr.get("enabled", True))
        self.face_recognition_continuous = bool(fr.get("continuous", False))
        watch = settings.get("watch", {}) or {}
        self.person_detect_enabled = bool(watch.get("run_person_detect_on_motion", False))
        rd = fr.get("retention_days")
        if isinstance(rd, (int, float)) and 1 <= rd <= 3650:
            self.face_retention_days = int(rd)
        self.vigilantia_armed = bool(settings.get("vigilantia_armed", False))
        self._reload_vigilantia_sources()
        self._discover_vision_models()

    def _discover_vision_models(self) -> None:
        """VLM-Discovery für das Vigilantia-Dropdown (SSOT).

        Primär llama-swap-Modelle mit ``-visiond``-Describer-Profil —
        der aktive Pfad seit dem Vision-Umbau (analyze_sequence löst
        jede Auswahl auf das Describer-Profil auf). Dahinter Ollama-VLMs
        ohne llama-swap-Pendant (Fallback-Pfad für Setups ohne
        Describer-Profile). Dubletten desselben Modells in beiden
        Backends erscheinen nur einmal, als llama-swap-Name.

        Ist die gespeicherte Auswahl nicht in der Liste, wird sie
        namens-normalisiert gematcht (Ollama-Schreibweise ↔ llama-swap-
        Name) und sonst geleert. Leere Liste = keine Quelle erreichbar →
        Auswahl unangetastet lassen."""
        from ..lib.vision_routing import _normalize

        models: list[str] = []
        suffix = "-visiond"
        try:
            from ..lib.calibration.llamaswap_io import parse_llamaswap_config
            from ..lib.config import LLAMASWAP_CONFIG_PATH
            swap_models = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
            models = sorted(
                mid[: -len(suffix)]
                for mid in swap_models if mid.endswith(suffix)
            )
        except (OSError, ValueError) as e:
            logger.warning("vision settings: llama-swap discovery failed: %s", e)
        try:
            from ..lib.ollama_models import list_ollama_vlm_models
            seen = {_normalize(m) for m in models}
            models.extend(
                m.name for m in list_ollama_vlm_models()
                if _normalize(m.name) not in seen
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("vision settings: ollama discovery failed: %s", e)

        if not models:
            self.vision_available_models = (
                [self.vision_model_value] if self.vision_model_value else []
            )
            return
        if self.vision_model_value and self.vision_model_value not in models:
            target = _normalize(self.vision_model_value)
            match = next((m for m in models if _normalize(m) == target), None)
            # Nur die ANZEIGE auf den gelisteten Namen mappen — die
            # Settings behalten den gespeicherten Wert, bis der User
            # aktiv wechselt (analyze_sequence löst beide Schreibweisen
            # identisch auf).
            self.vision_model_value = match or ""
        self.vision_available_models = models

    @rx.event
    def open_vision_settings(self) -> None:
        """Open the modal (called from the Plugin-Tab gear icon)."""
        self._refresh_vision_settings()
        self._reload_alert_rules()
        self.vision_settings_open = True

    @rx.event
    def close_vision_settings(self) -> None:
        """Close the modal (backdrop click or close button)."""
        self.vision_settings_open = False

    @rx.event
    async def set_vision_mode_value(self, value: str) -> None:
        # "off" is gone — on/off is the Watcher (the eye). Mode is only the
        # VLM residency choice while armed.
        if value not in ("on-demand", "live"):
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
            # Ollama-Unload nur für Ollama-Modelle: Läuft das alte Modell
            # als llama-swap-Describer, verdrängt die vision-Gruppe
            # (swap: true) es beim Laden des neuen selbst.
            from ..lib.vision_routing import visiond_profile_for
            if visiond_profile_for(old_model) is None:
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

    def _update_has_armed_source(self) -> None:
        """True wenn mindestens eine Source ``auto_start=True`` hat —
        die Live-Card zeigt sonst „Keine Cams scharfgeschaltet" statt
        „Ruhig". Wird nach jeder Zuweisung von vigilantia_sources
        aufgerufen (siehe Kommentar an der Var-Deklaration)."""
        armed = any(c.get("auto_start") for c in self.vigilantia_sources)
        if armed != self.vigilantia_has_armed_source:
            self.vigilantia_has_armed_source = armed

    # Recompute when a plugin toggle changes (the FS status can't be a dep, so
    # tool_plugin_toggles is the proxy); explicit deps also avoid the lazy-import
    # auto-dep warning.
    @rx.var(deps=["tool_plugin_toggles"], auto_deps=False)
    def vision_plugin_enabled(self) -> bool:
        """True if the Vision plugin is enabled (present in tools/). The eye
        toggle is greyed out when this is False — without the plugin the
        Watcher can't run."""
        from ..lib.plugin_registry import is_plugin_enabled
        return is_plugin_enabled("vision")

    def _reload_vigilantia_sources(self) -> None:
        """Aktuelle Cam-Liste mit auto_start/min_area_ratio aus dem
        Store laden — Quelle für die „Quellen"-Sektion im Settings-
        Modal."""
        try:
            from ..lib.frame_sources import list_all
            from ..lib.vision_profiles import DEFAULT_MIN_EVENT_INTERVAL_SEC
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
                cams.append({
                    "id": info.source_id,
                    "label": label,
                    "alias": alias,
                    "hardware_name": str(info.display_name or info.source_id),
                    "is_rtsp": str(info.source_id).startswith("cam/rtsp_"),
                    "available": bool(info.available),
                    "auto_start": bool(stored.get("auto_start", False)),
                    "alerts_enabled": bool(s.get("alerts_enabled", True)),
                    "alert_types": [
                        str(x) for x in s.get("alert_types", _DEFAULT_ALERT_TYPES)
                    ],
                    "quiet_enabled": bool(s.get("quiet_enabled", False)),
                    "quiet_start": str(s.get("quiet_start", 22)),
                    "quiet_end": str(s.get("quiet_end", 6)),
                    "schedule_enabled": bool(s.get("schedule_enabled", False)),
                    # Auf "HH:MM" normalisieren (Legacy-Stunden-Ints werden zu
                    # "HH:00") — der UI-time-Input erwartet dieses Format.
                    "schedule_start": _hhmm(s.get("schedule_start", "18:00")),
                    "schedule_end": _hhmm(s.get("schedule_end", "08:00")),
                    "resolution": str(s.get("resolution") or "default"),
                    "event_interval": _fmt_interval(
                        s.get("min_event_interval_sec", DEFAULT_MIN_EVENT_INTERVAL_SEC)
                    ),
                })
            # Assign only on real change: the 500ms feed tick calls this
            # whenever the list is empty (always true with the vision
            # plugin disabled) — an unconditional `= []` re-assignment
            # marks the var dirty EVERY tick, pushing a delta to all tabs
            # twice a second (which also wipes text selections).
            if cams != self.vigilantia_sources:
                self.vigilantia_sources = cams
        except Exception as e:  # noqa: BLE001
            logger.warning("vigilantia sources load failed: %s", e)
            if self.vigilantia_sources:
                self.vigilantia_sources = []
        self._update_has_armed_source()

    def _set_source_auto_start(self, source_id: str, active: bool) -> None:
        """Hintergrund-Toggle einer Quelle persistieren, alle übrigen Felder
        (settings, prompt_context …) bleiben erhalten. Legt die Quelle mit
        Defaults an, falls sie noch nicht im Store existiert."""
        from ..lib.frame_sources import get as get_source
        from ..lib.vision_store import VisionStore
        src = get_source(source_id)
        VisionStore().update_source_fields(
            source_id,
            fallback_display_name=src.display_name if src else source_id,
            fallback_kind=src.kind if src else "webcam",
            auto_start=active,
        )

    @rx.event
    def open_zone_editor(self, source_id: str):
        """Öffnet den standalone JS-Canvas-Zonen-Editor als eigenständiges
        Popup-Fenster (gleiche Mechanik wie die Vigilantia-Live-Vorschau:
        verschiebbares OS-Fenster, fixer Name fokussiert es beim Reklick).
        Ausgeliefert über /api (prefix-unabhängig); source_id als Query-
        Param, der Editor redet per /api/vision/* mit dem Backend.

        Fenster-Geometrie: der Editor schreibt Position+Größe beim
        Schließen nach localStorage ('aifred-zone-editor-geom'); hier wird
        sie gelesen, damit das Popup dort wieder aufgeht, wo der User es
        zuletzt hingeschoben hat (Defaults nur beim allerersten Mal)."""
        import json
        sid = json.dumps(source_id or "")
        return rx.call_script(
            "(function(){"
            "var g={};"
            "try{g=JSON.parse(localStorage.getItem('aifred-zone-editor-geom'))||{};}catch(e){}"
            "var f='popup=yes,menubar=no,toolbar=no,location=no,status=no'"
            "+',width='+(g.width||960)+',height='+(g.height||970)"
            "+',left='+(g.left!=null?g.left:200)+',top='+(g.top!=null?g.top:55);"
            "window.open('/api/vision/zone-editor?source_id=' + "
            f"encodeURIComponent({sid})"
            ",'aifred-zone-editor',f);"
            "})()"
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
            self._set_source_auto_start(source_id, active)
        except Exception as e:  # noqa: BLE001
            logger.warning("auto_start persist failed for %s: %s", source_id, e)
            return
        self.vigilantia_sources = [
            {**c, "auto_start": active} if c["id"] == source_id else c
            for c in self.vigilantia_sources
        ]
        self._update_has_armed_source()
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
    def set_vigilantia_source_alias(self, source_id: str, value: str) -> None:
        """Kameranamen (Alias) aus der Settings-Quellen-Karte setzen.

        Persistiert in ``vision_store.sources.settings.alias`` (SSoT, dieselbe
        Stelle wie früher das Vorschau-Feld) und zieht beide Quell-Listen nach:
        die Settings-Karte UND das read-only Namens-Schild in der Live-
        Vorschau. Leerer Wert löscht den Alias → Fallback auf den Hardware-
        Namen."""
        if not source_id:
            return
        new_alias = value.strip() if isinstance(value, str) else ""
        try:
            self._persist_source_alias(source_id, new_alias)  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            logger.warning("alias persist failed for %s: %s", source_id, e)
            return
        self.vigilantia_sources = [
            {**c, "alias": new_alias,
             "label": new_alias or str(c.get("hardware_name") or c["id"])}
            if c["id"] == source_id else c
            for c in self.vigilantia_sources
        ]
        # Live-Vorschau-Schild mitziehen, falls die Quelle dort gelistet ist.
        if hasattr(self, "vision_preview_sources"):
            from ._vision_preview_mixin import _label_from
            self.vision_preview_sources = [
                {**e, "alias": new_alias, "label": _label_from(e, new_alias)}
                if e["id"] == source_id else e
                for e in self.vision_preview_sources
            ]

    def _persist_source_setting(self, source_id: str, key: str, value: Any) -> None:
        """Einen Schlüssel in ``sources.settings`` mergen, alle übrigen Felder
        (auto_start, prompt_context, andere settings …) bleiben erhalten. Legt
        die Quelle mit Defaults an, falls sie noch nicht im Store existiert."""
        from ..lib.frame_sources import get as get_source
        from ..lib.vision_store import VisionStore
        store = VisionStore()
        src = get_source(source_id)
        existing = store.get_source(source_id)
        settings = dict(existing.get("settings", {})) if existing else {}
        settings[key] = value
        store.update_source_fields(
            source_id,
            fallback_display_name=src.display_name if src else source_id,
            fallback_kind=src.kind if src else "webcam",
            settings=settings,
        )

    @rx.event
    def set_vigilantia_source_alerts(self, source_id: str, value: bool) -> None:
        """Pro-Kamera Push-Alerts an/aus (``sources.settings.alerts_enabled``).
        Aus = die Kamera erkennt/speichert weiter, schickt aber keine
        proaktiven Benachrichtigungen (Anti-Spam)."""
        if not source_id:
            return
        active = bool(value)
        try:
            self._persist_source_setting(source_id, "alerts_enabled", active)
        except Exception as e:  # noqa: BLE001
            logger.warning("alerts_enabled persist failed for %s: %s", source_id, e)
            return
        self.vigilantia_sources = [
            {**c, "alerts_enabled": active} if c["id"] == source_id else c
            for c in self.vigilantia_sources
        ]

    @rx.event
    def set_vigilantia_alert_type(
        self, source_id: str, alert_type: str, enabled: bool
    ) -> None:
        """Pro-Kamera einen Alarm-Event-Typ (person/vehicle/animal/face) an/aus.
        Persistiert als ``sources.settings.alert_types`` (Liste)."""
        if not source_id or alert_type not in _DEFAULT_ALERT_TYPES:
            return
        cam = next((c for c in self.vigilantia_sources if c["id"] == source_id), None)
        current = list(cam.get("alert_types", _DEFAULT_ALERT_TYPES)) if cam else list(_DEFAULT_ALERT_TYPES)
        if enabled and alert_type not in current:
            current.append(alert_type)
        elif not enabled and alert_type in current:
            current.remove(alert_type)
        try:
            self._persist_source_setting(source_id, "alert_types", current)
        except Exception as e:  # noqa: BLE001
            logger.warning("alert_types persist failed for %s: %s", source_id, e)
            return
        self.vigilantia_sources = [
            {**c, "alert_types": current} if c["id"] == source_id else c
            for c in self.vigilantia_sources
        ]

    @rx.event
    def set_vigilantia_quiet(self, source_id: str, field: str, value: Any) -> None:
        """Pro-Kamera Ruhezeit setzen. ``field`` ∈ quiet_enabled / quiet_start /
        quiet_end. Start/Ende werden auf 0–23 normalisiert."""
        if not source_id or field not in ("quiet_enabled", "quiet_start", "quiet_end"):
            return
        if field == "quiet_enabled":
            stored_val: Any = bool(value)
            display_val: Any = bool(value)
        else:
            try:
                hour = max(0, min(23, int(str(value).strip())))
            except (TypeError, ValueError):
                hour = 0
            stored_val = hour
            display_val = str(hour)
        try:
            self._persist_source_setting(source_id, field, stored_val)
        except Exception as e:  # noqa: BLE001
            logger.warning("quiet persist failed for %s: %s", source_id, e)
            return
        self.vigilantia_sources = [
            {**c, field: display_val} if c["id"] == source_id else c
            for c in self.vigilantia_sources
        ]

    @rx.event
    def set_vigilantia_event_interval(self, source_id: str, value: str) -> None:
        """Pro-Kamera Mindestabstand zwischen Detection-Events (Hintergrund-
        Watcher). Greift erst beim nächsten (Re-)Start des Watchers für
        diese Quelle — bei laufender Überwachung also nach einem Toggle
        aus/an oder dem nächsten App-Neustart."""
        if not source_id:
            return
        try:
            sec = max(0.1, min(60.0, float(value)))
        except (TypeError, ValueError):
            return
        try:
            self._persist_source_setting(source_id, "min_event_interval_sec", sec)
        except Exception as e:  # noqa: BLE001
            logger.warning("event-interval persist failed for %s: %s", source_id, e)
            return
        display_val = _fmt_interval(sec)
        self.vigilantia_sources = [
            {**c, "event_interval": display_val} if c["id"] == source_id else c
            for c in self.vigilantia_sources
        ]

    @rx.event
    async def set_vigilantia_schedule(
        self, source_id: str, field: str, value: Any
    ) -> None:
        """Pro-Kamera Aktiv-Zeitfenster (scheduled Scharfschalten). ``field`` ∈
        schedule_enabled / schedule_start / schedule_end. Anders als die
        Ruhezeit (die nur Alerts unterdrückt) schaltet das die Überwachung der
        Kamera an/aus — greift sofort über den schedule_supervisor."""
        if not source_id or field not in (
            "schedule_enabled", "schedule_start", "schedule_end"
        ):
            return
        if field == "schedule_enabled":
            stored_val: Any = bool(value)
            display_val: Any = bool(value)
        else:
            # value = "HH:MM" (HTML-time-Input) oder Legacy-Stunde → über die
            # SSoT auf Minuten normalisieren und als "HH:MM" speichern/anzeigen.
            from ..lib.vision_autostart import schedule_minutes
            mins = schedule_minutes(value)
            norm = f"{mins // 60:02d}:{mins % 60:02d}"
            stored_val = norm
            display_val = norm
        try:
            self._persist_source_setting(source_id, field, stored_val)
        except Exception as e:  # noqa: BLE001
            logger.warning("schedule persist failed for %s: %s", source_id, e)
            return
        self.vigilantia_sources = [
            {**c, field: display_val} if c["id"] == source_id else c
            for c in self.vigilantia_sources
        ]
        # Sofort anwenden: Watcher je nach Fenster + global-armed starten/stoppen.
        try:
            from ..lib.vision_autostart import (
                _schedule_active_now,
                start_background_watcher,
            )
            from ..lib.vision_watcher import get_default_watcher
            if self.vigilantia_armed:
                rec = next(
                    (c for c in self.vigilantia_sources if c["id"] == source_id), None
                )
                settings = {
                    "schedule_enabled": bool(rec.get("schedule_enabled")) if rec else False,
                    # Roh-Wert ("HH:MM" oder Legacy) durchreichen — _schedule_active_now
                    # parst selbst über schedule_minutes.
                    "schedule_start": rec.get("schedule_start", "00:00") if rec else "00:00",
                    "schedule_end": rec.get("schedule_end", "00:00") if rec else "00:00",
                }
                active = _schedule_active_now(settings)
                running = get_default_watcher().is_running(source_id)
                # auto_start gate — mirrors schedule_supervisor: a camera whose
                # background watcher is disabled must not be started here.
                armed_source = bool(rec.get("auto_start")) if rec else False
                if active and not running and armed_source:
                    await start_background_watcher(source_id)
                elif not active and running:
                    await get_default_watcher().stop(source_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("schedule live-apply failed for %s: %s", source_id, e)

    # ── Alert-Routing-Regeln ─────────────────────────────────────────

    def _reload_alert_rules(self) -> None:
        """Regeln aus data/alert_rules.json in die UI-Liste spiegeln (eine Zeile
        je Kategorie) und die verfügbaren Channels generisch entdecken
        (plugin_registry) — kein Kanal hartcodiert."""
        try:
            from ..lib.plugin_registry import all_channels
            self.alert_channels = [
                {"name": str(n), "display_name": str(c.display_name)}
                for n, c in sorted(all_channels().items())
            ]
        except Exception as e:  # noqa: BLE001
            logger.warning("channel discovery failed: %s", e)
            self.alert_channels = []
        by_cat = {
            str(r.get("category")): r for r in _load_alert_rules()
            if isinstance(r, dict)
        }
        out: list[dict[str, Any]] = []
        for cat, label_key in _ALERT_RULE_CATEGORIES:
            r = by_cat.get(cat) or {}
            out.append({
                "category": cat,
                "label_key": label_key,
                "sinks": [str(s) for s in (r.get("sinks") or [])],
                "vlm": r.get("compose") == "vlm",
                "cooldown": str(int(r.get("min_interval_sec", 300) or 0)),
            })
        self.alert_rules_ui = out

    def _apply_alert_rule_change(self, category: str, mutator: Any) -> None:
        """Regel der Kategorie laden/anlegen, ``mutator(rule)`` anwenden,
        speichern, Dispatcher live neu laden, UI-Liste auffrischen."""
        if category not in {c for c, _ in _ALERT_RULE_CATEGORIES}:
            return
        rules = _load_alert_rules()
        rule = next(
            (r for r in rules if isinstance(r, dict) and r.get("category") == category),
            None,
        )
        if rule is None:
            rule = {
                "rule_id": f"vision-{category}",
                "producer": "vision",
                "category": category,
                "source_id": None,
                "sinks": [],
                "min_interval_sec": 300,
            }
            rules.append(rule)
        mutator(rule)
        _save_alert_rules(rules)
        try:
            from ..lib.alert_bus import reload_rules
            reload_rules()
        except Exception as e:  # noqa: BLE001
            logger.warning("alert dispatcher reload failed: %s", e)
        self._reload_alert_rules()

    @rx.event
    def set_alert_rule(self, category: str, field: str, value: Any) -> None:
        """VLM-Bildbeschreibung an/aus oder Cooldown einer Kategorie ändern."""
        if field not in ("vlm", "cooldown"):
            return

        def _mut(rule: dict[str, Any]) -> None:
            if field == "vlm":
                if bool(value):
                    rule["compose"] = "vlm"
                else:
                    rule.pop("compose", None)
            else:
                try:
                    rule["min_interval_sec"] = max(0, int(str(value).strip()))
                except (TypeError, ValueError):
                    pass

        self._apply_alert_rule_change(category, _mut)

    @rx.event
    def toggle_alert_sink(self, category: str, channel: str, enabled: bool) -> None:
        """Einen Kanal (channel-agnostisch, beliebiges Channel-Plugin) für eine
        Kategorie an/aus — landet in der ``sinks``-Liste der Regel."""
        if not channel:
            return

        def _mut(rule: dict[str, Any]) -> None:
            sinks = set(rule.get("sinks") or [])
            if bool(enabled):
                sinks.add(channel)
            else:
                sinks.discard(channel)
            rule["sinks"] = sorted(sinks)

        self._apply_alert_rule_change(category, _mut)

    # ── RTSP-Kamera-Verwaltung (Phase 2) ─────────────────────────────

    @staticmethod
    def _env_path() -> str:
        return str(Path(__file__).resolve().parents[2] / ".env")

    @staticmethod
    def _empty_rtsp_form() -> dict[str, Any]:
        return {
            "name": "", "host": "", "port": "554", "path": "",
            "cred": "", "profile": "webcam", "api_port": "443",
            "face_channel": "", "user": "", "password": "",
        }

    @rx.event
    def open_rtsp_camera_new(self) -> None:
        self.rtsp_form = self._empty_rtsp_form()
        self.rtsp_form_editing = ""
        self.rtsp_form_error = ""
        self.rtsp_form_open = True

    @rx.event
    def open_rtsp_camera_edit_by_source(self, source_id: str) -> None:
        """Verbindungs-Formular für eine bestehende RTSP-Quelle öffnen —
        ausgelöst über den Stecker an der Quellen-Karte. Der Name der Kamera
        wird über das Karten-Feld (Alias) verwaltet, NICHT hier; das Formular
        zeigt nur die Verbindungs-Felder. ``rtsp_form_editing`` hält den
        stabilen Config-Namen (= Basis der source_id), der unverändert bleibt."""
        from ..lib.frame_sources.rtsp_source import find_camera_config
        entry = find_camera_config(source_id) or {}
        form = self._empty_rtsp_form()
        if entry:
            fc = entry.get("face_channel")
            form.update({
                "host": str(entry.get("host", "")),
                "port": str(entry.get("port", 554)),
                "path": str(entry.get("path", "")),
                "cred": str(entry.get("cred", "")),
                "profile": str(entry.get("profile") or "webcam"),
                "api_port": str(entry.get("api_port", 443)),
                "face_channel": "" if fc is None else str(fc),
            })
            # Credentials werden NICHT vorbefüllt — Secrets bleiben verdeckt.
        self.rtsp_form = form
        self.rtsp_form_editing = str(entry.get("name", "")) if entry else ""
        self.rtsp_form_error = ""
        self.rtsp_form_open = True

    @rx.event
    def close_rtsp_camera_form(self) -> None:
        self.rtsp_form_open = False
        self.rtsp_form_error = ""

    @rx.event
    def set_rtsp_form_field(self, field: str, value: Any) -> None:
        self.rtsp_form = {**self.rtsp_form, field: value}

    def _persist_rtsp_credentials(self, cred: str, user: str, password: str) -> None:
        """User/Passwort in die .env schreiben (RTSP_<CRED>_USER/PASSWORD) und
        sofort im Broker verfügbar machen. Leere Werte werden übersprungen,
        damit ein leeres Edit-Feld bestehende Secrets nicht löscht."""
        from dotenv import set_key

        from ..lib.credential_broker import broker
        env_path = self._env_path()
        if user:
            set_key(env_path, broker.get_env_key(f"rtsp_{cred}", "user"), user)
            broker.set_runtime(f"rtsp_{cred}", "user", user)
        if password:
            set_key(env_path, broker.get_env_key(f"rtsp_{cred}", "password"), password)
            broker.set_runtime(f"rtsp_{cred}", "password", password)

    @rx.event
    def save_rtsp_camera(self) -> None:
        """Formular validieren, in settings.json ``rtsp_cameras`` schreiben
        (Update nach Name oder Append), Credentials in .env, Quellen neu
        einlesen."""
        f = self.rtsp_form
        editing = self.rtsp_form_editing
        host = str(f.get("host", "")).strip()
        # Beim Bearbeiten bleibt der Config-Name stabil (source_id-Basis); der
        # Anzeigename wird über das Karten-Alias-Feld verwaltet. Beim Anlegen
        # ist der Formular-Name der neue Config-Name.
        name = editing if editing else str(f.get("name", "")).strip()
        if not name or not host:
            self.rtsp_form_error = (
                "Host ist Pflicht." if editing else "Name und Host sind Pflicht."
            )
            return

        def _int(v: Any, default: int) -> int:
            try:
                return int(str(v).strip())
            except (TypeError, ValueError):
                return default

        entry: dict[str, Any] = {
            "name": name, "host": host, "port": _int(f.get("port"), 554),
        }
        path = str(f.get("path", "")).strip().lstrip("/")
        if path:
            entry["path"] = path
        cred = str(f.get("cred", "")).strip()
        if cred:
            entry["cred"] = cred
        profile = str(f.get("profile") or "webcam")
        entry["profile"] = profile
        if profile == "ai_camera":
            entry["api_port"] = _int(f.get("api_port"), 443)
            fc = str(f.get("face_channel", "")).strip()
            if fc:
                entry["face_channel"] = _int(fc, 1)

        settings = _load_settings()
        cams = settings.get("rtsp_cameras")
        cams = cams if isinstance(cams, list) else []
        target = self.rtsp_form_editing or name
        new_list: list[Any] = []
        replaced = False
        for c in cams:
            if isinstance(c, dict) and str(c.get("name")) == target:
                new_list.append(entry)
                replaced = True
            else:
                new_list.append(c)
        if not replaced:
            new_list.append(entry)
        settings["rtsp_cameras"] = new_list
        _save_settings(settings)

        if cred:
            try:
                self._persist_rtsp_credentials(
                    cred, str(f.get("user", "")).strip(), str(f.get("password", "")),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("rtsp credential persist failed: %s", e)

        try:
            from ..lib.frame_sources import rescan
            rescan()
        except Exception as e:  # noqa: BLE001
            logger.warning("frame source rescan failed: %s", e)

        self.rtsp_form_open = False
        self.rtsp_form_error = ""
        self._reload_vigilantia_sources()

    @rx.event
    def delete_rtsp_camera(self, name: str) -> None:
        settings = _load_settings()
        cams = settings.get("rtsp_cameras") or []
        settings["rtsp_cameras"] = [
            c for c in cams
            if not (isinstance(c, dict) and str(c.get("name")) == name)
        ]
        _save_settings(settings)
        try:
            from ..lib.frame_sources import rescan
            rescan()
        except Exception as e:  # noqa: BLE001
            logger.warning("frame source rescan failed: %s", e)
        self.rtsp_form_open = False
        self._reload_vigilantia_sources()

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
        # Can't arm without the plugin — the Watcher needs it. The eye is
        # greyed out in this case; guard here too against programmatic calls.
        from ..lib.plugin_registry import is_plugin_enabled
        if not is_plugin_enabled("vision"):
            return
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

    async def force_disarm_vigilantia(self) -> None:
        """Unconditionally disarm the Watcher + stop its background watchers.

        Used when the Vision plugin is disabled: the Watcher can't run without
        the plugin, so it must not stay armed. No-op when already disarmed."""
        if not self.vigilantia_armed:
            return
        self.vigilantia_armed = False
        settings = _load_settings()
        settings["vigilantia_armed"] = False
        _save_settings(settings)
        try:
            from ..lib.vision_autostart import stop_all_background_watchers
            await stop_all_background_watchers()
        except Exception as e:  # noqa: BLE001
            logger.warning("force_disarm_vigilantia side-effect failed: %s", e)

    @rx.event
    async def refresh_vlm_loaded(self) -> None:
        """Status frisch von Ollama abfragen. Wird vom on_load des
        Vorschau-Popups gerufen, damit der Power-Button den realen
        Zustand zeigt."""
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
                # Explizites Laden über den Power-Button MUSS tatsächlich
                # laden — auch im on-demand-Modus, wo prewarm_vlm() sonst ein
                # No-op ist und irreführend True zurückgibt (stiller Fallback).
                # keep_alive_override umgeht diesen Kurzschluss; danach fragen
                # wir den ECHTEN Ollama-Zustand ab, statt dem Return zu trauen.
                keep_alive = str(_load_settings().get("vlm", {}).get("keep_alive", "30m"))
                await prewarm_vlm(keep_alive_override=keep_alive)
                self.vlm_model_loaded = await is_vlm_loaded(model)
        except Exception as e:  # noqa: BLE001
            logger.warning("vlm toggle failed: %s", e)
        finally:
            self.vlm_model_busy = False

    @rx.event
    def rescan_vision_models(self) -> None:
        """Re-run Ollama discovery (after the user did `ollama pull` externally)."""
        self._discover_vision_models()
