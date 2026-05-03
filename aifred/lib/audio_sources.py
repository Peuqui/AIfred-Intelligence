"""Audio Source Resolver — turns user-facing labels into mpv URIs.

The LLM never sees raw paths or URLs. It only sees source labels
(defined in the plugin's settings.json) plus item names within those
sources. This module resolves those references to concrete file paths
or stream URLs, with path-traversal protection on local sources.

Source-config schema (from settings.json):
    {
      "sources": {
        "hoerbuecher": {"type": "local_folder", "path": "/mnt/nas/Hoerbuecher"},
        "swr3":        {"type": "http_stream", "url": "https://liveradio.swr.de/..."}
      }
    }
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Audio extensions mpv reliably plays
ALLOWED_EXTENSIONS = {
    ".mp3", ".wav", ".ogg", ".flac", ".m4a", ".opus", ".aac", ".mp4", ".webm",
}


@dataclass
class ResolvedSource:
    uri: str            # Path or URL for mpv loadfile
    state_key: str      # Stable key for audio_state (label/relpath, or label)
    is_stream: bool     # True for HTTP streams (no resume sense)
    label: str          # Source label
    item: str           # Item path within source ("" for streams)


class SourceResolver:
    """Resolves source labels + items to concrete URIs."""

    def __init__(self, sources_config: dict[str, dict[str, str]]) -> None:
        self._sources = sources_config

    def reload(self, sources_config: dict[str, dict[str, str]]) -> None:
        self._sources = sources_config

    # ── Listings ──────────────────────────────────────────

    def list_sources(self) -> list[dict[str, str]]:
        """Return [{label, type, target}] for UI/LLM."""
        return [
            {
                "label": label,
                "type": cfg.get("type", "?"),
                "target": cfg.get("path") or cfg.get("url", ""),
            }
            for label, cfg in self._sources.items()
        ]

    def list_items(self, label: str) -> list[str]:
        """Recursively list audio files in a local_folder source."""
        cfg = self._sources.get(label)
        if not cfg or cfg.get("type") != "local_folder":
            return []
        path = cfg.get("path", "")
        if not path:
            return []
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            return []
        items: list[str] = []
        for child in root.rglob("*"):
            if not child.is_file():
                continue
            if child.suffix.lower() not in ALLOWED_EXTENSIONS:
                continue
            try:
                rel = child.relative_to(root)
            except ValueError:
                continue
            items.append(str(rel))
        items.sort()
        return items

    # ── Resolution ────────────────────────────────────────

    def resolve(self, item: str) -> ResolvedSource:
        """Resolve an item identifier to a ResolvedSource.

        Item formats:
          "swr3"                       → http_stream by label
          "hoerbuecher/foo.mp3"        → local_folder/file
          "hoerbuecher/sub/foo.mp3"    → nested file
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
            return ResolvedSource(
                uri=url,
                state_key=label,
                is_stream=True,
                label=label,
                item="",
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
            return ResolvedSource(
                uri=str(target),
                state_key=f"{label}/{sub}",
                is_stream=False,
                label=label,
                item=sub,
            )

        raise ValueError(f"Unknown source type: {stype!r} for label '{label}'")
