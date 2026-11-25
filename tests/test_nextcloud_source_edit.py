"""What the Nextcloud document source and its WebDAV client send to change the account (new folder, rename, move,
copy, delete), against a local server that records the requests and answers what Nextcloud answers (201, 204, 405 for
a folder in use, 412 for a target in use). No Nextcloud is contacted."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aifred.lib.document_sources import EditableDocumentSource
from aifred.lib.user_error import Conflict, Refused
from aifred.plugins.tools.nextcloud.dav import DavClient, DavConflict
from aifred.plugins.tools.nextcloud.source import NextcloudDocumentSource

FILES = "/nextcloud/remote.php/dav/files/ki/"


class Recorder(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests: list[tuple[str, str, str | None, str | None]] = []   # method, path, Destination, Overwrite
    folders_in_use = {FILES + "Projekt"}
    targets_in_use = {FILES + "Belegt.txt"}

    def _answer(self, ok: int) -> None:
        destination = self.headers.get("Destination")
        self.requests.append((self.command, self.path, destination, self.headers.get("Overwrite")))
        status = ok
        if self.command == "MKCOL" and self.path in self.folders_in_use:
            status = 405
        if destination and "Belegt.txt" in destination:
            status = 412
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_MKCOL(self) -> None:
        self._answer(201)

    def do_MOVE(self) -> None:
        self._answer(201)

    def do_COPY(self) -> None:
        self._answer(201)

    def do_DELETE(self) -> None:
        self._answer(204)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def dav() -> Iterator[DavClient]:
    Recorder.requests = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    client = DavClient(f"http://127.0.0.1:{httpd.server_address[1]}/nextcloud", "ki", "secret", timeout=5.0)
    yield client
    client.close()
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def source(dav: DavClient) -> NextcloudDocumentSource:
    return NextcloudDocumentSource(dav, writable=True)


def test_copy_file_sends_copy_with_destination_and_no_overwrite(dav: DavClient) -> None:
    dav.copy_file("Projekt/Plan.txt", "Archiv/Plan.txt")
    [(method, path, destination, overwrite)] = Recorder.requests
    assert (method, path, overwrite) == ("COPY", FILES + "Projekt/Plan.txt", "F")
    assert destination is not None and destination.endswith(FILES + "Archiv/Plan.txt")


def test_copy_file_onto_an_existing_target_is_a_conflict(dav: DavClient) -> None:
    with pytest.raises(DavConflict):
        dav.copy_file("Plan.txt", "Belegt.txt")


def test_make_folder_exists_ok_by_default_and_a_conflict_when_asked(dav: DavClient) -> None:
    dav.make_folder("Projekt")
    with pytest.raises(DavConflict):
        dav.make_folder("Projekt", exist_ok=False)


def test_a_nextcloud_source_is_editable(source: NextcloudDocumentSource) -> None:
    assert isinstance(source, EditableDocumentSource)


def test_the_source_changes_the_account_and_names_the_new_ids(source: NextcloudDocumentSource) -> None:
    async def run() -> list[str]:
        return [
            await source.create_folder("Projekt", "Neu"),
            await source.rename("Projekt/Plan.txt", "Plan2.txt"),
            await source.move("Projekt/Plan2.txt", "Archiv"),
            await source.copy("Archiv/Plan2.txt", "Projekt"),
        ]

    assert asyncio.run(run()) == ["Projekt/Neu", "Projekt/Plan2.txt", "Archiv/Plan2.txt", "Projekt/Plan2.txt"]
    asyncio.run(source.delete("Archiv/Plan2.txt"))
    assert [(method, path.removeprefix(FILES)) for method, path, _, _ in Recorder.requests] == [
        ("MKCOL", "Projekt/Neu"), ("MOVE", "Projekt/Plan.txt"), ("MOVE", "Projekt/Plan2.txt"),
        ("COPY", "Archiv/Plan2.txt"), ("DELETE", "Archiv/Plan2.txt"),
    ]


def test_a_name_or_folder_in_use_is_its_own_message(source: NextcloudDocumentSource) -> None:
    with pytest.raises(Conflict) as in_use:                              # 409, not a failure of Nextcloud
        asyncio.run(source.create_folder("", "Projekt"))
    assert in_use.value.key == "source_name_exists"
    with pytest.raises(Conflict) as taken:
        asyncio.run(source.rename("Plan.txt", "Belegt.txt"))
    assert taken.value.key == "source_name_exists"


def test_a_read_only_source_changes_nothing(dav: DavClient) -> None:
    read_only = NextcloudDocumentSource(dav, writable=False)
    with pytest.raises(Refused) as refused:                              # 403
        asyncio.run(read_only.delete("Plan.txt"))
    assert refused.value.key == "source_read_only" and Recorder.requests == []
