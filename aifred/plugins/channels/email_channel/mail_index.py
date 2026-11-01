"""A local word index over one mailbox.

Searching on the mail server only finds whole words ("matratze" not in
"Wechseldruckmatratze") and no attachments. So the text of every mail lies here, on
trigrams like the document search. The mailbox stays the truth: the index is brought up
to date before every search, is opened read-only there, and can be built anew at any time.
"""

import os
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ....lib.logging_utils import log_message
from ....lib.text_search import (
    MIN_INDEXED_WORD_LENGTH,
    fts_phrase,
    query_words,
    without_marks,
)

# Up to here a search reads what is new itself. More, above all the first build, goes on
# in the background: a search must not wait minutes, it answers from what is there.
READ_WHILE_SEARCHING = 25

_COLUMNS = ("subject", "sender", "recipients", "body", "attachments")
_IN_ANY_COLUMN = "(" + " OR ".join(f"mail_words.{column} LIKE ?" for column in _COLUMNS) + ")"


@dataclass(frozen=True)
class MailText:
    """What a search can find in a mail."""

    subject: str = ""
    sender: str = ""
    recipients: str = ""
    body: str = ""
    attachments: str = ""
    sent_at: float = 0.0


class Mailbox(Protocol):
    """The reading part of an IMAP connection."""

    def folders(self) -> list[str]: ...
    def open(self, folder: str) -> None: ...
    def uid_validity(self) -> int: ...
    def search(self, criteria: str) -> list[bytes]: ...
    def fetch_message(self, uid: str) -> list: ...


OpenMailbox = Callable[[], AbstractContextManager[Mailbox]]
ReadMail = Callable[[bytes], MailText]


class MailIndex:
    """The index file of one mailbox. ``pending`` counts the mails not read yet."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self.pending = 0
        self._busy = threading.Lock()
        self._worker: threading.Thread | None = None
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._database() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS folders(name TEXT PRIMARY KEY, uid_validity INTEGER NOT NULL)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS mails(id INTEGER PRIMARY KEY, folder TEXT NOT NULL, "
                "uid INTEGER NOT NULL, sent_at REAL NOT NULL, UNIQUE(folder, uid))"
            )
            db.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS mail_words USING fts5({', '.join(_COLUMNS)}, tokenize='trigram')")
        os.chmod(path, 0o600)

    @contextmanager
    def _database(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self._path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def refresh(self, open_mailbox: OpenMailbox, read_mail: ReadMail) -> None:
        """Bring the index up to date. While a build is running this returns at once."""
        if not self._busy.acquire(blocking=False):
            return
        handed_over = False
        try:
            with open_mailbox() as mailbox:
                missing = self._match_folders(mailbox)
                self.pending = sum(map(len, missing.values()))
                if self.pending <= READ_WHILE_SEARCHING:
                    self._read_missing(mailbox, missing, read_mail)
            if self.pending:
                self._worker = threading.Thread(
                    target=self._read_in_background, args=(open_mailbox, missing, read_mail),
                    name="mail-index", daemon=True,
                )
                self._worker.start()
                handed_over = True
        finally:
            if not handed_over:
                self._busy.release()

    def wait_for_build(self) -> None:
        if self._worker is not None:
            self._worker.join()

    def _read_in_background(self, open_mailbox: OpenMailbox, missing: dict[str, list[int]], read_mail: ReadMail) -> None:
        try:
            with open_mailbox() as mailbox:
                self._read_missing(mailbox, missing, read_mail)
            log_message("📧 Mail index: build finished")
        except Exception as exc:  # noqa: BLE001 — the thread ends here; the next search goes on where this stopped
            log_message(f"📧 Mail index: build stopped with {self.pending} mails left: {exc}", "warning")
        finally:
            self._busy.release()

    def _match_folders(self, mailbox: Mailbox) -> dict[str, list[int]]:
        """Forget what the mailbox no longer holds. Returns the UIDs to read per folder,
        newest first: during a first build the mails that matter most are there first."""
        missing: dict[str, list[int]] = {}
        with self._database() as db:
            known = dict(db.execute("SELECT name, uid_validity FROM folders"))
            names = mailbox.folders()
            for gone in set(known) - set(names):
                self._forget_folder(db, gone)
                db.execute("DELETE FROM folders WHERE name = ?", (gone,))
            for name in names:
                mailbox.open(name)
                validity = mailbox.uid_validity()
                if known.get(name) != validity:
                    # A new UIDVALIDITY means the old UIDs now name other mails.
                    self._forget_folder(db, name)
                    db.execute("INSERT OR REPLACE INTO folders VALUES (?, ?)", (name, validity))
                stored = dict(db.execute("SELECT uid, id FROM mails WHERE folder = ?", (name,)))
                on_server = {int(uid) for uid in mailbox.search("ALL")}
                self._forget_mails(db, [stored[uid] for uid in stored.keys() - on_server])
                if new := sorted(on_server - stored.keys(), reverse=True):
                    missing[name] = new
        return missing

    @staticmethod
    def _forget_mails(db: sqlite3.Connection, ids: list[int]) -> None:
        db.executemany("DELETE FROM mail_words WHERE rowid = ?", ((mail_id,) for mail_id in ids))
        db.executemany("DELETE FROM mails WHERE id = ?", ((mail_id,) for mail_id in ids))

    def _forget_folder(self, db: sqlite3.Connection, folder: str) -> None:
        self._forget_mails(db, [row[0] for row in db.execute("SELECT id FROM mails WHERE folder = ?", (folder,))])

    def _read_missing(self, mailbox: Mailbox, missing: dict[str, list[int]], read_mail: ReadMail) -> None:
        for folder, uids in missing.items():
            mailbox.open(folder)
            for uid in uids:
                raw = next((part[1] for part in mailbox.fetch_message(str(uid)) if isinstance(part, tuple)), None)
                # None: gone since the folders were matched.
                if raw is not None:
                    self._store(folder, uid, self._text_of(folder, uid, raw, read_mail))
                self.pending -= 1

    @staticmethod
    def _text_of(folder: str, uid: int, raw: bytes, read_mail: ReadMail) -> MailText:
        try:
            return read_mail(raw)
        except Exception as exc:  # noqa: BLE001 — stored empty, or this one mail would stop every later build
            log_message(f"📧 Mail index: mail {uid} in {folder} is unreadable: {exc}", "warning")
            return MailText()

    def _store(self, folder: str, uid: int, text: MailText) -> None:
        with self._database() as db:
            cursor = db.execute(
                "INSERT INTO mails(folder, uid, sent_at) VALUES (?, ?, ?)", (folder, uid, text.sent_at)
            )
            db.execute(
                f"INSERT INTO mail_words(rowid, {', '.join(_COLUMNS)}) VALUES (?, ?, ?, ?, ?, ?)",
                (cursor.lastrowid, *(without_marks(getattr(text, column)) for column in _COLUMNS)),
            )

    def find(self, query: str, folder: str | None = None) -> list[tuple[str, int]]:
        """(folder, UID) of every mail holding all words of ``query``, newest first."""
        words = query_words(query)
        indexed = [word for word in words if len(word) >= MIN_INDEXED_WORD_LENGTH]
        short = [word for word in words if len(word) < MIN_INDEXED_WORD_LENGTH]
        if not words:
            return []
        conditions: list[str] = []
        parameters: list[str] = []
        if indexed:
            conditions.append("mail_words MATCH ?")
            parameters.append(" AND ".join(fts_phrase(word) for word in indexed))
        for word in short:
            conditions.append(_IN_ANY_COLUMN)
            parameters += [f"%{word}%"] * len(_COLUMNS)
        if folder is not None:
            conditions.append("mails.folder = ?")
            parameters.append(folder)
        with self._database() as db:
            return db.execute(
                "SELECT mails.folder, mails.uid FROM mail_words JOIN mails ON mails.id = mail_words.rowid "
                f"WHERE {' AND '.join(conditions)} ORDER BY mails.sent_at DESC, mails.id DESC",
                parameters,
            ).fetchall()
