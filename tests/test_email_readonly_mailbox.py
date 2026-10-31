"""The second mailbox: strictly read-only, and AIfred's own never stands in for it.

Kontrakt (email_channel/client.py, tools.py):
- Lesen öffnet mit EXAMINE (``select(..., readonly=True)``) und holt nur mit
  BODY.PEEK; STORE, COPY, APPEND, EXPUNGE werden nie gerufen.
- ``email_readonly`` gibt es nur mit eigenen Zugangsdaten, trägt TIER_WRITE_DATA und
  kennt nur check, read, search. Die Zugangsdaten von AIfreds Postfach springen nie ein.
- Das Postfach hat keinen Listener; Schreibendes erreicht nur AIfreds eigenes Postfach.
- Mails daraus bekommen nie das Owner-Label.
- Der Weg über das ``email``-Werkzeug läuft weiter auf AIfreds Postfach, mit Owner-Urteil.
- Die Liste zeigt Datum, Absender, Betreff und den Lesestatus, keinen Inhalt; sie sagt, wie viele
  von wie vielen sie zeigt. ``unread`` liefert alle ungelesenen.
- ``read`` nimmt mehrere IDs, über eine Verbindung. Mails werden nie still gekürzt: Passt nicht
  alles ins Budget des Modells, nennt die Antwort die übrigen IDs für den nächsten Schritt.
- Aus HTML wird lesbarer Text, ohne Technik und ohne Inhalt zu verlieren.

Ohne Netz: FakeImap ersetzt imaplib.IMAP4_SSL und antwortet in der Form von imaplib.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from email import message_from_bytes, policy
from email.message import EmailMessage
from pathlib import Path
from typing import Self

import pytest

from aifred.lib.plugin_base import PluginContext
from aifred.lib.security import TIER_COMMUNICATE, TIER_WRITE_DATA, TIER_WRITE_SYSTEM
from aifred.lib.tool_output_cap import budget_var
from aifred.plugins.channels import email_channel
from aifred.plugins.channels.email_channel import EmailChannel, client
from aifred.plugins.channels.email_channel.tools import get_readonly_mailbox_tools

OWN_HOST = "imap.own.test"
OWN_USER = "aifred@own.test"
OWN_PASSWORD = "own-secret"
READONLY_HOST = "imap.private.test"
READONLY_USER = "leser@private.test"
READONLY_PASSWORD = "private-secret"
OWNER_ADDRESS = "owner@private.test"

OWN_ACCOUNT = {
    "EMAIL_ENABLED": "true",
    "EMAIL_IMAP_HOST": OWN_HOST,
    "EMAIL_USER": OWN_USER,
    "EMAIL_PASSWORD": OWN_PASSWORD,
}
READONLY_ACCOUNT = {
    "EMAIL_READONLY_IMAP_HOST": READONLY_HOST,
    "EMAIL_READONLY_IMAP_PORT": "993",
    "EMAIL_READONLY_USER": READONLY_USER,
    "EMAIL_READONLY_PASSWORD": READONLY_PASSWORD,
}
OTHER_KEYS = ("EMAIL_IMAP_PORT", "EMAIL_FROM", "EMAIL_ALLOWED_SENDERS")


def _mail(subject: str, sender: str, body: str, auth_results: str | None = None) -> bytes:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = READONLY_USER
    message["Date"] = "Sat, 31 Oct 2026 09:30:00 +0100"
    if auth_results:
        message["Authentication-Results"] = auth_results
    message.set_content(body)
    return message.as_bytes()


def _html_mail(subject: str, html: str) -> bytes:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = "Anna <anna@freunde.test>"
    message["To"] = READONLY_USER
    message["Date"] = "Sat, 31 Oct 2026 09:30:00 +0100"
    message.set_content(html, subtype="html")
    return message.as_bytes()


def _both_parts_mail(subject: str, text: str, html: str) -> bytes:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = "Anna <anna@freunde.test>"
    message["To"] = READONLY_USER
    message["Date"] = "Sat, 31 Oct 2026 09:30:00 +0100"
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    return message.as_bytes()


def _searchable_headers(raw: bytes) -> str:
    """Subject and sender as a server searches them: decoded, not as RFC 2047 bytes."""
    message = message_from_bytes(raw, policy=policy.default)
    return f"{message['Subject']} {message['From']}"


class FakeServer:
    """The mailbox behind every FakeImap, and the record of what happened to it."""

    def __init__(self) -> None:
        self.mails: dict[int, tuple[bytes, str]] = {
            5: (_mail("Rechnung Oktober", "Stadtwerke <rechnung@stadtwerke.test>", "Bitte bis Freitag zahlen."), r"\Seen"),
            7: (_mail("Treffen bei Müller", "Anna <anna@freunde.test>", "Sehen wir uns am Samstag?"), ""),
        }
        self.connections: list[FakeImap] = []
        self.selects: list[tuple[str, bool]] = []
        self.fetches: list[str] = []
        self.searches: list[tuple] = []

    def connect(self, host: str, port: int, ssl_context: object = None, timeout: float | None = None) -> FakeImap:
        connection = FakeImap(self, host, port)
        self.connections.append(connection)
        return connection


class FakeImap:
    """Stands in for imaplib.IMAP4_SSL. Answers what the read path may use; any other
    attribute (store, copy, append, expunge, …) fails the test."""

    def __init__(self, server: FakeServer, host: str, port: int) -> None:
        self.server = server
        self.host = host
        self.port = port
        self.user = ""
        self.password = ""
        self.literal: bytes | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def __getattr__(self, name: str) -> None:
        raise AssertionError(f"IMAP command the read path must not use: {name}")

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        self.user, self.password = user, password
        return "OK", [b"logged in"]

    def select(self, mailbox: str = "INBOX", readonly: bool = False) -> tuple[str, list[bytes]]:
        self.server.selects.append((mailbox, readonly))
        if mailbox not in ("INBOX", "Archiv"):
            return "NO", [b"no such mailbox"]
        return "OK", [str(len(self.server.mails)).encode()]

    def uid(self, command: str, *args: object) -> tuple[str, list]:
        if command == "SEARCH":
            return "OK", [self._search(args)]
        if command == "FETCH":
            uid, items = args
            return "OK", self._fetch(int(str(uid)), str(items))
        raise AssertionError(f"UID command the read path must not use: {command}")

    def _search(self, args: tuple) -> bytes:
        self.server.searches.append(args)
        criteria = args[-1]
        if criteria == "UNSEEN":
            unseen = [uid for uid, (_, flags) in sorted(self.server.mails.items()) if r"\Seen" not in flags]
            return " ".join(str(uid) for uid in unseen).encode()
        if criteria == "ALL":
            needle = ""
        elif args[0] == "CHARSET":
            assert self.literal is not None
            needle, self.literal = self.literal.decode("utf-8"), None
        else:
            quoted = re.search(r'SUBJECT "([^"]*)"', str(criteria))
            assert quoted is not None
            needle = quoted.group(1)
        hits =[uid for uid, (raw, _) in sorted(self.server.mails.items()) if needle in _searchable_headers(raw)]
        return " ".join(str(uid) for uid in hits).encode()

    def _fetch(self, uid: int, items: str) -> list:
        self.server.fetches.append(items)
        if uid not in self.server.mails:
            return [None]
        raw, flags = self.server.mails[uid]
        header = raw.partition(b"\n\n")[0] + b"\n\n"
        if items == "(BODY.PEEK[])":
            return [(f"1 (UID {uid} BODY[] {{{len(raw)}}}".encode(), raw), b")"]
        with_flags = (f"1 (UID {uid} FLAGS ({flags}) BODY[HEADER] {{{len(header)}}}".encode(), header)
        if items == "(FLAGS BODY.PEEK[HEADER])":
            return [with_flags, b")"]
        raise AssertionError(f"FETCH form the read path must not use: {items}")


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> FakeServer:
    """Empty credentials and a fake server. setenv, not delenv: apply_credentials
    writes to os.environ, and only a set key is restored afterwards."""
    for key in (*OWN_ACCOUNT, *READONLY_ACCOUNT, *OTHER_KEYS):
        monkeypatch.setenv(key, "")
    fake = FakeServer()
    monkeypatch.setattr(client.imaplib, "IMAP4_SSL", fake.connect)
    return fake


def _configure(monkeypatch: pytest.MonkeyPatch, *accounts: dict[str, str]) -> None:
    for account in accounts:
        for key, value in account.items():
            monkeypatch.setenv(key, value)


def _tools() -> dict:
    """What the channel offers, the way collect_plugin_tools asks for it."""
    context = PluginContext(agent_id="test", lang="de", session_id="session")
    return {tool.name: tool for tool in EmailChannel().get_tools(context)}


def _run(tool, **args: str) -> str:
    answer: str = asyncio.run(tool.executor(**args))
    return answer


READ_CALLS = [
    {"action": "check"},
    {"action": "check", "unread": True},
    {"action": "read", "msg_id": "7"},
    {"action": "read", "msg_id": "5,7"},
    {"action": "search", "query": "Rechnung"},
    {"action": "search", "query": "Müller"},
]


class TestStrictlyReadOnly:
    @pytest.mark.parametrize("call", READ_CALLS)
    def test_opens_read_only(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch, call: dict) -> None:
        _configure(monkeypatch, OWN_ACCOUNT, READONLY_ACCOUNT)
        _run(_tools()["email_readonly"], **call)
        assert server.selects == [("INBOX", True)]

    @pytest.mark.parametrize("call", READ_CALLS)
    def test_fetches_only_with_peek(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch, call: dict) -> None:
        _configure(monkeypatch, OWN_ACCOUNT, READONLY_ACCOUNT)
        _run(_tools()["email_readonly"], **call)
        assert server.fetches
        for items in server.fetches:
            assert "BODY.PEEK[" in items
            assert "RFC822" not in items
            assert "BODY[" not in items

    def test_every_action_ends_without_a_writing_command(
        self, server: FakeServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FakeImap raises on anything else, so getting through is the proof."""
        _configure(monkeypatch, OWN_ACCOUNT, READONLY_ACCOUNT)
        tool = _tools()["email_readonly"]
        for call in READ_CALLS:
            assert "untrusted_data" in _run(tool, **call)

    @pytest.mark.parametrize("command", ["store", "copy", "append", "expunge", "create", "delete"])
    def test_fake_really_fails_on_writing_commands(self, server: FakeServer, command: str) -> None:
        with pytest.raises(AssertionError):
            getattr(server.connect("host", 993), command)

    @pytest.mark.parametrize("command", ["STORE", "COPY", "MOVE", "EXPUNGE"])
    def test_fake_really_fails_on_writing_uid_commands(self, server: FakeServer, command: str) -> None:
        with pytest.raises(AssertionError):
            server.connect("host", 993).uid(command, "5", "+FLAGS", r"\Deleted")

    def test_read_functions_cannot_reach_the_connection(self) -> None:
        """Their whole IMAP surface: no writing method, no public way to the connection."""
        surface = {name for name in vars(client._ReadOnlyMailbox) if not name.startswith("_")}
        assert surface == {"search", "search_header", "fetch_headers", "fetch_message"}

    def test_unopenable_folder_stops_before_any_fetch(
        self, server: FakeServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(monkeypatch, READONLY_ACCOUNT)
        with pytest.raises(ValueError, match="Gibtsnicht"):
            client.check_inbox(client.readonly_mailbox_account(), folder="Gibtsnicht")
        assert server.searches == []
        assert server.fetches == []

    def test_folder_argument_is_passed_on_read_only(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure(monkeypatch, OWN_ACCOUNT, READONLY_ACCOUNT)
        _run(_tools()["email_readonly"], action="check", folder="Archiv")
        assert server.selects == [("Archiv", True)]


class TestToolOffer:
    def test_offered_with_credentials(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure(monkeypatch, OWN_ACCOUNT, READONLY_ACCOUNT)
        assert set(_tools()) == {"email", "email_manage", "email_readonly"}

    def test_no_credentials_no_tool(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure(monkeypatch, OWN_ACCOUNT)
        assert set(_tools()) == {"email", "email_manage"}

    @pytest.mark.parametrize(
        "missing", ["EMAIL_READONLY_IMAP_HOST", "EMAIL_READONLY_USER", "EMAIL_READONLY_PASSWORD"]
    )
    def test_one_missing_field_no_tool(
        self, server: FakeServer, monkeypatch: pytest.MonkeyPatch, missing: str
    ) -> None:
        _configure(monkeypatch, OWN_ACCOUNT, READONLY_ACCOUNT)
        monkeypatch.setenv(missing, "")
        assert "email_readonly" not in _tools()

    def test_carries_write_data(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure(monkeypatch, OWN_ACCOUNT, READONLY_ACCOUNT)
        assert _tools()["email_readonly"].tier == TIER_WRITE_DATA

    def test_offers_only_reading_actions_and_parameters(
        self, server: FakeServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(monkeypatch, OWN_ACCOUNT, READONLY_ACCOUNT)
        schema = _tools()["email_readonly"].parameters
        assert schema["properties"]["action"]["enum"] == ["check", "read", "search"]
        assert set(schema["properties"]) == {"action", "msg_id", "query", "n", "unread", "folder"}

    @pytest.mark.parametrize("lang", ["de", "en"])
    @pytest.mark.parametrize("action", ["send", "delete", "move", "mark", "list_folders", "create_folder"])
    def test_any_other_action_is_refused_before_connecting(
        self, server: FakeServer, monkeypatch: pytest.MonkeyPatch, action: str, lang: str
    ) -> None:
        _configure(monkeypatch, READONLY_ACCOUNT)
        tool = get_readonly_mailbox_tools(lang=lang)[0]
        answer = _run(tool, action=action, msg_id="5", to="fremd@example.org", subject="x", body="y")
        assert answer.startswith("Fehler" if lang == "de" else "Error")
        assert "check, read, search" in answer
        assert server.connections == []

    @pytest.mark.parametrize(("arguments", "missing"), [({"action": "read"}, "msg_id"), ({"action": "search"}, "query")])
    def test_missing_argument_is_reported(
        self, server: FakeServer, monkeypatch: pytest.MonkeyPatch, arguments: dict, missing: str
    ) -> None:
        _configure(monkeypatch, READONLY_ACCOUNT)
        assert missing in _run(get_readonly_mailbox_tools()[0], **arguments)
        assert server.connections == []


class TestNeverTheOwnMailbox:
    @pytest.mark.parametrize("call", READ_CALLS)
    def test_logs_in_with_its_own_credentials(
        self, server: FakeServer, monkeypatch: pytest.MonkeyPatch, call: dict
    ) -> None:
        _configure(monkeypatch, OWN_ACCOUNT, READONLY_ACCOUNT)
        _run(_tools()["email_readonly"], **call)
        assert [(c.host, c.user, c.password) for c in server.connections] == [
            (READONLY_HOST, READONLY_USER, READONLY_PASSWORD)
        ]

    def test_works_without_any_own_credentials(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure(monkeypatch, READONLY_ACCOUNT)
        _run(get_readonly_mailbox_tools()[0], action="check")
        assert server.connections[0].user == READONLY_USER

    @pytest.mark.parametrize(
        ("missing", "stand_in"),
        [
            ("EMAIL_READONLY_IMAP_HOST", OWN_HOST),
            ("EMAIL_READONLY_USER", OWN_USER),
            ("EMAIL_READONLY_PASSWORD", OWN_PASSWORD),
        ],
    )
    def test_own_field_never_fills_a_gap(
        self, server: FakeServer, monkeypatch: pytest.MonkeyPatch, missing: str, stand_in: str
    ) -> None:
        _configure(monkeypatch, OWN_ACCOUNT, READONLY_ACCOUNT)
        monkeypatch.setenv(missing, "")
        assert not client.readonly_mailbox_configured()
        assert stand_in not in repr(client.readonly_mailbox_account())
        assert client.readonly_mailbox_account().password != OWN_PASSWORD

    def test_read_functions_have_no_default_account(self) -> None:
        for function in (client.check_inbox, client.read_emails, client.search_emails):
            parameter = inspect.signature(function).parameters["account"]
            assert parameter.default is inspect.Parameter.empty

    @pytest.mark.parametrize(
        "writer",
        [client.send_email, client.delete_email, client.move_email, client.mark_email,
         client.create_folder, client.list_folders, client._imap_connect],
    )
    def test_writing_functions_take_no_account(self, writer) -> None:
        assert "account" not in inspect.signature(writer).parameters

    def test_writing_path_logs_in_to_the_own_mailbox_only(
        self, server: FakeServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(monkeypatch, OWN_ACCOUNT, READONLY_ACCOUNT)
        client._imap_connect()
        assert server.connections[0].user == OWN_USER

    def test_credentials_alone_start_no_listener(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        """is_configured gates the listener, and only AIfred's own account counts."""
        _configure(monkeypatch, READONLY_ACCOUNT)
        monkeypatch.setenv("EMAIL_ENABLED", "true")
        assert not EmailChannel().is_configured()

    def test_password_stays_out_of_repr(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure(monkeypatch, READONLY_ACCOUNT)
        assert READONLY_PASSWORD not in repr(client.readonly_mailbox_account())


class TestOutput:
    def test_check_shows_mails_newest_first_with_read_state(
        self, server: FakeServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(monkeypatch, READONLY_ACCOUNT)
        answer = _run(get_readonly_mailbox_tools()[0], action="check")
        assert answer.index("[7]") < answer.index("[5]")
        assert "📩 [7]" in answer
        assert "📧 [5]" in answer

    def test_list_shows_no_content(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure(monkeypatch, READONLY_ACCOUNT)
        answer = _run(get_readonly_mailbox_tools()[0], action="check")
        assert "Treffen bei Müller" in answer and "Anna" in answer
        assert "Sehen wir uns" not in answer

    def test_list_says_how_many_of_how_many(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure(monkeypatch, READONLY_ACCOUNT)
        server.mails = {uid: (_mail(f"Mail {uid}", "A <a@b.test>", "x"), "") for uid in range(1, 31)}
        answer = _run(get_readonly_mailbox_tools()[0], action="check", n="3")
        assert "3 of 30" in answer and "[30]" in answer and "[27]" not in answer

    def test_unread_lists_only_the_unread_ones(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure(monkeypatch, READONLY_ACCOUNT)
        answer = _run(get_readonly_mailbox_tools()[0], action="check", unread=True)
        assert "[7]" in answer and "[5]" not in answer

    def test_unread_lists_all_of_them(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure(monkeypatch, READONLY_ACCOUNT)
        server.mails = {uid: (_mail(f"Mail {uid}", "A <a@b.test>", "x"), "") for uid in range(1, 61)}
        answer = _run(get_readonly_mailbox_tools()[0], action="check", unread=True)
        assert answer.count("📩") == 60

    def test_nothing_unread_is_said_plainly(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure(monkeypatch, READONLY_ACCOUNT)
        server.mails = {5: server.mails[5]}
        answer = _run(get_readonly_mailbox_tools()[0], action="check", unread=True)
        assert "[5]" not in answer and "No unread" in answer

    def test_search_shows_the_read_state_too(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure(monkeypatch, READONLY_ACCOUNT)
        answer = _run(get_readonly_mailbox_tools()[0], action="search", query="Rechnung")
        assert "📧 [5]" in answer

    def test_read_returns_the_whole_mail(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure(monkeypatch, READONLY_ACCOUNT)
        answer = _run(get_readonly_mailbox_tools()[0], action="read", msg_id="5")
        assert "Subject: Rechnung Oktober" in answer
        assert "Bitte bis Freitag zahlen." in answer

    def test_several_mails_in_one_call_and_one_connection(
        self, server: FakeServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(monkeypatch, READONLY_ACCOUNT)
        answer = _run(get_readonly_mailbox_tools()[0], action="read", msg_id="7, 5")
        assert answer.index("Sehen wir uns am Samstag?") < answer.index("Bitte bis Freitag zahlen.")
        assert len(server.connections) == 1

    def test_several_with_an_unknown_id_reports_it(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure(monkeypatch, READONLY_ACCOUNT)
        answer = _run(get_readonly_mailbox_tools()[0], action="read", msg_id="5,99")
        assert "Bitte bis Freitag zahlen." in answer and "99" in answer

    def test_long_mail_is_not_cut(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure(monkeypatch, READONLY_ACCOUNT)
        server.mails[9] = (_mail("Lang", "Anna <anna@freunde.test>", "Absatz eins.\n" * 5000 + "Das Ende."), "")
        answer = _run(get_readonly_mailbox_tools()[0], action="read", msg_id="9")
        assert "Das Ende." in answer

    def test_what_exceeds_the_budget_is_named_for_the_next_step(
        self, server: FakeServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(monkeypatch, READONLY_ACCOUNT)
        server.mails = {uid: (_mail(f"Mail {uid}", "A <a@b.test>", f"Text {uid}. " * 200), "") for uid in (1, 2, 3)}
        token = budget_var.set(500)
        try:
            answer = _run(get_readonly_mailbox_tools()[0], action="read", msg_id="1,2,3")
        finally:
            budget_var.reset(token)
        assert "Text 1." in answer and "Text 3." not in answer
        rest = answer.rsplit("</untrusted_data>", 1)[1]
        assert "3" in rest and "1" not in rest

    def test_first_mail_comes_even_above_the_budget(
        self, server: FakeServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(monkeypatch, READONLY_ACCOUNT)
        server.mails = {1: (_mail("Gross", "A <a@b.test>", "Wort " * 2000), "")}
        token = budget_var.set(100)
        try:
            answer = _run(get_readonly_mailbox_tools()[0], action="read", msg_id="1")
        finally:
            budget_var.reset(token)
        assert "Subject: Gross" in answer

    def test_without_a_budget_everything_comes(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure(monkeypatch, READONLY_ACCOUNT)
        server.mails = {uid: (_mail(f"Mail {uid}", "A <a@b.test>", f"Text {uid}. " * 500), "") for uid in (1, 2, 3)}
        answer = _run(get_readonly_mailbox_tools()[0], action="read", msg_id="1,2,3")
        assert all(f"Text {uid}." in answer for uid in (1, 2, 3))

    @pytest.mark.parametrize("mail_id,build", [
        (21, lambda html: _html_mail("Nur HTML", html)),
        (22, lambda html: _both_parts_mail("Leerer Textteil", "", html)),
    ])
    def test_html_becomes_readable_text(self, server, monkeypatch, mail_id: int, build) -> None:
        _configure(monkeypatch, READONLY_ACCOUNT)
        html = ("<html><head><title>Kopf</title><style>p {color: red}</style></head><body>"
                "<p>Erster&nbsp;Satz.</p><div>Zweiter Satz.</div><script>alert(1)</script>"
                "<table><tr><td>Kosten</td><td>12 Euro</td></tr></table></body></html>")
        server.mails[mail_id] = (build(html), "")
        answer = _run(get_readonly_mailbox_tools()[0], action="read", msg_id=str(mail_id))
        assert "Erster Satz." in answer and "Zweiter Satz." in answer and "12 Euro" in answer
        assert "<p>" not in answer and "color: red" not in answer and "alert(1)" not in answer

    def test_short_html_keeps_its_text(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        """An article extractor drops a short mail altogether (real mail, 31.10.2026)."""
        _configure(monkeypatch, READONLY_ACCOUNT)
        server.mails[23] = (_html_mail("Kurz", "<p>Termin um neun.</p>"), "")
        assert "Termin um neun." in _run(get_readonly_mailbox_tools()[0], action="read", msg_id="23")

    def test_html_with_an_encoding_declaration(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure(monkeypatch, READONLY_ACCOUNT)
        html = '<?xml version="1.0" encoding="utf-8"?><html><body><p>Grüße aus Köln</p></body></html>'
        server.mails[24] = (_html_mail("XHTML", html), "")
        assert "Grüße aus Köln" in _run(get_readonly_mailbox_tools()[0], action="read", msg_id="24")

    def test_empty_html_is_no_error(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure(monkeypatch, READONLY_ACCOUNT)
        server.mails[25] = (_html_mail("Leer", "   "), "")
        assert "Subject: Leer" in _run(get_readonly_mailbox_tools()[0], action="read", msg_id="25")

    def test_text_part_goes_before_html(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure(monkeypatch, READONLY_ACCOUNT)
        server.mails[26] = (_both_parts_mail("Beides", "Klartext.", "<p>Aus dem HTML.</p>"), "")
        answer = _run(get_readonly_mailbox_tools()[0], action="read", msg_id="26")
        assert "Klartext." in answer and "Aus dem HTML." not in answer

    def test_non_ascii_search_uses_literals(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure(monkeypatch, READONLY_ACCOUNT)
        answer = _run(get_readonly_mailbox_tools()[0], action="search", query="Müller")
        assert "[7]" in answer
        assert server.searches == [("CHARSET", "UTF-8", "SUBJECT"), ("CHARSET", "UTF-8", "FROM")]

    @pytest.mark.parametrize("call", READ_CALLS)
    def test_content_is_fenced_as_external(
        self, server: FakeServer, monkeypatch: pytest.MonkeyPatch, call: dict
    ) -> None:
        _configure(monkeypatch, READONLY_ACCOUNT)
        answer = _run(get_readonly_mailbox_tools()[0], **call)
        assert answer.startswith('<untrusted_data source="email" trust="external">')
        assert answer.rstrip().endswith("</untrusted_data>")

    def test_a_mail_from_the_owner_stays_external(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        """A provider-stamped pass earns "owner" on AIfred's mailbox, never on this one."""
        _configure(monkeypatch, OWN_ACCOUNT, READONLY_ACCOUNT)
        monkeypatch.setenv("EMAIL_ALLOWED_SENDERS", OWNER_ADDRESS)
        server.mails[9] = (_mail("Von mir", OWNER_ADDRESS, "Text", "mx.test; dmarc=pass"), "")
        tools = _tools()
        assert 'trust="external"' in _run(tools["email_readonly"], action="read", msg_id="9")
        assert 'trust="owner"' in _run(tools["email"], action="read", msg_id="9")


class TestOwnMailboxPath:
    """The ``email`` tool shares the read code: same mailbox, same tiers as before."""

    @pytest.mark.parametrize("call", READ_CALLS)
    def test_email_tool_reads_the_own_mailbox(
        self, server: FakeServer, monkeypatch: pytest.MonkeyPatch, call: dict
    ) -> None:
        _configure(monkeypatch, OWN_ACCOUNT, READONLY_ACCOUNT)
        _run(_tools()["email"], **call)
        assert [(c.host, c.user) for c in server.connections] == [(OWN_HOST, OWN_USER)]
        assert server.selects == [("INBOX", True)]

    def test_tiers_are_unchanged(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure(monkeypatch, OWN_ACCOUNT)
        tools = _tools()
        assert tools["email"].tier == TIER_COMMUNICATE
        assert tools["email_manage"].tier == TIER_WRITE_SYSTEM

    def test_empty_folder_gives_no_mails(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure(monkeypatch, OWN_ACCOUNT)
        server.mails.clear()
        assert "0 messages" in _run(_tools()["email"], action="check")


class TestCredentialFields:
    def _fields(self) -> dict:
        return {field.env_key: field for field in EmailChannel().credential_fields}

    def test_secrets_go_to_the_env_file(self) -> None:
        fields = self._fields()
        assert fields["EMAIL_READONLY_PASSWORD"].is_password
        assert fields["EMAIL_READONLY_PASSWORD"].is_secret
        assert fields["EMAIL_READONLY_USER"].is_secret

    def test_defaults_are_mail_de_and_nothing_personal(self) -> None:
        fields = self._fields()
        assert fields["EMAIL_READONLY_IMAP_HOST"].default == "imap.mail.de"
        assert fields["EMAIL_READONLY_IMAP_PORT"].default == "993"
        assert fields["EMAIL_READONLY_USER"].default == ""
        assert fields["EMAIL_READONLY_PASSWORD"].default == ""

    def test_labels_exist_in_both_languages(self) -> None:
        i18n = json.loads((Path(email_channel.__file__).parent / "i18n.json").read_text(encoding="utf-8"))
        for env_key in READONLY_ACCOUNT:
            label = i18n[self._fields()[env_key].label_key]
            assert label["de"] and label["en"]

    def test_applying_keeps_the_two_accounts_apart(self, server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
        _configure(monkeypatch, OWN_ACCOUNT)
        EmailChannel().apply_credentials({**OWN_ACCOUNT, **READONLY_ACCOUNT})
        assert client.readonly_mailbox_account().user == READONLY_USER
        assert client.agent_mailbox_account().user == OWN_USER
