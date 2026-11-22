"""Addresses the owner keeps in an address book.

A tool plugin with an address book offers it through an optional attribute ``address_book`` (an object
satisfying :class:`AddressBook`, or None while switched off). The core discovers it among the enabled
plugins and never imports a plugin: a disabled plugin simply offers no address book.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AddressBook(Protocol):
    def knows_mail_address(self, address: str) -> bool:
        """Whether a contact of this book has ``address`` (compared without case)."""


def known_mail_address(address: str) -> bool:
    """Whether ``address`` belongs to a contact in an address book an enabled, available plugin offers."""
    from .plugin_registry import discover_tools

    for plugin in discover_tools():
        book = getattr(plugin, "address_book", None)
        if book is not None and plugin.is_available() and book.knows_mail_address(address):
            return True
    return False
