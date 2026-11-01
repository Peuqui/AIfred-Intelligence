"""The local mail index.

Kontrakt (email_channel/mail_index.py):
- Alle Wörter müssen vorkommen, auch als Wortteil, in Kopf, Text oder Anhängen; ohne Rücksicht auf
  Groß- und Kleinschreibung und Umlaute. Kurze Wörter werden ebenfalls gefunden.
- Das Postfach bleibt die Wahrheit: Vor jeder Suche wird abgeglichen, Gelöschtes verschwindet,
  Neues kommt dazu, schon Gelesenes wird nicht noch einmal geholt, ein neues UIDVALIDITY erneuert
  den Ordner.
- Der erste Aufbau läuft im Hintergrund, setzt nach einem Abbruch fort, und eine Suche währenddessen
  sagt, wie viele Mails noch fehlen.
- Eine unlesbare Mail hält den Aufbau nicht auf.

Ohne Netz: FakeMailbox ist die Lesefläche der IMAP-Verbindung.
"""

from __future__ import annotations

import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from aifred.plugins.channels.email_channel import mail_index
from aifred.plugins.channels.email_channel.mail_index import MailIndex, MailText


class FakeMailbox:
    def __init__(self) -> None:
        self.mails: dict[str, dict[int, MailText]] = {"INBOX": {}, "Archiv": {}}
        self.validity: dict[str, int] = {"INBOX": 1, "Archiv": 1}
        self.fetched: list[tuple[str, int]] = []
        self.current = "INBOX"
        self.hold_at: int | None = None
        self.release = threading.Event()

    def folders(self) -> list[str]:
        return list(self.mails)

    def open(self, folder: str) -> None:
        self.current = folder

    def uid_validity(self) -> int:
        return self.validity[self.current]

    def search(self, criteria: str) -> list[bytes]:
        assert criteria == "ALL"
        return [str(uid).encode() for uid in sorted(self.mails[self.current])]

    def fetch_message(self, uid: str) -> list:
        if self.hold_at is not None and len(self.fetched) >= self.hold_at:
            self.release.wait(5)
        self.fetched.append((self.current, int(uid)))
        if int(uid) not in self.mails[self.current]:
            return [None]
        return [(b"1 (BODY[])", f"{self.current}/{uid}".encode()), b")"]

    @contextmanager
    def opened(self) -> Iterator[FakeMailbox]:
        yield self

    def read(self, raw: bytes) -> MailText:
        folder, uid = raw.decode().split("/")
        return self.mails[folder][int(uid)]


@pytest.fixture
def box() -> FakeMailbox:
    return FakeMailbox()


@pytest.fixture
def index(tmp_path: Path) -> MailIndex:
    return MailIndex(tmp_path / "index" / "mail.sqlite")


def _refresh(index: MailIndex, box: FakeMailbox) -> None:
    index.refresh(box.opened, box.read)


def _uids(index: MailIndex, query: str, folder: str | None = None) -> list[int]:
    return [uid for _, uid in index.find(query, folder)]


class TestFinding:
    @pytest.fixture(autouse=True)
    def _mails(self, index: MailIndex, box: FakeMailbox) -> None:
        box.mails["INBOX"][1] = MailText(
            subject="Ihre Heizölrechnung", sender="Müller GmbH <m@firma.test>", recipients="peuqui@test.example",
            body="Betrag 2.350 Euro für die Wechseldruckmatratze", attachments="vertrag.pdf\nLaufzeit bis Dezember",
            sent_at=100.0,
        )
        box.mails["Archiv"][2] = MailText(subject="Heizöl alt", body="KI im Alltag", sent_at=50.0)
        box.mails["Archiv"][3] = MailText(subject="Heizöl neu", sent_at=200.0)
        _refresh(index, box)

    @pytest.mark.parametrize("query", [
        "Heizölrechnung", "heizölrechnung", "HEIZOLRECHNUNG", "matratze", "Müller", "muller", "2.350",
        "vertrag.pdf", "Laufzeit", "peuqui@test.example", "Heizöl Laufzeit",
    ])
    def test_found_anywhere_also_as_a_part_of_a_word(self, index: MailIndex, query: str) -> None:
        assert _uids(index, query) == [1]

    def test_every_word_must_occur(self, index: MailIndex) -> None:
        assert _uids(index, "Heizöl Dezember") == [1]
        assert _uids(index, "Heizöl Januar") == []

    def test_newest_first_across_folders(self, index: MailIndex) -> None:
        assert _uids(index, "Heizöl") == [3, 1, 2]

    def test_a_named_folder_limits_the_search(self, index: MailIndex) -> None:
        assert _uids(index, "Heizöl", "Archiv") == [3, 2]
        assert _uids(index, "Heizöl", "Gibtsnicht") == []

    def test_short_words_are_found_too(self, index: MailIndex) -> None:
        assert _uids(index, "KI") == [2]
        assert _uids(index, "KI Heizöl") == [2]

    @pytest.mark.parametrize("query", ['"', 'x" OR y', "NOT AND", "(((", "col:umn", "*", "", "   ", "a%", "%"])
    def test_a_query_is_never_syntax(self, index: MailIndex, query: str) -> None:
        index.find(query)


class TestFollowsTheMailbox:
    def test_new_mail_is_found_after_the_next_refresh(self, index: MailIndex, box: FakeMailbox) -> None:
        _refresh(index, box)
        box.mails["INBOX"][1] = MailText(subject="Neu")
        _refresh(index, box)
        assert _uids(index, "Neu") == [1]

    def test_what_was_read_is_not_fetched_again(self, index: MailIndex, box: FakeMailbox) -> None:
        box.mails["INBOX"][1] = MailText(subject="Einmal")
        _refresh(index, box)
        _refresh(index, box)
        assert box.fetched == [("INBOX", 1)]

    def test_a_deleted_mail_disappears(self, index: MailIndex, box: FakeMailbox) -> None:
        box.mails["INBOX"][1] = MailText(subject="Weg")
        _refresh(index, box)
        del box.mails["INBOX"][1]
        _refresh(index, box)
        assert _uids(index, "Weg") == []

    def test_a_moved_mail_names_its_new_folder(self, index: MailIndex, box: FakeMailbox) -> None:
        box.mails["INBOX"][1] = MailText(subject="Wandert")
        _refresh(index, box)
        box.mails["Archiv"][9] = box.mails["INBOX"].pop(1)
        _refresh(index, box)
        assert index.find("Wandert") == [("Archiv", 9)]

    def test_a_deleted_folder_disappears(self, index: MailIndex, box: FakeMailbox) -> None:
        box.mails["Archiv"][1] = MailText(subject="Ordner")
        _refresh(index, box)
        del box.mails["Archiv"]
        _refresh(index, box)
        assert _uids(index, "Ordner") == []

    def test_a_new_uidvalidity_renews_the_folder(self, index: MailIndex, box: FakeMailbox) -> None:
        box.mails["INBOX"][1] = MailText(subject="Alt")
        _refresh(index, box)
        box.mails["INBOX"][1] = MailText(subject="Andere Mail, gleiche UID")
        box.validity["INBOX"] = 2
        _refresh(index, box)
        assert _uids(index, "Alt") == [] and _uids(index, "gleiche") == [1]


class TestBuilding:
    @pytest.fixture
    def many(self, box: FakeMailbox) -> FakeMailbox:
        box.mails["INBOX"] = {uid: MailText(subject=f"Mail{uid}x", sent_at=float(uid)) for uid in range(1, 61)}
        return box

    def test_a_first_build_does_not_block_the_search(self, index: MailIndex, many: FakeMailbox) -> None:
        many.hold_at = 5
        _refresh(index, many)
        assert index.pending > 0
        assert len(_uids(index, "Mail")) <= 5
        many.release.set()
        index.wait_for_build()
        assert index.pending == 0 and len(_uids(index, "Mail")) == 60

    def test_the_newest_mails_come_first(self, index: MailIndex, many: FakeMailbox) -> None:
        many.hold_at = 3
        _refresh(index, many)
        many.release.set()
        index.wait_for_build()
        assert [uid for _, uid in many.fetched[:3]] == [60, 59, 58]

    def test_a_search_during_the_build_does_not_start_a_second_one(self, index: MailIndex, many: FakeMailbox) -> None:
        many.hold_at = 5
        _refresh(index, many)
        _refresh(index, many)
        many.release.set()
        index.wait_for_build()
        assert len(many.fetched) == 60

    def test_a_broken_connection_goes_on_where_it_stopped(self, index: MailIndex, many: FakeMailbox) -> None:
        original = many.fetch_message

        def break_after_ten(uid: str) -> list:
            if len(many.fetched) == 10:
                raise OSError("connection lost")
            return original(uid)

        many.fetch_message = break_after_ten  # type: ignore[method-assign]
        _refresh(index, many)
        index.wait_for_build()
        assert len(_uids(index, "Mail")) == 10
        many.fetch_message = original  # type: ignore[method-assign]
        _refresh(index, many)
        index.wait_for_build()
        assert len(_uids(index, "Mail")) == 60
        assert len(many.fetched) == 60

    def test_few_new_mails_are_read_within_the_search(self, index: MailIndex, box: FakeMailbox) -> None:
        box.mails["INBOX"] = {uid: MailText(subject="Wenige") for uid in range(1, mail_index.READ_WHILE_SEARCHING + 1)}
        _refresh(index, box)
        assert index.pending == 0 and len(_uids(index, "Wenige")) == mail_index.READ_WHILE_SEARCHING

    def test_a_refused_connection_leaves_the_index_usable(self, index: MailIndex, box: FakeMailbox) -> None:
        @contextmanager
        def refused() -> Iterator[FakeMailbox]:
            raise OSError("refused")
            yield box

        with pytest.raises(OSError):
            index.refresh(refused, box.read)
        box.mails["INBOX"][1] = MailText(subject="Danach")
        _refresh(index, box)
        assert _uids(index, "Danach") == [1]

    def test_an_unreadable_mail_does_not_stop_the_build(self, index: MailIndex, box: FakeMailbox) -> None:
        box.mails["INBOX"] = {1: MailText(subject="Gut"), 2: MailText(subject="Auch gut")}

        def read(raw: bytes) -> MailText:
            if raw.endswith(b"/2"):
                raise ValueError("kaputt")
            return box.read(raw)

        index.refresh(box.opened, read)
        assert _uids(index, "Gut") == [1] and box.fetched == [("INBOX", 2), ("INBOX", 1)]
        index.refresh(box.opened, read)
        assert len(box.fetched) == 2


def test_only_the_owner_can_read_the_file(tmp_path: Path) -> None:
    path = tmp_path / "index" / "mail.sqlite"
    MailIndex(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
