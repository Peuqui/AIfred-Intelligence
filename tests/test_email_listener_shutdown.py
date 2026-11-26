"""Ending the e-mail listener must not block the event loop of the Message Hub.

Until 26.11.2026 the cancellation called LOGOUT on the connection while a thread was still reading the IDLE answer on
it; the server never answers in IDLE, so the loop hung until the hub gave up after 10 s ("worker thread did not stop
cleanly") and no other channel could stop either.
"""

import asyncio
import imaplib
import socket
import threading
import time
from collections.abc import Buffer
from typing import TYPE_CHECKING

import pytest

from aifred.lib.credential_broker import broker
from aifred.plugins.channels import email_channel
from aifred.plugins.channels.email_channel import EmailChannel

if TYPE_CHECKING:
    _ImapBase = imaplib.IMAP4_SSL
else:
    _ImapBase = object

LIMIT_SECONDS = 2.0
BLOCKED_LOGOUT_SECONDS = 5.0


if TYPE_CHECKING:
    _SocketBase = socket.socket
else:
    _SocketBase = object


class FakeIdleSocket(_SocketBase):
    def __init__(self, closed: threading.Event) -> None:
        self._closed = closed

    def shutdown(self, how: int) -> None:
        self._closed.set()


class FakeIdleConnection(_ImapBase):
    """In IDLE the server answers nothing: reading blocks until the connection is closed, LOGOUT waits for an answer
    that never comes."""

    def __init__(self) -> None:
        self.closed = threading.Event()
        self.reading = threading.Event()
        self.shutdown_calls = 0
        self.logout_calls = 0
        self._socket = FakeIdleSocket(self.closed)

    def send(self, data: Buffer) -> None:
        pass

    def readline(self) -> bytes:
        self.reading.set()
        self.closed.wait()
        raise imaplib.IMAP4.abort("socket error: EOF")

    def logout(self) -> tuple[str, list[None] | list[bytes | tuple[bytes, bytes]]]:
        self.logout_calls += 1
        # Bounded, then the connection drops: the old code fails instead of hanging the suite on the reader thread.
        self.closed.wait(BLOCKED_LOGOUT_SECONDS)
        self.closed.set()
        return "BYE", []

    def socket(self) -> socket.socket:
        return self._socket

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self.closed.set()


def test_cancel_in_idle_closes_connection_without_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = FakeIdleConnection()
    monkeypatch.setattr(EmailChannel, "is_configured", lambda self: True)
    monkeypatch.setattr(broker, "get", lambda service, key: "993")
    monkeypatch.setattr(email_channel, "_connect_imap", lambda host, port, user, password: (connection, 1))
    monkeypatch.setattr(email_channel, "_get_existing_uids", lambda imap: set())
    monkeypatch.setattr(email_channel, "_load_checkpoint", lambda: (1, 1))
    channel = EmailChannel()
    monkeypatch.setattr(channel, "channel_log", lambda message, *args, **kwargs: None)
    elapsed: list[float] = []

    async def scenario() -> None:
        task = asyncio.create_task(channel.listener_loop())
        for _ in range(100):
            if connection.reading.is_set():
                break
            await asyncio.sleep(0.05)
        assert connection.reading.is_set(), "listener never reached the IDLE read"
        started = time.monotonic()
        task.cancel()
        await asyncio.wait_for(task, timeout=LIMIT_SECONDS)
        elapsed.append(time.monotonic() - started)

    try:
        asyncio.run(scenario())
    finally:
        connection.closed.set()  # on the old code: release the blocked LOGOUT and reader so no thread outlives the test
    assert connection.shutdown_calls == 1
    assert connection.logout_calls == 0
    assert elapsed[0] < LIMIT_SECONDS
