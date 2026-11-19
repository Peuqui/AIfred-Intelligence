"""A session in a discussion mode with its places filled: the product has no default line-up, so a test that wants
a debate chooses one (through the one function that applies a config change, lib/mode_lineups.py)."""

from typing import Any

from aifred.lib.debate_seats import chosen_seats
from aifred.lib.mode_lineups import apply_config_changes

CRITIC = "sokrates"
JUDGE = "salomo"


def choose_mode(session_id: str, mode: str, **changes: Any) -> dict[str, Any]:
    """Switch ``session_id`` into ``mode``; the author is AIfred, critic Sokrates and judge Salomo, unless
    ``changes`` (``active_agent``, ``debate_seats``, ``symposion_agents``) say otherwise."""
    seats = {seat: CRITIC if seat == "critic" else JUDGE for seat in chosen_seats(mode)}
    if mode in ("standard", "symposion"):
        places: dict[str, Any] = {}
    else:
        places = {"active_agent": "aifred", "debate_seats": {**seats, **changes.pop("debate_seats", {})}}
    return apply_config_changes(session_id, {"multi_agent_mode": mode, **places, **changes})
