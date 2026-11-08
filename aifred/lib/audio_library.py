"""The audio player's sources for every interface (the audio page of Vue, the Reflex copy until it goes): every source
(a folder under data/media/audio/, mostly a symlink, or a stream of the plugin's settings) with how many files its index
holds; a folder of the picker's sandbox added as a source, a source taken out, its index read anew (a run of the
server, one at a time) or emptied; the plugin's settings the page changes: the sandbox (picker.root) and from how many
entries the list in a spoken reply is cut (tts_list.full_max_items).

Only a label the source map knows is taken out or indexed (a page can name anything); a new source must resolve
inside the sandbox, so no symlink chain leads /api/audio/file outside it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .user_error import Conflict, Invalid, Missing

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Source:
    label: str
    type: str            # local_folder or http_stream
    target: str          # the folder it shows, or the stream's URL
    is_symlink: bool
    indexed: int         # files in the index


def sources() -> list[Source]:
    from .audio_index import audio_index
    from .audio_player_settings import build_configured_source_map

    per_source = audio_index.stats()["per_source"]
    return sorted((Source(label, str(cfg.get("type", "")), str(cfg.get("path") or cfg.get("url") or ""),
                          bool(cfg.get("is_symlink", False)), int(per_source.get(label, 0)))
                   for label, cfg in build_configured_source_map().items()), key=lambda source: source.label.lower())


def _local_folder(label: str) -> dict[str, Any]:
    """The source map's entry of a local folder; KeyError for a label it does not know as one."""
    from .audio_player_settings import build_configured_source_map

    cfg = build_configured_source_map().get(label)
    if cfg is None or cfg.get("type") != "local_folder":
        raise Missing("no_audio_source", source=label)
    return dict(cfg)


def picker_root() -> str:
    from .audio_player_settings import load_audio_player_settings
    from .config import AUDIO_PICKER_ROOT_DEFAULT

    return str((load_audio_player_settings().get("picker") or {}).get("root") or AUDIO_PICKER_ROOT_DEFAULT).strip()


def tts_list_max() -> int:
    """From how many entries the list in a spoken reply is cut."""
    from .audio_player_settings import load_audio_player_settings
    from .config import AUDIO_TTS_LIST_MAX_DEFAULT

    return int((load_audio_player_settings().get("tts_list") or {}).get("full_max_items", AUDIO_TTS_LIST_MAX_DEFAULT))


def change_settings(*, root: str | None = None, list_max: int | None = None) -> None:
    """ValueError for a sandbox that is no absolute folder or a threshold below 1 (nothing is written then)."""
    from .audio_player_settings import (
        load_audio_player_settings,
        save_audio_player_settings,
    )

    settings = load_audio_player_settings()
    if root is not None:
        folder = Path(root.strip()).expanduser()
        if not folder.is_absolute() or not folder.is_dir():
            raise Invalid("sandbox_not_folder", path=root)
        settings.setdefault("picker", {})["root"] = str(folder)
    if list_max is not None:
        if list_max < 1:
            raise Invalid("number_too_small", value=list_max, low=1)
        settings.setdefault("tts_list", {})["full_max_items"] = list_max
    save_audio_player_settings(settings)


def add_source(rel_path: str) -> str:
    """A folder of the sandbox as a new source: a symlink under data/media/audio/ named after it (a number added
    while the name is taken). Returns the label. ValueError for no folder or one that resolves outside the sandbox."""
    from .config import MEDIA_AUDIO_DIR
    from .file_browser import resolve

    root = picker_root()
    folder = resolve(root, rel_path)
    if not folder.is_dir():
        raise Invalid("not_a_folder", path=rel_path)
    # A symlink chain inside the sandbox may still point out of it: the resolved folder decides.
    try:
        folder.resolve().relative_to(Path(root).expanduser().resolve())
    except ValueError as error:
        raise Invalid("leads_outside_sandbox", path=rel_path) from error
    base = folder.name or "source"
    label, number = base, 1
    while (MEDIA_AUDIO_DIR / label).exists() or (MEDIA_AUDIO_DIR / label).is_symlink():
        number += 1
        label = f"{base}_{number}"
    (MEDIA_AUDIO_DIR / label).symlink_to(folder)
    return label


def remove_source(label: str) -> int:
    """Take a known source out (its symlink or folder under data/media/audio/, never what a symlink points to) with
    its index; returns how many index entries went. KeyError for a label the source map does not know."""
    from .audio_index import audio_index
    from .audio_sources import remove_local_source
    from .config import MEDIA_AUDIO_DIR

    _local_folder(label)
    remove_local_source(MEDIA_AUDIO_DIR, label)
    return audio_index.remove_source(label)


def clear_index(label: str) -> int:
    """Empty a known source's index; returns how many entries went. KeyError for an unknown label."""
    from .audio_index import audio_index

    _local_folder(label)
    return audio_index.remove_source(label)


# ── Reading a source's files anew into the index (a run of the server, one at a time) ───────────────────────────


@dataclass(frozen=True)
class Indexing:
    running: str = ""         # the source being read ("" = none)
    last: str = ""            # the source read last
    scanned: int = 0
    inserted: int = 0
    updated: int = 0
    deleted: int = 0
    seconds: float = 0.0
    error: str = ""

    def message(self, lang: str) -> str:
        from .formatting import format_number
        from .i18n import t

        if self.running:
            return t("audio_index_running", lang=lang, source=self.running)
        if self.error:
            return f"⚠️ {self.error}"
        if not self.last:
            return ""
        return t("audio_index_done", lang=lang, source=self.last, scanned=format_number(self.scanned, 0),
                 inserted=format_number(self.inserted, 0), updated=format_number(self.updated, 0),
                 deleted=format_number(self.deleted, 0), seconds=format_number(self.seconds, 1))


class IndexingRunning(Conflict):
    """A source is being read already."""


_indexing = Indexing()
_runs: set[asyncio.Task[None]] = set()


def indexing() -> Indexing:
    return _indexing


def start_indexing(label: str, force: bool = False) -> asyncio.Task[None]:
    """Read a known source's files anew (``force``: every file, not only the changed ones). :class:`IndexingRunning`
    while one runs, KeyError for an unknown label."""
    global _indexing
    if _indexing.running:
        raise IndexingRunning("indexing_running", source=_indexing.running)
    folder = _local_folder(label)["path"]
    _indexing = Indexing(running=label)
    run = asyncio.create_task(_read(label, str(folder), force))
    _runs.add(run)
    run.add_done_callback(_runs.discard)
    return run


async def _read(label: str, folder: str, force: bool) -> None:
    global _indexing
    from .audio_index import audio_index

    try:
        result = await asyncio.to_thread(audio_index.scan_source, label, folder, force=force)
    except Exception as failure:  # the page shows why (files, tags, the index database)
        logger.warning("Audio index of %s failed: %s", label, failure, exc_info=True)
        _indexing = Indexing(last=label, error=str(failure) or type(failure).__name__)
        return
    _indexing = replace(Indexing(), last=label, scanned=result.scanned, inserted=result.inserted, updated=result.updated,
                        deleted=result.deleted, seconds=result.elapsed_sec)
