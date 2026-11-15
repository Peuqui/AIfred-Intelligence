"""The Nextcloud client does not reuse a connection the server has already closed (a live-Nextcloud test failed now and
then with "Server disconnected without sending a response")."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aifred.plugins.tools.nextcloud import dav
from aifred.plugins.tools.nextcloud.dav import DavClient

SERVER_IDLE_TIMEOUT_S = 1.0


class IdleClosingHandler(BaseHTTPRequestHandler):
    """Keeps a connection alive, and closes it after it was idle for SERVER_IDLE_TIMEOUT_S, as an Apache does."""

    protocol_version = "HTTP/1.1"
    timeout = SERVER_IDLE_TIMEOUT_S

    def do_DELETE(self) -> None:
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def server() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), IdleClosingHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/nextcloud"
    httpd.shutdown()
    httpd.server_close()


def test_a_request_after_the_server_closed_the_idle_connection_goes_through(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dav, "NEXTCLOUD_KEEPALIVE_EXPIRY_S", SERVER_IDLE_TIMEOUT_S / 2)
    client = DavClient(server, "ki", "secret", timeout=5.0)
    client.delete_file("a.txt")
    time.sleep(SERVER_IDLE_TIMEOUT_S * 1.3)   # the server has closed the connection by now
    client.delete_file("b.txt")
    client.close()
