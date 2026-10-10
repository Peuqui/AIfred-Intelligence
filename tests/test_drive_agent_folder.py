"""Agents work in ONE Drive folder (GOOGLE_DRIVE_AGENT_FOLDER), nothing outside."""

import asyncio
import json
import re
from types import SimpleNamespace
from typing import Any

import pytest

from aifred.plugins.tools.google_suite.drive import agent_folder as agent_folder_module
from aifred.plugins.tools.google_suite.drive import tools as tools_module
from aifred.plugins.tools.google_suite.drive._api import FOLDER_MIME
from aifred.plugins.tools.google_suite.drive.agent_folder import OutsideAgentFolder

AGENT_FOLDER = "AIfred-Intelligence"


class FakeDrive:
    """My Drive with the agent folder, a subfolder and files inside and outside.
    The search answers with EVERY file — as if a crafted query had escaped the
    parents clause — so only the boundary filter keeps the outside out."""

    def __init__(self, roots: int = 1) -> None:
        self.files: dict[str, dict[str, Any]] = {}
        self.created = 0
        for n in range(roots):
            self.add(f"agent{n or ''}", AGENT_FOLDER, "root", FOLDER_MIME)
        self.add("sub", "Notizen", "agent", FOLDER_MIME)
        self.add("inside", "plan.txt", "agent")
        self.add("deep", "tief.txt", "sub")
        self.add("private", "Steuer.pdf", "root")
        self.add("other", "Privat", "root", FOLDER_MIME)
        self.add("private2", "geheim.txt", "other")

    def add(self, file_id: str, name: str, parent: str, mime: str = "text/plain") -> None:
        self.files[file_id] = {"id": file_id, "name": name, "mimeType": mime, "parents": [parent]}

    async def request(self, method: str, url: str, *, params: dict | None = None, json: dict | None = None,
                      timeout: float = 15.0) -> SimpleNamespace:
        params = params or {}
        path = url.split("/drive/v3")[1]
        if method == "GET" and path == "/files":
            return self._reply({"files": self._query(params["q"])})
        if method == "GET":
            return self._reply(self.files[path.removeprefix("/files/")])
        if method == "POST":
            self.created += 1
            new_id = f"new{self.created}"
            assert json is not None
            self.add(new_id, json["name"], (json.get("parents") or ["root"])[0], json["mimeType"])
            return self._reply({"id": new_id, "name": json["name"]})
        if method == "DELETE":
            del self.files[path.removeprefix("/files/")]
            return self._reply({})
        raise AssertionError(f"unexpected {method} {url}")

    def _query(self, query: str) -> list[dict]:
        if query.startswith("name = "):
            name = re.search(r"name = '([^']*)'", query).group(1)  # type: ignore[union-attr]
            return [f for f in self.files.values() if f["name"] == name and f["parents"] == ["root"]]
        if "fullText" in query or "(name contains" in query:
            return [f for f in self.files.values() if f["mimeType"] != FOLDER_MIME]
        parents = set(re.findall(r"'([^']+)' in parents", query))
        folders_only = f"mimeType = '{FOLDER_MIME}'" in query
        return [
            f for f in self.files.values()
            if f["parents"][0] in parents and (not folders_only or f["mimeType"] == FOLDER_MIME)
        ]

    @staticmethod
    def _reply(body: dict) -> SimpleNamespace:
        return SimpleNamespace(json=lambda: body)


@pytest.fixture
def drive(monkeypatch: pytest.MonkeyPatch) -> FakeDrive:
    fake = FakeDrive()
    monkeypatch.setattr(agent_folder_module, "_google_request", fake.request)
    monkeypatch.setattr(tools_module, "_google_request", fake.request)
    return fake


def _tools(name: str = AGENT_FOLDER) -> dict[str, Any]:
    return {tool.name: tool.executor for tool in tools_module.get_drive_tools(name)}


def _run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def _ids(result: str) -> set[str]:
    start, end = result.index("["), result.rindex("]") + 1
    return {entry["id"] for entry in json.loads(result[start:end])}


def test_list_without_folder_shows_the_agent_folder(drive: FakeDrive) -> None:
    assert _ids(_run(_tools()["google_drive_list_files"]())) == {"sub", "inside"}


def test_list_outside_is_refused(drive: FakeDrive) -> None:
    with pytest.raises(OutsideAgentFolder):
        _run(_tools()["google_drive_list_files"](folder_id="other"))


@pytest.mark.parametrize("query", ["Steuer", "name contains 'a') or (trashed=false"])
def test_search_returns_only_files_inside(drive: FakeDrive, query: str) -> None:
    assert _ids(_run(_tools()["google_drive_search"](query=query))) == {"inside", "deep"}


@pytest.mark.parametrize("file_id", ["private", "private2", "other"])
def test_reading_changing_deleting_outside_is_refused(drive: FakeDrive, file_id: str) -> None:
    tools = _tools()
    for call in (
        tools["google_drive_get_file"](file_id=file_id),
        tools["google_drive_update_file"](file_id=file_id, content="x"),
        tools["google_drive_delete_file"](file_id=file_id),
        tools["google_drive_move_file"](file_id=file_id, target_folder_id="agent"),
    ):
        with pytest.raises(OutsideAgentFolder):
            _run(call)
    assert file_id in drive.files


def test_the_agent_folder_itself_stays(drive: FakeDrive) -> None:
    with pytest.raises(OutsideAgentFolder):
        _run(_tools()["google_drive_delete_file"](file_id="agent"))
    assert "agent" in drive.files


def test_moving_out_is_refused(drive: FakeDrive) -> None:
    with pytest.raises(OutsideAgentFolder):
        _run(_tools()["google_drive_move_file"](file_id="inside", target_folder_id="other"))


def test_deleting_deep_inside_works(drive: FakeDrive) -> None:
    _run(_tools()["google_drive_delete_file"](file_id="deep"))
    assert "deep" not in drive.files


def test_new_folder_is_usable_in_the_same_turn(drive: FakeDrive) -> None:
    tools = _tools()
    created = json.loads(_run(tools["google_drive_create_folder"](name="Neu")))
    assert drive.files[created["id"]]["parents"] == ["agent"]
    assert _ids(_run(tools["google_drive_list_files"](folder_id=created["id"]))) == set()


def test_missing_agent_folder_is_created_in_my_drive(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDrive()
    monkeypatch.setattr(agent_folder_module, "_google_request", fake.request)
    monkeypatch.setattr(tools_module, "_google_request", fake.request)
    _run(_tools("Agenten")["google_drive_list_files"]())
    assert [f for f in fake.files.values() if f["name"] == "Agenten"][0]["parents"] == ["root"]


def test_two_folders_with_the_name_are_ambiguous(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDrive(roots=2)
    monkeypatch.setattr(agent_folder_module, "_google_request", fake.request)
    monkeypatch.setattr(tools_module, "_google_request", fake.request)
    with pytest.raises(RuntimeError, match="ambiguous"):
        _run(_tools()["google_drive_list_files"]())


def test_parameter_descriptions_name_the_folder() -> None:
    tool = next(t for t in tools_module.get_drive_tools("Ablage-X") if t.name == "google_drive_list_files")
    assert "'Ablage-X'" in tool.parameters["properties"]["folder_id"]["description"]
