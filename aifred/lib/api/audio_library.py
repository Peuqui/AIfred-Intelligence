"""The audio player's sources and settings (``lib/audio_library.py``): every source with its index count, a folder of
the sandbox added, a source removed (only a label the source map knows), its index read anew (a run of the server,
one at a time) or emptied; the sandbox and the list threshold of the spoken reply."""

import asyncio

from pydantic import BaseModel, ConfigDict, Field

from ..logging_utils import log_message
from .app import api_app


class AudioSourceRow(BaseModel):
    label: str
    type: str = Field(description="local_folder or http_stream")
    target: str = Field(description="The folder it shows, or the stream's URL")
    is_symlink: bool
    indexed: int


class AudioLibrary(BaseModel):
    sources: list[AudioSourceRow]
    picker_root: str = Field(description="The sandbox new sources come from")
    tts_list_max: int = Field(description="From how many entries a list in the spoken reply is cut")
    indexing: str = Field(description="The source being read now ('' = none)")
    message: str = Field(description="The reading's state or its last outcome in words")


class AudioSourceAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="The folder, relative to the sandbox")


class AudioIndexStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    force: bool = Field(False, description="Every file anew, not only the changed ones")


class AudioSettingsChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    picker_root: str | None = None
    tts_list_max: int | None = None


class AudioDone(BaseModel):
    message: str


def _lang() -> str:
    from ..prompt_loader import get_language

    return get_language()


def _library() -> AudioLibrary:
    from dataclasses import asdict

    from ..audio_library import indexing, picker_root, sources, tts_list_max

    state = indexing()
    return AudioLibrary(sources=[AudioSourceRow(**asdict(source)) for source in sources()], picker_root=picker_root(),
                        tts_list_max=tts_list_max(), indexing=state.running, message=state.message(_lang()))


@api_app.get("/audio/sources", response_model=AudioLibrary, tags=["Audio"])
async def audio_sources() -> AudioLibrary:
    return await asyncio.to_thread(_library)


@api_app.post("/audio/sources", response_model=AudioDone, status_code=201, tags=["Audio"])
async def audio_source_add(adding: AudioSourceAdd) -> AudioDone:
    """A folder of the sandbox as a new source (a symlink under data/media/audio/); 422 for no folder or one that
    leads outside the sandbox."""
    from ..audio_library import add_source
    from ..i18n import t

    label = await asyncio.to_thread(add_source, adding.path)
    log_message(f"🔊 Audio source {label!r} → {adding.path!r}")
    return AudioDone(message=t("audio_source_added", lang=_lang(), source=label))


@api_app.delete("/audio/sources/{label}", response_model=AudioDone, tags=["Audio"])
async def audio_source_remove(label: str) -> AudioDone:
    """Take a source out with its index (the folder a symlink points to stays); 404 for a label the source map does
    not know."""
    from ..audio_library import remove_source
    from ..i18n import t

    removed = await asyncio.to_thread(remove_source, label)
    log_message(f"🔊 Audio source {label!r} removed ({removed} index entries)")
    return AudioDone(message=t("audio_source_removed", lang=_lang(), source=label, count=removed))


@api_app.delete("/audio/sources/{label}/index", response_model=AudioDone, tags=["Audio"])
async def audio_source_clear(label: str) -> AudioDone:
    from ..audio_library import clear_index
    from ..i18n import t

    removed = await asyncio.to_thread(clear_index, label)
    return AudioDone(message=t("audio_source_cleared", lang=_lang(), source=label, count=removed))


@api_app.post("/audio/sources/{label}/index", response_model=AudioLibrary, status_code=202, tags=["Audio"])
async def audio_source_index(label: str, start: AudioIndexStart) -> AudioLibrary:
    """Read a source's files anew into the index; answers at once, ``GET /audio/sources`` follows it. 409 while one
    runs, 404 for an unknown label."""
    from ..audio_library import start_indexing

    start_indexing(label, start.force)
    return await asyncio.to_thread(_library)


@api_app.put("/audio/settings", response_model=AudioLibrary, tags=["Audio"])
async def audio_settings_change(change: AudioSettingsChange) -> AudioLibrary:
    """The sandbox (an absolute folder) and the list threshold (at least 1); 422 otherwise, nothing is written."""
    from ..audio_library import change_settings

    await asyncio.to_thread(lambda: change_settings(root=change.picker_root, list_max=change.tts_list_max))
    return await asyncio.to_thread(_library)
