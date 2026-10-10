"""Header of the user's bubble: "[Channel] Sender → Agent" (build_user_chat_content)."""

from datetime import datetime, timezone

import pytest

from aifred.lib import agent_config
from aifred.lib.envelope import InboundMessage
from aifred.lib.message_processor import build_user_chat_content


@pytest.fixture(autouse=True)
def _agents(monkeypatch: pytest.MonkeyPatch) -> None:
    known = {"codine": "🧑‍💻 Codine"}
    monkeypatch.setattr(agent_config, "get_agent_config", lambda agent_id: object() if agent_id in known else None)
    monkeypatch.setattr(agent_config, "get_agent_label", lambda agent_id: known[agent_id])


def _message(metadata: dict) -> InboundMessage:
    return InboundMessage(
        channel="freeecho2", channel_id="Buero-Puck-2", sender="Buero-Puck-2",
        text="Erkläre Quantenphysik.", timestamp=datetime.now(timezone.utc), metadata=metadata,
    )


def test_addressed_agent_follows_the_arrow() -> None:
    header = build_user_chat_content(_message({"wake_agent": "codine"})).split("\n")[0]
    assert header.endswith("Buero-Puck-2 → 🧑‍💻 Codine")


@pytest.mark.parametrize("wake_agent", [None, "", "unbekannt"])
def test_no_arrow_without_a_configured_addressee(wake_agent: str | None) -> None:
    header = build_user_chat_content(_message({"wake_agent": wake_agent})).split("\n")[0]
    assert header.endswith("] Buero-Puck-2")
