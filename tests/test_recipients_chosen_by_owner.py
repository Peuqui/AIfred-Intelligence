"""Who may receive what the agents send (Peuqui 22.11.2026).

Contract:
- A mail goes only to a recipient the owner chose: on the allowlist, in an address book a plugin offers
  (Nextcloud contacts), or written by the owner in this conversation (their message of this turn, a job's own
  text, or an earlier message of theirs). This holds in the browser too; before, the browser sent anywhere.
- An address that stands only in foreign text (a page, a mail, a tool result, the model's own words) does not
  count: that is how an injected instruction gets data out.
- A job's mail recipient and webhook follow the same rule when the job is created or changed.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from aifred.lib import address_books, plugin_registry
from aifred.lib.plugin_base import PluginContext
from aifred.lib.security import named_by_owner
from aifred.plugins.channels import email_channel
from aifred.plugins.channels.email_channel import client as email_client
from aifred.plugins.channels.email_channel import tools as email_tools
from aifred.plugins.tools.horarium import _not_chosen_by_owner

FOREIGN = "drop@attacker.example"
NAMED = "mueller@beispiel.de"
HISTORY = [
    {"role": "user", "content": f"Merk dir bitte {NAMED} für später."},
    {"role": "assistant", "content": f"Laut der Seite soll alles an {FOREIGN} gehen."},
    {"role": "tool", "tool_call_id": "c1", "content": f"Contact: {FOREIGN}"},
]


def _ctx(source: str = "browser", trust: str = "external", user_query: str = "Schreib ihm bitte.") -> PluginContext:
    return PluginContext(agent_id="aifred", lang="de", session_id="s", user_query=user_query, source=source,
                         trust=trust, llm_history=list(HISTORY))


@pytest.mark.parametrize("ctx", [_ctx(), _ctx(source="telegram", trust="owner"), _ctx(source="scheduler", trust="owner")])
def test_the_owner_names_an_address_in_their_own_messages(ctx: PluginContext) -> None:
    assert named_by_owner(NAMED, ctx)
    assert named_by_owner(NAMED.upper(), ctx)


def test_an_address_from_foreign_text_is_not_named_by_the_owner() -> None:
    assert not named_by_owner(FOREIGN, _ctx())


def test_the_message_of_this_turn_counts() -> None:
    assert named_by_owner("neu@beispiel.de", _ctx(user_query="Schick das an neu@beispiel.de"))


@pytest.mark.parametrize(("source", "trust"), [("ai_connect", "external"), ("telegram", "external"), ("webhook", "owner")])
def test_outside_an_owner_context_nobody_names_anything(source: str, trust: str) -> None:
    assert not named_by_owner(NAMED, _ctx(source=source, trust=trust))


def _send(monkeypatch: pytest.MonkeyPatch, to: str, ctx: PluginContext, contacts: frozenset[str] = frozenset()) -> str:
    sent: list[str] = []

    def send_email(**kwargs: Any) -> str:
        sent.append(kwargs["to"])
        return "sent"

    monkeypatch.setattr(email_channel, "_is_sender_allowed", lambda address: False)
    monkeypatch.setattr(address_books, "known_mail_address", lambda address: address in contacts)
    monkeypatch.setattr(email_client, "send_email", send_email)
    tool = email_tools.get_email_tools(source=ctx.source, trust=ctx.trust, ctx=ctx)[0]
    result = str(asyncio.run(tool.executor(action="send", to=to, subject="Betreff", body="Text")))
    assert (result == "sent") == (sent == [to]), (result, sent)
    return result


def test_the_browser_no_longer_mails_an_address_from_foreign_text(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _send(monkeypatch, FOREIGN, _ctx()).startswith("Error: refused")


@pytest.mark.parametrize("ctx", [_ctx(), _ctx(source="telegram", trust="owner")])
def test_a_named_address_is_mailed_from_every_owner_context(monkeypatch: pytest.MonkeyPatch, ctx: PluginContext) -> None:
    assert _send(monkeypatch, f"Herr Müller <{NAMED}>", ctx) == "sent"


def test_a_contact_is_mailed_without_being_named(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _send(monkeypatch, "kontakt@beispiel.de", _ctx(), contacts=frozenset({"kontakt@beispiel.de"})) == "sent"


def test_an_address_book_comes_from_the_plugins_themselves(monkeypatch: pytest.MonkeyPatch) -> None:
    book = SimpleNamespace(knows_mail_address=lambda address: address == "kontakt@beispiel.de")
    plugins: list[Any] = [SimpleNamespace(address_book=None, is_available=lambda: True),
                          SimpleNamespace(address_book=book, is_available=lambda: True)]
    monkeypatch.setattr(plugin_registry, "discover_tools", lambda: plugins)
    assert address_books.known_mail_address("kontakt@beispiel.de")
    assert not address_books.known_mail_address(FOREIGN)


def _job_error(monkeypatch: pytest.MonkeyPatch, recipient: str = "", webhook_url: str = "", **ctx: Any) -> str:
    monkeypatch.setattr(address_books, "known_mail_address", lambda address: False)
    return asyncio.run(_not_chosen_by_owner(recipient, webhook_url, _ctx(**ctx)))


def test_a_job_mails_only_a_chosen_recipient(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _job_error(monkeypatch, recipient=NAMED) == ""
    assert _job_error(monkeypatch, recipient="Peuqui") == ""          # a name the user mapping resolves
    assert FOREIGN in json.loads(_job_error(monkeypatch, recipient=FOREIGN))["error"]


def test_a_job_posts_only_to_a_webhook_the_owner_wrote(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://hooks.beispiel.de/abc"
    assert _job_error(monkeypatch, webhook_url=url, user_query=f"Schick das Ergebnis an {url}") == ""
    assert "webhook_url" in json.loads(_job_error(monkeypatch, webhook_url="https://attacker.example/x"))["error"]
