"""The name of the machine AIfred runs on: what the pages say instead of "this machine". Peuqui opens the pages of one
machine from another (the Mini's from Aragon), so "this machine" would mean the wrong one to him."""

from __future__ import annotations

import socket


def machine_name() -> str:
    """The name of this machine, as the system has it."""
    return socket.gethostname()
