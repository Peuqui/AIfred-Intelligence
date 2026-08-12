"""Audio Source Resolver — turns user-facing labels into mpv URIs.

Architecture:
- **Local folder sources** are auto-discovered as direct children of
  ``data/media/audio/`` — each subfolder (real or symlink) becomes a
  source whose label is the folder/symlink name. Symlinks make NAS
  mounts transparent (e.g. ``data/media/audio/nas_music`` →
  ``/mnt/auto/vuplus/MediaServ/Musik``).
- **HTTP-stream sources** (Internet radio) are read from the audio_player
  plugin's ``settings.json`` ``sources`` block, only entries with
  ``type: http_stream``.

The LLM never sees raw paths or URLs — only labels + relative items.
Path-traversal protection: an item must stay within its source root
(symlink target counts as the root for content listing, but the user-
facing path always starts with the source label).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# Audio extensions mpv reliably plays
ALLOWED_EXTENSIONS = {
    ".mp3", ".wav", ".ogg", ".flac", ".m4a", ".opus", ".aac", ".mp4", ".webm",
}

# ── audio_type Hierarchie ───────────────────────────────────────────
#
# Reihenfolge (User-Wunsch): höchste Priorität zuerst
#   1. Genre-Tag aus dem Audio-File (mutagen via audio_index)
#   2. Filename (Wörter wie "audiobook"/"hörbuch"/"alarm")
#   3. Source-Label (Folder-Name → Heuristik)
#   4. Default: "music"
#
# settings.json kann pro Source ein explizites ``audio_type``-Feld
# setzen — das überschreibt die ganze Hierarchie und wird in
# build_source_map einfach mitgenommen.

# Genre-Tags die auf Sprache hinweisen (case-insensitive substring match)
_SPEECH_GENRE_HINTS = (
    "audiobook", "audio book", "hörbuch", "hoerbuch",
    "spoken", "speech", "podcast", "talk", "interview",
    "lesung", "vortrag",
)
# Genre-Tags die auf Alarm/SFX hinweisen
_ALARM_GENRE_HINTS = ("alarm", "wecker", "sfx", "sound effect", "notification")

# Filename-Hints (substring, case-insensitive)
_SPEECH_FILE_HINTS = ("audiobook", "hörbuch", "hoerbuch", "podcast", "lesung")
_ALARM_FILE_HINTS = ("alarm", "wecker", "alert", "ding")

# Folder-Label-Heuristik (exact match auf .lower()).
# Achtung: ``label.lower()`` lowercased Umlaute (Ö→ö), aber konvertiert sie
# NICHT zu ASCII (ö→oe). Daher hier beide Schreibweisen aufnehmen.
_FOLDER_AUDIO_TYPE: dict[str, str] = {
    "alarms":      "alarm",
    "wecker":      "alarm",
    "hörbücher":   "speech",
    "hörbuch":     "speech",
    "hoerbuecher": "speech",
    "hoerbuch":    "speech",
    "audiobooks":  "speech",
    "audiobook":   "speech",
    "podcasts":    "speech",
    "podcast":     "speech",
    "speech":      "speech",
    "sprache":     "speech",
    "lesungen":    "speech",
    "lesung":      "speech",
}


def _audio_type_from_genre(genre: Optional[str]) -> Optional[str]:
    if not genre:
        return None
    g = genre.lower()
    for hint in _SPEECH_GENRE_HINTS:
        if hint in g:
            return "speech"
    for hint in _ALARM_GENRE_HINTS:
        if hint in g:
            return "alarm"
    return None  # bekannter Tag aber keine Audio-Type-Mapping → music via Default


def _audio_type_from_filename(filename: str) -> Optional[str]:
    if not filename:
        return None
    f = filename.lower()
    for hint in _SPEECH_FILE_HINTS:
        if hint in f:
            return "speech"
    for hint in _ALARM_FILE_HINTS:
        if hint in f:
            return "alarm"
    return None


def _guess_audio_type(label: str) -> str:
    """Vermute den audio_type aus dem Source-Label. Default: music."""
    return _FOLDER_AUDIO_TYPE.get(label.lower(), "music")


def resolve_audio_type(
    label: str,
    sub_path: str,
    *,
    source_default: Optional[str] = None,
) -> str:
    """Bestimme audio_type via Hierarchie (Tag > Filename > Source > Default).

    ``source_default`` ist der bereits per Heuristik oder settings.json
    bestimmte Source-Wert. Wenn der explizit (nicht "music") gesetzt ist,
    schlägt er die Filename-Heuristik nicht — er ist Tier 3 und greift
    nur wenn Tag und Filename nichts ergeben.

    Tag-Lookup geht über das audio_index (lazy-import um Module-Cycle zu
    vermeiden). Wenn das Item nicht indiziert ist, wird Tier 1
    übersprungen — kein Fehler.
    """
    # Tier 1: Genre-Tag aus audio_index
    if sub_path:
        try:
            from .audio_index import audio_index
            genre = audio_index.get_genre(label, sub_path)
        except Exception:  # noqa: BLE001
            genre = None
        tag_type = _audio_type_from_genre(genre)
        if tag_type:
            return tag_type

    # Tier 2: Filename- und Sub-Folder-Heuristik
    if sub_path:
        for part in Path(sub_path).parts:
            file_type = _audio_type_from_filename(part)
            if file_type:
                return file_type

    # Tier 3: Source-Default
    if source_default:
        return source_default

    # Tier 4: hartes Default
    return "music"


def safe_subpath(root: Path, rel: str) -> "Path | None":
    """Resolve ``rel`` unter ``root`` mit Traversal-Schutz (SSOT).

    Gleiches Muster wie in ``SourceResolver.resolve``: ``..``-Teile,
    absolute Pfade und Symlink-Ausbrüche werden abgelehnt. ``None`` wenn
    der Pfad die Wurzel verlässt — der Aufrufer formuliert die Meldung.
    Leeres ``rel`` liefert die (resolved) Wurzel selbst.
    """
    root = root.resolve()
    if not rel:
        return root
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts:
        return None
    candidate = (root / p).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


@dataclass
class ResolvedSource:
    uri: str            # Path or URL for mpv loadfile
    state_key: str      # Stable key for audio_state (label/relpath, or label)
    is_stream: bool     # True for HTTP streams (no resume sense)
    label: str          # Source label
    item: str           # Item path within source ("" for streams)
    # Sink-Hint für Output-Channels: "music" (default), "speech" (Hörbücher,
    # Podcasts, TTS), oder "alarm" (Wecker). Lebt in der Source-Config
    # (`audio_type`-Feld in settings.json) und wird vom FreeEcho2Channel via
    # send_audio_start an den FreeEcho.2 übermittelt — der nutzt das für
    # VU-Pattern (Stereo-VU bei music, Voice-VU bei speech).
    audio_type: str = "music"


def _discover_local_sources(audio_root: Path) -> dict[str, dict[str, Any]]:
    """Scan audio_root for direct children (folders + symlinks).

    Each becomes a local_folder source. Broken symlinks are skipped with
    a warning. Hidden entries (leading dot) are excluded.
    """
    from .logging_utils import log_message

    sources: dict[str, dict[str, Any]] = {}
    if not audio_root.is_dir():
        return sources

    for child in audio_root.iterdir():
        if child.name.startswith("."):
            continue
        # Resolve symlinks; broken symlinks raise or return non-dir.
        try:
            real = child.resolve()
        except (OSError, RuntimeError) as exc:
            log_message(f"Audio source '{child.name}': resolve failed ({exc})", "warning")
            continue
        if not real.is_dir():
            # Broken symlink or not a folder — skip but inform.
            if child.is_symlink():
                log_message(
                    f"Audio source '{child.name}' → '{real}' is a broken symlink",
                    "warning",
                )
            continue
        sources[child.name] = {
            "type": "local_folder",
            "path": str(real),
            "is_symlink": child.is_symlink(),
            "audio_type": _guess_audio_type(child.name),
        }
    return sources


def build_source_map(
    audio_root: Path,
    settings_streams: dict[str, dict[str, str]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Combine filesystem-discovered local folders with HTTP-stream entries
    from plugin settings.json into a single source map."""
    sources = _discover_local_sources(audio_root)
    if settings_streams:
        for label, cfg in settings_streams.items():
            if cfg.get("type") == "http_stream":
                sources[label] = dict(cfg)
    return sources


class SourceResolver:
    """Resolves source labels + items to concrete URIs."""

    def __init__(self, sources_config: dict[str, dict[str, Any]]) -> None:
        self._sources = sources_config

    def reload(self, sources_config: dict[str, dict[str, Any]]) -> None:
        self._sources = sources_config

    # ── Listings ──────────────────────────────────────────

    def list_sources(self) -> list[dict[str, Any]]:
        """Return [{label, type, target, is_symlink}] for UI/LLM."""
        return [
            {
                "label": label,
                "type": cfg.get("type", "?"),
                "target": cfg.get("path") or cfg.get("url", ""),
                "is_symlink": cfg.get("is_symlink", False),
            }
            for label, cfg in self._sources.items()
        ]

    # ── Resolution ────────────────────────────────────────

    def resolve(self, item: str) -> ResolvedSource:
        """Resolve an item identifier to a ResolvedSource.

        Item formats:
          "swr3"                       → http_stream by label
          "nas_music/Klassik/foo.mp3"  → local_folder/file (symlink ok)
        """
        if "/" not in item:
            label, sub = item, ""
        else:
            label, sub = item.split("/", 1)

        cfg = self._sources.get(label)
        if cfg is None:
            available = list(self._sources.keys())
            raise ValueError(
                f"Unknown source label: '{label}'. Available: {available}"
            )

        stype = cfg.get("type")

        if stype == "http_stream":
            url = cfg.get("url", "")
            if not url:
                raise ValueError(f"Source '{label}' has no URL configured")
            # HTTP-Streams haben keine Tags / kein File-Path → Source-Wert.
            return ResolvedSource(
                uri=url,
                state_key=label,
                is_stream=True,
                label=label,
                item="",
                audio_type=str(cfg.get("audio_type", "music")),
            )

        if stype == "local_folder":
            if not sub:
                raise ValueError(
                    f"Source '{label}' is a folder. Use audio_list(source='{label}') "
                    f"to see available items, then call with 'label/filename'."
                )
            root_path = cfg.get("path", "")
            if not root_path:
                raise ValueError(f"Source '{label}' has no path configured")
            root = Path(root_path).expanduser().resolve()
            if not root.is_dir():
                raise ValueError(f"Source '{label}' path does not exist: {root}")
            # Path-traversal guard
            if ".." in Path(sub).parts:
                raise ValueError(f"Path traversal denied: {sub!r}")
            target = (root / sub).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"Path '{sub}' escapes source folder") from exc
            if not target.is_file():
                raise ValueError(f"File not found in '{label}': {sub}")
            if target.suffix.lower() not in ALLOWED_EXTENSIONS:
                raise ValueError(f"Unsupported audio extension: {target.suffix}")
            # audio_type per Hierarchie: Tag > Filename > Source-Default
            source_default = str(cfg.get("audio_type", _guess_audio_type(label)))
            audio_type = resolve_audio_type(
                label, sub, source_default=source_default,
            )
            return ResolvedSource(
                uri=str(target),
                state_key=f"{label}/{sub}",
                is_stream=False,
                label=label,
                item=sub,
                audio_type=audio_type,
            )

        raise ValueError(f"Unknown source type: {stype!r} for label '{label}'")
