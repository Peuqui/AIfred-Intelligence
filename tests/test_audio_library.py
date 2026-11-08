"""The audio player's sources and settings (lib/audio_library.py, /audio/sources, /audio/settings): the sources with
their index counts, a folder of the sandbox added (never one a symlink leads outside of, never by ``..``), only a known
source removed or indexed (the folder a symlink points to stays), the settings checked before they are written, and
reading a source anew as a run of the server with its outcome in words. Index, settings file and media folder are the
test's own."""

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from aifred.lib import audio_index as audio_index_module
from aifred.lib import audio_library, audio_player_settings, config


class _Index:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {"Musik": 12}
        self.scanned: list[tuple[str, str, bool]] = []

    def stats(self) -> dict:
        return {"total": sum(self.counts.values()), "per_source": dict(self.counts)}

    def remove_source(self, source: str) -> int:
        return self.counts.pop(source, 0)

    def scan_source(self, source: str, root_path: str, on_progress: object = None, force: bool = False) -> SimpleNamespace:
        self.scanned.append((source, root_path, force))
        return SimpleNamespace(scanned=40, inserted=3, updated=1, deleted=2, elapsed_sec=1.25)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """A sandbox with two folders and a symlink leading out of it; data/media/audio/ with the source "Musik" (a
    symlink into the sandbox); the settings file naming the sandbox."""
    sandbox, media, outside = tmp_path / "mnt", tmp_path / "media", tmp_path / "outside"
    for folder in (sandbox / "Hörbücher", sandbox / "Musik" / "Jazz", media, outside):
        folder.mkdir(parents=True)
    (sandbox / "escape").symlink_to(outside)
    (media / "Musik").symlink_to(sandbox / "Musik")
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"picker": {"root": str(sandbox)}, "sources": {"Radio": {"type": "http_stream", "url": "http://radio/x"}}}))
    index = _Index()
    monkeypatch.setattr(config, "MEDIA_AUDIO_DIR", media)
    monkeypatch.setattr(audio_player_settings, "AUDIO_PLAYER_SETTINGS_PATH", settings)
    monkeypatch.setattr(audio_index_module, "audio_index", index)
    monkeypatch.setattr(audio_library, "_indexing", audio_library.Indexing())
    return {"sandbox": sandbox, "media": media, "settings": settings, "index": index}


def test_the_sources_with_their_index_counts(world: dict) -> None:
    listed = {source.label: source for source in audio_library.sources()}
    assert (listed["Musik"].type, listed["Musik"].is_symlink, listed["Musik"].indexed) == ("local_folder", True, 12)
    assert (listed["Radio"].type, listed["Radio"].target) == ("http_stream", "http://radio/x")


def test_a_folder_of_the_sandbox_becomes_a_source_and_nothing_leads_outside(world: dict) -> None:
    assert audio_library.add_source("Hörbücher") == "Hörbücher"
    assert (world["media"] / "Hörbücher").resolve() == (world["sandbox"] / "Hörbücher").resolve()
    assert audio_library.add_source("Musik/Jazz") == "Jazz"
    assert audio_library.add_source("Hörbücher") == "Hörbücher_2"          # the name was taken
    for bad in ("escape", "../outside", "nothing"):
        with pytest.raises(ValueError):
            audio_library.add_source(bad)
    assert not (world["media"] / "escape").exists()


def test_only_a_known_source_is_removed_and_its_target_stays(world: dict) -> None:
    for unknown in ("Radio", "../mnt", "nothing"):
        with pytest.raises(KeyError):
            audio_library.remove_source(unknown)
    assert audio_library.remove_source("Musik") == 12
    assert not (world["media"] / "Musik").exists() and (world["sandbox"] / "Musik" / "Jazz").is_dir()


def test_the_settings_are_checked_before_they_are_written(world: dict, tmp_path: Path) -> None:
    before = world["settings"].read_text()
    for bad in ({"root": "relative/path"}, {"root": str(tmp_path / "nothing")}, {"list_max": 0}):
        with pytest.raises(ValueError):
            audio_library.change_settings(**bad)
    assert world["settings"].read_text() == before
    audio_library.change_settings(root=str(tmp_path / "outside"), list_max=8)
    assert (audio_library.picker_root(), audio_library.tts_list_max()) == (str(tmp_path / "outside"), 8)
    assert json.loads(world["settings"].read_text())["sources"]["Radio"]["type"] == "http_stream"   # the rest stays


def test_reading_a_source_is_one_run_at_a_time_and_tells_its_outcome(world: dict) -> None:
    async def scenario() -> str:
        run = audio_library.start_indexing("Musik", force=True)
        running = audio_library.indexing().message("de")
        with pytest.raises(audio_library.IndexingRunning):
            audio_library.start_indexing("Musik")
        await run
        return running

    assert asyncio.run(scenario()) == "⏳ Lese Musik ein …"
    assert world["index"].scanned == [("Musik", str(world["sandbox"] / "Musik"), True)]
    assert audio_library.indexing().message("de") == "✅ Musik: 40 Dateien gesehen, 3 neu, 1 geändert, 2 entfernt (1,2 s)"
    with pytest.raises(KeyError):
        audio_library.start_indexing("Radio")


@pytest.fixture
def client(world: dict) -> Iterator[TestClient]:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import sign_username
    from aifred.lib.browser_storage import USERNAME_COOKIE_NAME

    with TestClient(api_app) as test_client:
        test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        yield test_client


def test_the_sources_over_the_api(client: TestClient, world: dict) -> None:
    library = client.get("/audio/sources").json()
    assert [source["label"] for source in library["sources"]] == ["Musik", "Radio"] and library["indexing"] == ""
    folders = client.get("/audio/sources/folders", params={"path": "Musik"}).json()
    assert folders == {"path": "Musik", "folders": [{"name": "Jazz", "path": "Musik/Jazz", "is_symlink": False}]}
    assert client.get("/audio/sources/folders", params={"path": "../outside"}).status_code == 422
    added = client.post("/audio/sources", json={"path": "Hörbücher"})
    assert added.status_code == 201 and "Hörbücher" in added.json()["message"]
    assert client.post("/audio/sources", json={"path": "escape"}).status_code == 422
    assert client.delete("/audio/sources/Radio").status_code == 404
    assert client.delete("/audio/sources/Musik/index").json()["message"] == "🗑️ Musik: 12 Index-Einträge gelöscht"
    assert client.put("/audio/settings", json={"tts_list_max": 0}).status_code == 422
    assert client.put("/audio/settings", json={"tts_list_max": 7}).json()["tts_list_max"] == 7
