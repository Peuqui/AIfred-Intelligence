"""A document source whose plugin is switched off: its endpoints answer 409 with the plugin's name (404 stays for a
source nobody knows), but its entries in the search index are core and come out also while the plugin is off.
The plugin tree, the source and the index are stand-ins; nothing touches data/ or a real marker."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from starlette.testclient import TestClient

from aifred.lib import document_sources, document_store, file_manager, plugin_registry
from aifred.lib.document_sources import source_index_name
from aifred.plugins.tools.google_suite.drive.source import DriveDocumentSource
from aifred.plugins.tools.nextcloud.source import NextcloudDocumentSource


class _Index:
    """Stand-in for the document store: one entry per indexed name with its section count and source."""

    def __init__(self) -> None:
        self.entries: dict[str, tuple[int, str]] = {
            source_index_name("stub", "a.txt"): (3, "stub"),
            source_index_name("stub", "Ordner/b.txt"): (2, "stub"),
            source_index_name("other", "c.txt"): (4, "other"),
            "lokal.txt": (5, ""),
        }

    def list_documents(self) -> list[dict[str, Any]]:
        return [{"filename": name, "total_chunks": chunks, "upload_date": "2026-11-14T10:00:00", "source": source}
                for name, (chunks, source) in self.entries.items()]

    async def delete_document(self, path: str, delete_file: bool = False) -> int:
        chunks, _source = self.entries.pop(path, (0, ""))
        return chunks


@pytest.fixture
def plugin_on(monkeypatch: pytest.MonkeyPatch) -> dict[str, bool]:
    """The source "stub" belongs to the plugin nextcloud (its real name text), "other" to google_suite; a switch per plugin."""
    state = {"nextcloud": False, "google_suite": True}
    monkeypatch.setattr(plugin_registry, "document_source_owners", lambda: {"stub": "nextcloud", "other": "google_suite"})
    monkeypatch.setattr(plugin_registry, "is_plugin_enabled", lambda name: state[name])
    monkeypatch.setattr(document_sources, "document_sources", dict)
    return state


@pytest.fixture
def index(monkeypatch: pytest.MonkeyPatch) -> _Index:
    stand_in = _Index()
    monkeypatch.setattr(document_store, "get_document_store", lambda: stand_in)
    monkeypatch.setattr(file_manager, "chunks_per_document", dict)
    return stand_in


@pytest.fixture
def client(plugin_on: dict[str, bool], index: _Index) -> Iterator[TestClient]:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import USERNAME_COOKIE_NAME, sign_username

    with TestClient(api_app) as test_client:
        test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        yield test_client


def test_each_plugin_names_its_source_key_and_the_source_uses_it() -> None:
    owners = plugin_registry.document_source_owners()
    assert owners == {NextcloudDocumentSource.key: "nextcloud", DriveDocumentSource.key: "google_suite"}


def test_a_source_of_a_plugin_that_is_off_is_409_with_the_plugin_name(client: TestClient) -> None:
    assert client.post("/documents/source/stub/index", json={"files": [{"id": "a.txt", "name": "a.txt"}], "folders": []}).status_code == 409


def test_an_unknown_source_stays_404(client: TestClient) -> None:
    refused = client.post("/documents/source/gibts-nicht/index", json={"files": [], "folders": []})
    assert refused.status_code == 404


def test_a_source_of_a_plugin_that_is_on_but_offers_none_stays_404(client: TestClient) -> None:
    refused = client.post("/documents/source/other/index", json={"files": [], "folders": []})
    assert refused.status_code == 404


def test_deindexing_works_with_the_plugin_off_and_touches_only_those_files(client: TestClient, index: _Index) -> None:
    answer = client.post("/documents/source/stub/deindex", json={"file_ids": ["a.txt"]})
    assert answer.status_code == 200 and answer.json()["chunks_removed"] == 3
    assert set(index.entries) == {source_index_name("stub", "Ordner/b.txt"), source_index_name("other", "c.txt"), "lokal.txt"}
    assert client.post("/documents/source/gibts-nicht/deindex", json={"file_ids": ["a.txt"]}).status_code == 404


def test_the_index_overview_lists_sources_with_their_plugin_state_and_one_click_takes_a_source_out(
    client: TestClient, index: _Index,
) -> None:
    overview = client.get("/documents/index/overview").json()
    sources = {source["key"]: source for source in overview["sources"]}
    assert (sources["stub"]["documents"], sources["stub"]["chunks"], sources["stub"]["plugin_on"]) == (2, 5, False)
    assert (sources["other"]["documents"], sources["other"]["plugin_on"]) == (1, True)
    after = client.post("/documents/index/sources/remove", json={"key": "stub"}).json()
    assert set(index.entries) == {source_index_name("other", "c.txt"), "lokal.txt"}
    assert [source["key"] for source in after["sources"]] == ["other"]
    assert client.post("/documents/index/sources/remove", json={"key": "gibts-nicht"}).status_code == 404
