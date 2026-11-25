"""The IDLE cycle of AIfred's mailbox: a quiet window ends with DONE on the same connection, no fresh login.

Before 25.11.2026 the read of the IDLE event was abandoned after the window (asyncio gives a thread up, it does not stop
it); that read swallowed the server's answer to DONE, the drain waited in vain and the cycle raised "IDLE exit timeout",
which logged in again: every five minutes, about 290 logins a day.
"""

import asyncio
import imaplib
import queue
import threading
from collections.abc import Buffer
from typing import TYPE_CHECKING

import pytest

from aifred.plugins.channels import email_channel
from aifred.plugins.channels.email_channel import EmailChannel

if TYPE_CHECKING:
    _ImapBase = imaplib.IMAP4_SSL
else:
    _ImapBase = object


class FakeIdleServer(_ImapBase):
    """A connection whose readline blocks like a socket until the server says something. Two reads at once on one
    connection are the fault: on a real socket the earlier one takes the next line."""

    def __init__(self) -> None:
        self.lines: queue.Queue[bytes] = queue.Queue()
        self.lines.put(b"+ idling\r\n")
        self.reading = 0
        self.overlapping_reads = 0
        self.lock = threading.Lock()

    def send(self, data: Buffer) -> None:
        if bytes(data) == b"DONE\r\n":
            self.lines.put(b"A001 OK Idle completed\r\n")

    def readline(self) -> bytes:
        with self.lock:
            self.reading += 1
            if self.reading > 1:
                self.overlapping_reads += 1
        try:
            return self.lines.get(timeout=3)
        finally:
            with self.lock:
                self.reading -= 1


@pytest.fixture
def short_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(email_channel, "_IDLE_TIMEOUT_SECONDS", 0.2)


def test_quiet_window_ends_without_reconnect(short_window: None) -> None:
    server = FakeIdleServer()
    asyncio.run(asyncio.wait_for(EmailChannel()._idle_cycle(server), timeout=5))
    assert server.lines.empty()  # the answer to DONE was read, nothing left over
    assert server.overlapping_reads == 0


def test_event_in_window_is_read_and_drained(short_window: None) -> None:
    server = FakeIdleServer()
    server.lines.put(b"* 3 EXISTS\r\n")
    logged: list[str] = []
    channel = EmailChannel()
    channel.channel_log = lambda message, *args, **kwargs: logged.append(message)  # type: ignore[method-assign]
    asyncio.run(asyncio.wait_for(channel._idle_cycle(server), timeout=5))
    assert server.lines.empty()
    assert server.overlapping_reads == 0
    assert any("IDLE wakeup — * 3 EXISTS" in line for line in logged)
