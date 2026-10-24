"""Nextcloud DAV layer against the live server, in the test collections of
a test user of its own (``aifred-test`` calendar and address book). The user
AIfred runs as sees only what is shared with it, never test data.

Credentials come from a local file (never versioned); without it the tests
are skipped. Every test removes what it creates.
"""

# EPIM and CalDAV clients here work with naive local wall-clock time.
# ruff: noqa: DTZ001

import uuid
from datetime import datetime
from pathlib import Path

import pytest

from aifred.plugins.tools.nextcloud.dav import (
    ADDRESSBOOK,
    CALENDAR,
    DavClient,
    DavConflict,
    DavError,
    DavObject,
)

CREDENTIALS = Path.home() / ".config" / "aifred" / "nextcloud-test.env"
TEST_COLLECTION = "aifred-test"

pytestmark = pytest.mark.skipif(not CREDENTIALS.exists(), reason=f"no Nextcloud test credentials ({CREDENTIALS})")


def _credentials() -> dict[str, str]:
    pairs = (line.split("=", 1) for line in CREDENTIALS.read_text(encoding="utf-8").splitlines()
             if line and not line.startswith("#"))
    return {k: v for k, v in pairs}


@pytest.fixture
def client():
    creds = _credentials()
    dav = DavClient(creds["NEXTCLOUD_URL"], creds["NEXTCLOUD_USER"], creds["NEXTCLOUD_APP_PASSWORD"], 20.0)
    yield dav
    dav.close()


def _collection(client: DavClient, kind: str):
    return next(c for c in client.collections() if c.kind == kind and c.name == TEST_COLLECTION)


def _event(uid: str, summary: str, rrule: str = "") -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//AIfred//test//DE", "BEGIN:VEVENT", f"UID:{uid}",
             "DTSTAMP:20261023T120000Z", "DTSTART;TZID=Europe/Berlin:20261103T100000",
             "DTEND;TZID=Europe/Berlin:20261103T113000", f"SUMMARY:{summary}"]
    if rrule:
        lines.append(f"RRULE:{rrule}")
    return "\r\n".join([*lines, "END:VEVENT", "END:VCALENDAR", ""])


def test_collections_report_kind_components_and_rights(client):
    calendar = _collection(client, CALENDAR)
    assert calendar.writable and {"VEVENT", "VTODO"} <= calendar.components
    assert _collection(client, ADDRESSBOOK).writable
    read_only = [c for c in client.collections() if c.kind == ADDRESSBOOK and not c.writable]
    assert read_only  # Nextcloud's system address books are not writable


def test_event_roundtrip_expansion_and_change_tracking(client):
    calendar = _collection(client, CALENDAR)
    uid = f"aifred-test-{uuid.uuid4()}"
    href = f"{calendar.href}{uid}.ics"
    token = calendar.sync_token
    assert token is not None
    client.create(href, _event(uid, "Serie Grüße", "FREQ=WEEKLY;COUNT=3"), "text/calendar; charset=utf-8")
    try:
        found = [o for o in client.calendar_objects(calendar.href, "VEVENT", datetime(2026, 11, 1), datetime(2026, 12, 1))
                 if uid in o.data]
        assert len(found) == 1 and found[0].data.count("BEGIN:VEVENT") == 3
        assert "Serie Grüße" in found[0].data
        assert href in client.changes(calendar.href, token).changed
    finally:
        client.delete(client.get(href))
    assert href in client.changes(calendar.href, token).removed


def test_stale_write_is_refused(client):
    calendar = _collection(client, CALENDAR)
    uid = f"aifred-test-{uuid.uuid4()}"
    href = f"{calendar.href}{uid}.ics"
    client.create(href, _event(uid, "Erst"), "text/calendar; charset=utf-8")
    try:
        current = client.get(href)
        client.replace(current, _event(uid, "Zweit"), "text/calendar; charset=utf-8")
        with pytest.raises(DavConflict):
            client.replace(current, _event(uid, "Dritt"), "text/calendar; charset=utf-8")
        assert "Zweit" in client.get(href).data
        with pytest.raises(DavConflict):
            client.create(href, _event(uid, "Doppelt"), "text/calendar; charset=utf-8")
    finally:
        client.delete(client.get(href))


def test_contact_roundtrip(client):
    book = _collection(client, ADDRESSBOOK)
    uid = f"aifred-test-{uuid.uuid4()}"
    href = f"{book.href}{uid}.vcf"
    card = f"BEGIN:VCARD\r\nVERSION:3.0\r\nUID:{uid}\r\nFN:Jörg Meier\r\nN:Meier;Jörg;;;\r\nEND:VCARD\r\n"
    client.create(href, card, "text/vcard; charset=utf-8")
    try:
        assert any("FN:Jörg Meier" in o.data for o in client.addressbook_objects(book.href) if o.href == href)
    finally:
        client.delete(client.get(href))
    with pytest.raises(DavError):
        client.delete(DavObject(href, '"gone"', ""))
