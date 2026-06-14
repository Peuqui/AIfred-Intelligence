"""Vision Tool Plugin — Snapshot/Analyze/Watch/Enroll over registered frame sources.

LLM-facing API of the vision pipeline. Heavy lifting (frame capture,
motion detection, face recognition, VLM call) lives in
``aifred.lib.frame_sources`` / ``vision_filters`` / ``vision_analyzer``
/ ``vision_watcher`` — this plugin is just the glue that wraps those
in ``Tool``-instances.

VLM-calls go via Ollama as a side-channel (independent from the active
chat backend on llama-swap) so a snapshot-analyse never swaps out the
running chat model. Settings are loaded fresh per call from
``settings.json`` so the user can tweak prompt/model/thresholds from
the plugin manager without restarting.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ....lib.frame_sources import (
    get as get_source,
    list_all as list_all_sources,
    rescan as rescan_sources,
)
from ....lib.function_calling import Tool
from ....lib.plugin_base import PluginContext, load_tool_description
from ....lib.security import TIER_READONLY, TIER_WRITE_DATA
from ....lib.vision_analyzer import (
    DEFAULT_KEEP_ALIVE,
    DEFAULT_MODEL,
    DEFAULT_NUM_CTX,
    analyze_sequence,
)
from ....lib.vision_filters.face_detect import (
    get_default_detector,
    set_default_detector_kwargs,
)
from ....lib.vision_filters.face_recognize import FaceRecognizer
from ....lib.vision_gpu_select import resolve_gpu_id
from ....lib.vision_store import VisionStore
from ....lib.vision_utils import (
    TOOLCALL_IMAGES_DIR,
    annotate_frame,
    filename_timestamp,
    get_image_url,
    resolve_source_alias,
    resolve_source_label,
    save_image_to_file,
    slugify_for_filename,
    source_overlay_label,
)
from ....lib.vision_watcher import (
    VisionWatcher,
    WatchConfig,
    get_default_watcher,
)

logger = logging.getLogger(__name__)

_PLUGIN_DIR = Path(__file__).parent
_SETTINGS_PATH = _PLUGIN_DIR / "settings.json"

def _load_settings() -> dict[str, Any]:
    """Load plugin settings fresh on every access — file is small, not a hot path."""
    if not _SETTINGS_PATH.exists():
        return {}
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("vision settings.json unreadable: %s", e)
        return {}


def _apply_face_detector_settings() -> None:
    """Wire face-recognition settings (provider, gpu_id, det_size) into the
    module-level default detector. Has effect only before first use.

    ``gpu_id`` accepts an integer, ``"auto"`` (selected via
    ``vision_gpu_select.pick_vlm_gpu()`` — second-best GPU of the top
    compute class so the chat-LLM keeps the fastest one), or ``null``
    for CPU-only.
    """
    cfg = _load_settings().get("face_recognition", {})
    kwargs: dict[str, Any] = {}
    if "providers" in cfg:
        kwargs["providers"] = list(cfg["providers"])
    resolved = resolve_gpu_id(cfg.get("gpu_id"))
    if resolved is None:
        # No GPU available / disabled → drop CUDA from the provider list
        # so onnxruntime doesn't crash trying to allocate on a missing device.
        kwargs["providers"] = [
            p for p in kwargs.get("providers", ["CUDAExecutionProvider", "CPUExecutionProvider"])
            if p != "CUDAExecutionProvider"
        ] or ["CPUExecutionProvider"]
    else:
        kwargs["gpu_id"] = resolved
    if "det_size" in cfg:
        kwargs["det_size"] = int(cfg["det_size"])
    if "model_name" in cfg:
        kwargs["model_name"] = str(cfg["model_name"])
    if kwargs:
        set_default_detector_kwargs(**kwargs)


def _watch_config_from_settings(overrides: dict[str, Any] | None = None) -> WatchConfig:
    cfg = _load_settings().get("watch", {})
    merged: dict[str, Any] = dict(cfg)
    if overrides:
        merged.update(overrides)

    def _f(key: str, default: float) -> float:
        v = merged.get(key, default)
        return float(v) if v is not None else default

    def _i(key: str, default: int) -> int:
        v = merged.get(key, default)
        return int(v) if v is not None else default

    def _b(key: str, default: bool) -> bool:
        v = merged.get(key, default)
        return bool(v) if v is not None else default

    fps = merged.get("fps", merged.get("default_fps", 1.0))
    return WatchConfig(
        fps=float(fps) if fps is not None else 1.0,
        motion_min_area_ratio=_f("motion_min_area_ratio", 0.02),
        motion_history_frames=_i("motion_history_frames", 500),
        motion_var_threshold=_f("motion_var_threshold", 16.0),
        motion_warmup_frames=_i("motion_warmup_frames", 10),
        min_event_interval_sec=_f("min_event_interval_sec", 5.0),
        save_event_frames=_b("save_event_frames", True),
        run_face_detect_on_motion=_b("run_face_detect_on_motion", True),
        run_person_detect_on_motion=_b("run_person_detect_on_motion", False),
        motion_gated=_b("motion_gated", True),
    )


def _store() -> VisionStore:
    return VisionStore()


def _recognizer(store: VisionStore | None = None) -> FaceRecognizer:
    cfg = _load_settings().get("face_recognition", {})
    return FaceRecognizer(
        store or _store(),
        threshold_known=float(cfg.get("threshold_known", 0.5)),
        threshold_unsure=float(cfg.get("threshold_unsure", 0.4)),
    )


def _watcher(store: VisionStore | None = None) -> VisionWatcher:
    return get_default_watcher(store or _store())


def _err(msg: str, **extra: Any) -> str:
    return json.dumps({"success": False, "error": msg, **extra})


def _ok(**payload: Any) -> str:
    return json.dumps({"success": True, **payload})


def _vision_mode() -> str:
    """Read the global vision-mode toggle: ``"off"`` / ``"on-demand"`` / ``"live"``.

    * ``off``       — Vision is disabled; all tools return an error indicating
                      the user must enable it in settings. No VRAM-Reservation
                      during calibration, no watch tasks accepted.
    * ``on-demand`` — Default. Snapshot/analyze run on demand; watch tasks
                      require explicit ``vision_start_watch`` calls. VLM is
                      held with ``keep_alive`` (typically 30 min).
    * ``live``      — Like on-demand, plus VLM is kept permanently loaded
                      (caller patches ``keep_alive=-1`` for analyze calls).
                      Use for Türsteher / always-on surveillance.
    """
    raw = _load_settings().get("vision_mode", "on-demand")
    if not isinstance(raw, str):
        return "on-demand"
    mode = raw.lower().strip()
    if mode not in ("off", "on-demand", "live"):
        logger.warning("invalid vision_mode setting %r — falling back to on-demand", raw)
        return "on-demand"
    return mode


def _err_mode_off() -> str:
    return _err(
        "vision_disabled",
        message=(
            "Vision is disabled in plugin settings. Set vision_mode to "
            "'on-demand' or 'live' to enable image / webcam operations."
        ),
    )


@dataclass
class VisionPlugin:
    name: str = "vision"
    display_name: str = "Vigilantia"
    description: str = (
        "Macht Fotos und kurze Bildbeschreibungen über Webcam oder andere "
        "Bildquellen. Erkennt Bewegung und bekannte Gesichter."
    )
    # Triggers the /vision-settings page (analog to audio_player). The
    # Plugin-Tab gear icon dispatches this state event.
    settings_event_name: str = "open_vision_settings"

    def is_available(self) -> bool:
        # Plugin is always loadable — individual tools fail gracefully
        # when no source is registered. Settings file presence is sufficient.
        return _SETTINGS_PATH.exists()

    def get_prompt_instructions(
        self, lang: str, granted_tools: "set[str] | None" = None
    ) -> str:
        # Kein Hardcoding — atomare Fragmente pro Tool in prompts/<de|en>/.
        from ....lib.plugin_base import load_plugin_instructions
        return load_plugin_instructions(self, lang, granted_tools)

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        is_de = lang.startswith("de")
        mapping = {
            "vision_list_sources": ("Verfügbare Bildquellen werden gesucht …",
                                    "Listing image sources …"),
            "vision_rescan_sources": ("Bildquellen werden neu gescannt …",
                                      "Re-scanning image sources …"),
            "vision_snapshot": ("Foto wird aufgenommen …", "Capturing snapshot …"),
            "vision_analyze": ("Bild wird analysiert …", "Analysing image …"),
            "vision_enroll_face": ("Gesicht wird gespeichert …", "Storing face …"),
            "vision_start_watch": ("Live-Überwachung wird gestartet …",
                                   "Starting live watch …"),
            "vision_stop_watch": ("Live-Überwachung wird gestoppt …",
                                  "Stopping live watch …"),
            "vision_list_active_watches": ("Aktive Überwachungen werden geprüft …",
                                           "Listing active watches …"),
            "vision_query_events": ("Ereignisse werden abgefragt …",
                                    "Querying events …"),
        }
        de_msg, en_msg = mapping.get(tool_name, ("", ""))
        return de_msg if is_de else en_msg

    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        # Globaler Toggle: bei vision_mode=off präsentiert das Plugin gar keine
        # Tools — der LLM sieht das Plugin damit als „nicht verfügbar" und
        # versucht nicht, Bild-Operationen zu starten.
        if _vision_mode() == "off":
            return []
        _apply_face_detector_settings()
        return [
            self._tool_list_sources(ctx),
            self._tool_rescan_sources(ctx),
            self._tool_snapshot(ctx),
            self._tool_analyze(ctx),
            self._tool_enroll_face(ctx),
            self._tool_start_watch(ctx),
            self._tool_stop_watch(ctx),
            self._tool_list_active_watches(ctx),
            self._tool_query_events(ctx),
        ]

    # ── list_sources ─────────────────────────────────────────────

    def _tool_list_sources(self, ctx: PluginContext) -> Tool:
        async def _exec() -> str:
            from ....lib.vision_utils import resolve_source_label
            sources = []
            for src in list_all_sources():
                info = src.info()
                # User-facing Anzeigename über die SSoT (Alias > display_name >
                # source_id) — der Agent sieht "Büro" statt
                # "OmniVision Technologies, Inc. USB Camera".
                sources.append({
                    "source_id": info.source_id,
                    "source_name": resolve_source_label(info.source_id),
                    "hardware_name": info.display_name,
                    "kind": info.kind,
                    "width": info.width,
                    "height": info.height,
                    "fps": info.fps,
                    "available": info.available,
                    "position": info.position,
                    "prompt_context": info.prompt_context,
                })
            return _ok(count=len(sources), sources=sources)

        return Tool(
            name="vision_list_sources",
            description=load_tool_description(__file__, "vision_list_sources"),
            parameters={"type": "object", "properties": {}, "required": []},
            executor=_exec,
            tier=TIER_READONLY,
        )

    # ── rescan_sources ───────────────────────────────────────────

    def _tool_rescan_sources(self, ctx: PluginContext) -> Tool:
        async def _exec() -> str:
            rescan_sources()
            sources = list_all_sources()
            return _ok(
                count=len(sources),
                ids=[s.source_id for s in sources],
                available=[s.source_id for s in sources if s.is_available()],
            )

        return Tool(
            name="vision_rescan_sources",
            description=load_tool_description(__file__, "vision_rescan_sources"),
            parameters={"type": "object", "properties": {}, "required": []},
            executor=_exec,
            tier=TIER_READONLY,
        )

    # ── snapshot ─────────────────────────────────────────────────

    def _tool_snapshot(self, ctx: PluginContext) -> Tool:
        async def _exec(source_id: str, n_frames: int = 1, save: bool = True) -> str:
            import asyncio
            from dataclasses import replace
            # LLMs übergeben gern den Anzeigenamen ("Büro") — auflösen wie
            # in vision_analyze.
            from ....lib.vision_utils import resolve_source_id
            source_id = resolve_source_id(source_id)
            source = get_source(source_id)
            if source is None:
                return _err(f"unknown source: {source_id}")
            if not source.is_available():
                return _err(f"source not available: {source_id}")
            # Per-camera resolution from vision_store (set in the live-
            # preview popup). Tools share the same persistent config as
            # the UI so a 4K outdoor cam stays at 4K for snapshots and
            # a low-res door cam stays low. (0, 0) → driver default.
            from ....lib.vision_utils import resolve_source_resolution
            from ....lib.frame_hub import get_default_hub
            w, h = resolve_source_resolution(source_id)
            n = max(1, min(int(n_frames), 10))
            # Burst spread: a tight loop would grab N near-identical frames.
            # Spacing them by burst_interval_s turns the burst into a real
            # short sequence ("film") that vision_analyze can judge over time.
            interval = float(_load_settings().get("snapshot", {}).get("burst_interval_s", 0.5))
            # Bevorzugt die Kamera-Snap-API (volle Linsen-Auflösung statt
            # RTSP-Substream) — None heißt: nicht konfiguriert/fehlgeschlagen.
            from ....lib.vision_snap import snap_frames as _snap_frames
            frames = await _snap_frames(source_id, n, interval)
            if frames is None:
                try:
                    # Über den FrameHub aufnehmen, nicht source.snapshot() direkt:
                    # ein laufender Watcher hält source._lock für die gesamte
                    # Stream-Dauer — ein direkter snapshot() liefe in den Deadlock.
                    # Der Hub teilt sich den Stream (Timeout 5 s).
                    hub = get_default_hub()
                    frames = []
                    for i in range(n):
                        if i > 0 and interval > 0:
                            await asyncio.sleep(interval)
                        frames.append(await hub.snapshot(source, width=w, height=h))
                except Exception as e:  # noqa: BLE001
                    return _err(f"snapshot failed: {e}")
            # Burn the documentation overlay (name + location + capture time)
            # into every frame. The same stamped image is what the user sees
            # AND what vision_analyze later sends to the VLM (one artifact
            # everywhere — SSOT).
            label = source_overlay_label(source_id)
            frames = [
                replace(f, image_bytes=annotate_frame(
                    f.image_bytes, label, timestamp=f.timestamp))
                for f in frames
            ]
            # Snap-API-Frames kennen ihre Maße nicht (width=0) — fürs
            # Tool-Ergebnis einmal aus dem JPEG dekodieren.
            rw, rh = frames[-1].width, frames[-1].height
            if not rw:
                import cv2
                import numpy as np
                img = cv2.imdecode(
                    np.frombuffer(frames[-1].image_bytes, np.uint8),
                    cv2.IMREAD_COLOR,
                )
                if img is not None:
                    rh, rw = img.shape[:2]
            result: dict[str, Any] = {
                "source_id": source_id,
                "source_name": resolve_source_label(source_id),
                "n_frames": len(frames),
                "timestamp": frames[-1].timestamp.isoformat(timespec="seconds"),
                "width": rw,
                "height": rh,
            }
            if save and ctx.session_id:
                # Filename: <kamera-alias>_<YYYY-MM-DD_HH-MM-SS_mmm>.jpg —
                # readable date/time + ms (unique within the per-session folder,
                # no uuid). The subfolder (toolcall/) already encodes the type.
                _alias = slugify_for_filename(
                    resolve_source_alias(source_id, fallback="cam")
                )
                urls: list[str] = []
                for f in frames:
                    try:
                        fname = f"{_alias}_{filename_timestamp(f.timestamp)}.jpg"
                        path = save_image_to_file(
                            f.image_bytes, ctx.session_id, fname,
                            base_dir=TOOLCALL_IMAGES_DIR,
                        )
                        urls.append(get_image_url(path))
                    except Exception as e:  # noqa: BLE001
                        logger.warning("snapshot save failed: %s", e)
                if urls:
                    # image_urls = the full burst (pass to vision_analyze for a
                    # sequence). image_url = representative (last) frame — the
                    # pipeline pins exactly one image per turn. No markdown echo.
                    result["image_urls"] = urls
                    result["image_url"] = urls[-1]
            return _ok(**result)

        return Tool(
            name="vision_snapshot",
            description=load_tool_description(__file__, "vision_snapshot"),
            parameters={
                "type": "object",
                "properties": {
                    "source_id": {
                        "type": "string",
                        "description": "Source identifier (see vision_list_sources).",
                    },
                    "n_frames": {
                        "type": "integer",
                        "description": "1 = single photo; 2-10 = burst sequence for motion/film.",
                        "default": 1,
                    },
                    "save": {
                        "type": "boolean",
                        "description": "Persist to session image dir.",
                        "default": True,
                    },
                },
                "required": ["source_id"],
            },
            executor=_exec,
            tier=TIER_READONLY,
        )

    # ── analyze ──────────────────────────────────────────────────

    def _tool_analyze(self, ctx: PluginContext) -> Tool:
        async def _exec(
            image_urls: Any,
            prompt: str | None = None,
            source_id: str | None = None,
        ) -> str:
            # LLMs übergeben gern den Anzeigenamen ("Büro") statt der
            # technischen id — auflösen, sonst werden Kamera-Briefing und
            # Event-Logging still übersprungen bzw. falsch verschlagwortet.
            if source_id:
                from ....lib.vision_utils import resolve_source_id
                source_id = resolve_source_id(source_id)
            # Separated capture/analyze: this tool does NOT take a photo. It
            # runs the VLM on ALREADY-captured images (from vision_snapshot, an
            # upload, or any saved image_url). A single url or a list is
            # accepted; a list is sent as a temporal sequence ("film").
            from ....lib.vision_utils import url_to_file_path
            from ....lib.frame_sources import Frame

            if isinstance(image_urls, str):
                urls = [image_urls]
            elif isinstance(image_urls, list):
                urls = [str(u) for u in image_urls if u]
            else:
                urls = []
            if not urls:
                return _err(
                    "image_urls required — pass the image_url(s) returned by "
                    "vision_snapshot (snapshot first, then analyze)."
                )

            frames: list[Frame] = []
            for u in urls[:10]:
                p = url_to_file_path(u)
                if p is None or not p.exists():
                    # Häufigste Ursache: das LLM vertippt das Präfix
                    # ("_upload/", "_/upload/") — der Hinweis gibt ihm die
                    # Chance zur Selbstkorrektur im Retry (kein Auto-Fix,
                    # bewusst kein Fallback).
                    return _err(
                        f"image not found: {u} — pass the url EXACTLY as "
                        f"returned by vision_snapshot, it must start with "
                        f"'/_upload/'. Check for typos in the prefix and retry."
                    )
                try:
                    data = p.read_bytes()
                except OSError as e:  # noqa: BLE001
                    return _err(f"cannot read image {u}: {e}")
                frames.append(Frame(
                    source_id=source_id or "",
                    timestamp=datetime.now(),
                    image_bytes=data,
                ))

            vlm_cfg = _load_settings().get("vlm", {})
            actual_prompt = prompt or vlm_cfg.get(
                "default_prompt", "Beschreibe knapp, was zu sehen ist."
            )
            # Per-camera briefing (prompt_context) — only when the caller passes
            # the source_id from the snapshot result. Optional static context
            # ("Eingang, Tür mit Briefkasten"), prepended to the prompt. Without
            # it the analysis still runs, just without the location hint.
            if source_id:
                try:
                    from ....lib.vision_store import VisionStore
                    _info = VisionStore().get_source(source_id)
                    briefing = (_info or {}).get("prompt_context") or ""
                    if isinstance(briefing, str) and briefing.strip():
                        actual_prompt = f"{briefing.strip()}\n\n{actual_prompt}"
                except Exception as e:  # noqa: BLE001
                    logger.warning("analyze briefing load failed: %s", e)
            # In "live"-Mode (Türsteher/Always-On) override keep_alive auf -1,
            # damit das VLM permanent im VRAM bleibt. Muss int sein — Ollama
            # parsed strings als Duration ("30m") und scheitert an "-1".
            keep_alive: Any = -1 if _vision_mode() == "live" else str(
                vlm_cfg.get("keep_alive", DEFAULT_KEEP_ALIVE)
            )
            # Modellwahl folgt der User-Einstellung (gleiche Kopplungsregel
            # wie der Chat-Vision-Pfad, siehe _chat_mixin): Das Haupt-LLM
            # beschreibt die Bilder NUR, wenn der User Vision-LLM == AIfred-
            # LLM gestellt hat (oder kein Vision-LLM gewählt ist) UND die
            # effektive Variante nativ sehen kann (--mmproj, SSOT
            # model_has_mmproj). Ein abweichend eingestelltes Vision-LLM
            # gewinnt — ein 397B-Hauptmodell würde sonst jede Analyse
            # minutenlang rechnen, obwohl ein schnelles 4B konfiguriert ist.
            # Default bleibt das Side-Channel-VLM (Ollama), das weiterhin
            # exklusiv die Überwachungs-Pipeline (Watcher/Alerts) bedient.
            vlm_model = str(vlm_cfg.get("model", DEFAULT_MODEL))
            from ....lib.settings import load_settings as _global_settings
            _settings = _global_settings() or {}
            if _settings.get("backend_type") == "llamacpp":
                _saved = _settings.get("backend_models", {}).get("llamacpp", {})
                _vision_choice = str(_saved.get("vision_model") or "")
                if not _vision_choice or _vision_choice == str(_saved.get("aifred_model") or ""):
                    from ....lib.config import get_effective_model_from_settings
                    from ....lib.vision_utils import model_has_mmproj
                    main_model = get_effective_model_from_settings("aifred")
                    if main_model and model_has_mmproj(main_model):
                        vlm_model = main_model
            try:
                result = await analyze_sequence(
                    frames,
                    actual_prompt,
                    model=vlm_model,
                    num_ctx=int(vlm_cfg.get("num_ctx", DEFAULT_NUM_CTX)),
                    keep_alive=keep_alive,
                    host=vlm_cfg.get("host"),
                )
            except RuntimeError as e:
                return _err(f"VLM call failed: {e}")

            # Empty description even after analyze_sequence's internal retry
            # = the VLM produced no text (transient glitch under VRAM
            # contention, observed live with the 397B + TTS + VLM sharing
            # GPUs). Don't hand the main LLM a silent empty field — return an
            # honest error so it can retry the call or tell the user that
            # this camera couldn't be analysed.
            if not (result.text or "").strip():
                cam = resolve_source_label(source_id) if source_id else "image"
                return _err(
                    f"VLM returned no description for {cam} — likely a "
                    f"transient overload. Try vision_analyze again for this "
                    f"image; if it keeps failing, report it to the user."
                )

            stats = result.metadata.get("stats", {}) if result.metadata else {}
            payload: dict[str, Any] = {
                "source_id": source_id or "",
                "source_name": resolve_source_label(source_id) if source_id else "",
                "n_frames": result.n_frames,
                "model": result.model,
                "prompt": result.prompt,
                "duration_ms": round(result.duration_ms, 1),
                "description": result.text,
                # vlm_raw → llm_pipeline.py renders it as a <vlm_output>
                # collapsible; vlm_stats feeds the metrics footer.
                "vlm_raw": result.text,
                "vlm_stats": stats,
                # The analysed image IS the already-saved image we were given —
                # return the representative url so the pipeline pins exactly the
                # image the VLM saw. No new file is written here (true SSOT).
                "image_url": urls[-1],
            }
            # Persist event for query_events
            try:
                _store().add_event(
                    source_id=source_id or "",
                    event_type="vlm_analysis",
                    timestamp=datetime.now(),
                    classification={"description": result.text, "model": result.model},
                    metadata={"prompt": actual_prompt, "n_frames": result.n_frames},
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("vlm event log failed: %s", e)
            return _ok(**payload)

        return Tool(
            name="vision_analyze",
            description=load_tool_description(__file__, "vision_analyze"),
            parameters={
                "type": "object",
                "properties": {
                    "image_urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "image_url(s) EXACTLY as returned by "
                            "vision_snapshot or an upload — must start with "
                            "'/_upload/'. Multiple = temporal sequence. A "
                            "single string is also accepted."
                        ),
                    },
                    "prompt": {
                        "type": "string",
                        "description": (
                            "Question or instruction for the VLM. If omitted, "
                            "the configured default prompt is used."
                        ),
                    },
                    "source_id": {
                        "type": "string",
                        "description": (
                            "Source of the image (from the snapshot result) — "
                            "applies the per-camera briefing. Optional."
                        ),
                    },
                },
                "required": ["image_urls"],
            },
            executor=_exec,
            tier=TIER_READONLY,
        )

    # ── enroll_face ──────────────────────────────────────────────

    def _tool_enroll_face(self, ctx: PluginContext) -> Tool:
        async def _exec(name: str, source_id: str, notes: str = "") -> str:
            name = name.strip()
            if not name:
                return _err("name must not be empty")
            source = get_source(source_id)
            if source is None:
                return _err(f"unknown source: {source_id}")
            if not source.is_available():
                return _err(f"source not available: {source_id}")
            from ....lib.frame_hub import get_default_hub
            try:
                # FrameHub statt direktem source.snapshot() (Deadlock bei
                # laufendem Watcher, siehe vision_snapshot).
                frame = await get_default_hub().snapshot(source)
            except Exception as e:  # noqa: BLE001
                return _err(f"snapshot failed: {e}")
            import asyncio
            try:
                detector = get_default_detector()
                detections = await asyncio.to_thread(detector.detect, frame)
            except Exception as e:  # noqa: BLE001
                return _err(f"face_detect failed: {e}")
            if not detections:
                return _err("no face found in current frame")
            # Pick the highest-scoring detection
            best = max(detections, key=lambda d: d.detection_score)
            store = _store()
            existing = store.get_face_by_name(name)
            if existing is not None:
                face_id = int(existing["id"])
            else:
                face_id = store.add_face(
                    name, notes=notes, enrolled_by=ctx.session_id or "unknown"
                )
            emb_id = store.add_embedding(
                face_id,
                best.embedding,
                quality_score=float(best.detection_score),
            )
            # Invalidate any cached recognizer
            _recognizer(store).invalidate()
            return _ok(
                face_id=face_id,
                name=name,
                embedding_id=emb_id,
                detection_score=float(best.detection_score),
                bbox=list(best.bbox),
            )

        return Tool(
            name="vision_enroll_face",
            description=load_tool_description(__file__, "vision_enroll_face"),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "source_id": {"type": "string"},
                    "notes": {"type": "string", "default": ""},
                },
                "required": ["name", "source_id"],
            },
            executor=_exec,
            tier=TIER_WRITE_DATA,
        )

    # ── start_watch ──────────────────────────────────────────────

    def _tool_start_watch(self, ctx: PluginContext) -> Tool:
        async def _exec(
            source_id: str,
            fps: float | None = None,
            run_face_detect: bool | None = None,
        ) -> str:
            overrides: dict[str, Any] = {}
            if fps is not None:
                overrides["fps"] = float(fps)
            if run_face_detect is not None:
                overrides["run_face_detect_on_motion"] = bool(run_face_detect)
            cfg = _watch_config_from_settings(overrides)
            try:
                status = await _watcher().start(source_id, cfg)
            except (ValueError, RuntimeError) as e:
                return _err(str(e))
            return _ok(
                source_id=status.source_id,
                source_name=resolve_source_label(status.source_id),
                running=status.running,
                started_at=status.started_at.isoformat(timespec="seconds"),
                fps=status.fps,
                face_detect_active=cfg.run_face_detect_on_motion,
            )

        return Tool(
            name="vision_start_watch",
            description=load_tool_description(__file__, "vision_start_watch"),
            parameters={
                "type": "object",
                "properties": {
                    "source_id": {"type": "string"},
                    "fps": {
                        "type": "number",
                        "description": "Frames per second; default from settings.",
                    },
                    "run_face_detect": {
                        "type": "boolean",
                        "description": "Run face_detect+recognize on each motion event.",
                    },
                },
                "required": ["source_id"],
            },
            executor=_exec,
            tier=TIER_WRITE_DATA,
        )

    # ── stop_watch ───────────────────────────────────────────────

    def _tool_stop_watch(self, ctx: PluginContext) -> Tool:
        async def _exec(source_id: str) -> str:
            stopped = await _watcher().stop(source_id)
            return _ok(
                source_id=source_id,
                source_name=resolve_source_label(source_id),
                was_running=stopped,
            )

        return Tool(
            name="vision_stop_watch",
            description=load_tool_description(__file__, "vision_stop_watch"),
            parameters={
                "type": "object",
                "properties": {"source_id": {"type": "string"}},
                "required": ["source_id"],
            },
            executor=_exec,
            tier=TIER_WRITE_DATA,
        )

    # ── list_active_watches ──────────────────────────────────────

    def _tool_list_active_watches(self, ctx: PluginContext) -> Tool:
        async def _exec() -> str:
            statuses = _watcher().list_active()
            return _ok(
                count=len(statuses),
                watches=[
                    {
                        "source_id": s.source_id,
                        "source_name": resolve_source_label(s.source_id),
                        "running": s.running,
                        "started_at": s.started_at.isoformat(timespec="seconds"),
                        "fps": s.fps,
                        "frames_seen": s.frames_seen,
                        "motion_events": s.motion_events,
                        "face_events": s.face_events,
                        "last_event_at": (
                            s.last_event_at.isoformat(timespec="seconds")
                            if s.last_event_at else None
                        ),
                    }
                    for s in statuses
                ],
            )

        return Tool(
            name="vision_list_active_watches",
            description=load_tool_description(__file__, "vision_list_active_watches"),
            parameters={"type": "object", "properties": {}, "required": []},
            executor=_exec,
            tier=TIER_READONLY,
        )

    # ── query_events ─────────────────────────────────────────────

    def _tool_query_events(self, ctx: PluginContext) -> Tool:
        async def _exec(
            source_id: str | None = None,
            event_type: str | None = None,
            since_hours: float | None = None,
            limit: int = 50,
            describe: bool = False,
        ) -> str:
            cfg = _load_settings().get("events", {})
            actual_limit = max(1, min(int(limit), 500))
            since = None
            if since_hours is not None and since_hours > 0:
                since = datetime.now() - timedelta(hours=float(since_hours))
            # On-demand: describe the whole queried window first.
            # run_bulk_describe clusters near-identical frames (CPU pHash —
            # cheap) and runs the VLM once per happening on everything still
            # undescribed, fresh events included, so the chronicle is complete.
            # The side-channel VLM is cheap even for a lot of frames; the nightly
            # run is the safety net, and it is idempotent (described events are
            # skipped).
            # "presence"/"people" = "wer war da": Vereinigung aller Personen-/
            # Gesichts-Typen, Motion ausgeschlossen. Wird ohne Store-Typ-Filter
            # geholt und nach dem Dedup auf diese Typen reduziert — so kann das
            # Personen-Event den Cluster repräsentieren, nicht ein Motion-Frame.
            _PRESENCE = {"face_unknown", "face_unsure", "face_known", "person"}
            # Wichtigkeit für die Repräsentanten-Wahl pro Cluster: ein Cluster,
            # der eine Person/ein Gesicht enthielt, MUSS als solches auftauchen —
            # nicht als Motion (Motion feuert am häufigsten und würde sonst als
            # "neuestes" Event gewinnen und die Person verstecken). Reihenfolge:
            # Fremder > Unsicher > Bekannt > Person (Körper) > VLM-Notiz > Motion.
            _SIG = {
                "face_unknown": 5, "face_unsure": 4, "face_known": 3,
                "person": 2, "vlm_analysis": 1, "motion": 0,
            }
            presence = (event_type or "").strip().lower() in ("presence", "people")
            store_event_type = None if presence else event_type

            if describe:
                try:
                    from ....lib.vision_bulk import run_bulk_describe
                    describe_types = (
                        sorted(_PRESENCE) if presence
                        else ([store_event_type] if store_event_type else None)
                    )
                    await run_bulk_describe(
                        source_id=source_id,
                        event_types=describe_types,
                        since=since,
                        check_vram=False,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("on-demand describe failed: %s", e)

            # Scan the WHOLE window (no cap) so dedup can collapse clusters that
            # span it without any happening slipping through an artificial
            # limit, then return only `limit` distinct happenings.
            store = _store()
            try:
                raw = store.query_events(
                    source_id=source_id,
                    event_type=store_event_type,
                    since=since,
                    limit=None,
                )
            except Exception as e:  # noqa: BLE001
                return _err(f"query failed: {e}")

            # Collapse cluster members into ONE representative per happening.
            # `raw` is newest-first. Pick the MOST SIGNIFICANT event of a cluster
            # as its representative (via _SIG), NOT merely the newest — otherwise
            # the ever-present motion frames mask a person/face the same cluster
            # detected. Ties keep the newer event (first seen). Events without a
            # cluster_id (unclustered) are kept individually.
            def _sig(ev: dict[str, Any]) -> int:
                return _SIG.get(str(ev.get("event_type") or ""), 0)

            reps: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
            solo: list[dict[str, Any]] = []
            member_count: dict[str, int] = {}
            for ev in raw:
                cid = str(ev.get("cluster_id") or "")
                if cid:
                    member_count[cid] = member_count.get(cid, 0) + 1
                    cur = reps.get(cid)
                    if cur is None or _sig(ev) > _sig(cur):
                        reps[cid] = ev
                else:
                    solo.append(ev)
            merged = list(reps.values()) + solo
            if presence:
                merged = [
                    ev for ev in merged
                    if str(ev.get("event_type") or "") in _PRESENCE
                ]
            happenings = sorted(
                merged, key=lambda ev: ev["timestamp"], reverse=True,
            )[:actual_limit]

            # Kamera-Anzeigenamen (SSoT) für die Ausgabe — eine Query für alle
            # Quellen, dann Dict-Lookup. So nennt der Assistent dem Nutzer "Büro"
            # statt der technischen source_id.
            labels = store.source_labels()

            def _out(ev: dict[str, Any]) -> dict[str, Any]:
                cid = str(ev.get("cluster_id") or "")
                fp = str(ev.get("frame_path") or "")
                sid = str(ev["source_id"])
                return {
                    "id": ev["id"],
                    "source_id": sid,
                    "source_name": labels.get(sid, sid),
                    "timestamp": ev["timestamp"],
                    "event_type": ev["event_type"],
                    "confidence": ev["confidence"],
                    "face_id": ev["face_id"],
                    "classification": ev["classification"],
                    # Browser-servable URL so the assistant can embed the frame
                    # as ![…](image_url) in its reply (and re-analyze it via
                    # vision_analyze).
                    "image_url": get_image_url(Path(fp)) if fp else "",
                    # How many frames this one happening spans.
                    "frames_in_cluster": member_count.get(cid, 1) if cid else 1,
                }

            return _ok(
                count=len(happenings),
                events=[_out(ev) for ev in happenings],
                config_defaults={"limit": cfg.get("default_query_limit", 50)},
            )

        return Tool(
            name="vision_query_events",
            description=load_tool_description(__file__, "vision_query_events"),
            parameters={
                "type": "object",
                "properties": {
                    "source_id": {
                        "type": "string",
                        "description": (
                            "OPTIONAL camera filter. Omit to search ALL "
                            "cameras (the usual case). If you set it, use an "
                            "EXACT id from vision_list_sources or a prior tool "
                            "result — never a guessed/remembered id, or you'll "
                            "silently get zero results."
                        ),
                    },
                    "event_type": {
                        "type": "string",
                        "description": (
                            "Filter. Use 'presence' for 'was anyone there?' "
                            "(union of face_known/face_unknown/face_unsure/"
                            "person, motion excluded). Other values: motion | "
                            "face_known | face_unknown | face_unsure | person | "
                            "vlm_analysis. Omit for everything."
                        ),
                    },
                    "since_hours": {
                        "type": "number",
                        "description": "Only events newer than N hours.",
                    },
                    "limit": {"type": "integer", "default": 50},
                    "describe": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Generate missing VLM scene descriptions for the "
                            "queried window before returning. Use only when the "
                            "user wants the actual content of what happened and "
                            "the events still lack a description."
                        ),
                    },
                },
                "required": [],
            },
            executor=_exec,
            tier=TIER_READONLY,
        )


# Plugin instance — auto-discovered by aifred.lib.plugin_registry
plugin = VisionPlugin()
