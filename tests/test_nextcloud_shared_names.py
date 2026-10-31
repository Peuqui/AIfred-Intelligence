"""Freigegebene Sammlungen ansprechen: Nextcloud hängt den Besitzer an den Anzeigenamen
(„Urlaub (Peuqui)“), der Benutzer sagt und AIfred schreibt „Urlaub“.

Kontrakt von NextcloudStore._target:
- Der Name gilt mit und ohne den Zusatz, den der Server als Besitzer nennt.
- Nur dieser Zusatz fällt weg: Andere Klammern bleiben Teil des Namens.
- Mehrdeutiges wird abgelehnt, statt zu raten; die Ablehnung nennt die Namen.
"""

import pytest

from aifred.lib.plugin_modules import ModuleAccess
from aifred.plugins.tools.nextcloud.dav import CALENDAR, DavCollection
from aifred.plugins.tools.nextcloud.errors import NextcloudRefused
from aifred.plugins.tools.nextcloud.settings import NextcloudSettings
from aifred.plugins.tools.nextcloud.store import _EVENT, NextcloudStore


def _calendar(uri: str, name: str, owner: str = "Peuqui") -> DavCollection:
    return DavCollection(href=f"/dav/calendars/aifred/{uri}/", kind=CALENDAR, name=name, owner=owner,
                         components=frozenset({"VEVENT"}), writable=True, sync_token=None)


@pytest.fixture
def store():
    modules = ModuleAccess({"calendar": "write", "tasks": "off", "contacts": "off", "notes": "off", "files": "off"})
    nextcloud = NextcloudStore(NextcloudSettings("https://nextcloud.invalid/nextcloud", "aifred", modules), "unused")
    yield nextcloud
    nextcloud.close()


def _offer(store: NextcloudStore, monkeypatch: pytest.MonkeyPatch, *calendars: DavCollection) -> None:
    monkeypatch.setattr(store.client, "collections", lambda: list(calendars))


def test_shared_calendar_answers_to_its_plain_name(store, monkeypatch):
    urlaub = _calendar("BF85F99B", "Urlaub (Peuqui)")
    _offer(store, monkeypatch, _calendar("FEAA2F68", "Dienst (Peuqui)"), urlaub)
    assert store._target(_EVENT, "Urlaub") == urlaub
    assert store._target(_EVENT, "urlaub") == urlaub


def test_full_name_and_id_still_work(store, monkeypatch):
    urlaub = _calendar("BF85F99B", "Urlaub (Peuqui)")
    _offer(store, monkeypatch, _calendar("FEAA2F68", "Dienst (Peuqui)"), urlaub)
    assert store._target(_EVENT, "Urlaub (Peuqui)") == urlaub
    assert store._target(_EVENT, "BF85F99B") == urlaub


def test_other_brackets_stay_part_of_the_name(store, monkeypatch):
    kurs = _calendar("A", "Kurs (alt) (Peuqui)")
    eigener = _calendar("B", "Reise (privat)", owner="AIfred")
    _offer(store, monkeypatch, kurs, eigener)
    assert store._target(_EVENT, "Kurs (alt)") == kurs
    assert store._target(_EVENT, "Reise (privat)") == eigener
    for name in ("Kurs", "Reise"):
        with pytest.raises(NextcloudRefused):
            store._target(_EVENT, name)


def test_same_name_from_two_owners_is_refused_with_the_names(store, monkeypatch):
    celia = _calendar("B", "Urlaub (Célia)", owner="Célia")
    _offer(store, monkeypatch, _calendar("A", "Urlaub (Peuqui)"), celia)
    with pytest.raises(NextcloudRefused, match=r"Urlaub \(Peuqui\), Urlaub \(Célia\)"):
        store._target(_EVENT, "Urlaub")
    assert store._target(_EVENT, "Urlaub (Célia)") == celia


def test_collection_without_owner_keeps_its_name(store, monkeypatch):
    zuletzt = _calendar("A", "Recently contacted", owner="")
    _offer(store, monkeypatch, zuletzt, _calendar("B", "Dienst (Peuqui)"))
    assert store._target(_EVENT, "Recently contacted") == zuletzt
