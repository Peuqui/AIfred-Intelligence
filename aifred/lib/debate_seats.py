"""The seats of the debate modes (critical_review, auto_consensus, tribunal): the author, the critic
and the judge. The author is the agent that answers the user first (the session's active or the
addressed agent); it refines and defends its own answer. Who sits as critic and judge a session
chooses (its config ``debate_seats``), by default as settings.json says (``debate_seats``: Sokrates
and Salomo). Any agent can take a seat and speaks there with its own persona; the seat brings the
task (``prompts/<lang>/debate/<key>.txt``, the same for every agent in it), which names the others by
their seats, never by a fixed name.

Peuqui 07.11.2026: critic and judge were tags on the agents (agents.json ``role``), grown
historically; an agent is just an agent, AIfred the base agent by its id.
"""

from __future__ import annotations

from typing import Any

SEAT_MAIN = "main"       # the author
SEAT_CRITIC = "critic"
SEAT_JUDGE = "judge"
# The seats a session chooses; the author is whoever answers first.
CHOSEN_SEATS = (SEAT_CRITIC, SEAT_JUDGE)

# The Reflex UI's debate (lib/multi_agent.py) speaks with fixed agents until the switch to the Vue app.
REFLEX_SEATS = {SEAT_MAIN: "aifred", SEAT_CRITIC: "sokrates", SEAT_JUDGE: "salomo"}


class SeatError(ValueError):
    """A seat names no agent that can take it (deleted, or a system agent)."""


def default_seats() -> dict[str, str]:
    """Critic and judge as settings.json says (``debate_seats``)."""
    from .settings import persisted_settings

    seats: dict[str, str] = dict(persisted_settings()["debate_seats"])
    return seats


def session_seats(session_config: dict[str, Any]) -> dict[str, str]:
    """Critic and judge of a session: its own choice over the default of the settings."""
    return {**default_seats(), **session_config["debate_seats"]}


def check_seat_agent(seat: str, agent: str) -> None:
    """``agent`` can take ``seat``: it is selectable (exists, no system agent, not switched off)."""
    from .agent_config import selectable_agents

    if seat not in CHOSEN_SEATS:
        raise SeatError(f"No seat '{seat}' to choose (one of {CHOSEN_SEATS})")
    if agent not in selectable_agents():
        raise SeatError(f"No agent '{agent}' can take the seat '{seat}' (unknown, a system agent or switched off)")


def seat_names(seats: dict[str, str]) -> dict[str, str]:
    """The names the debate prompts use: ``{main_agent}``, ``{critic_agent}``, ``{judge_agent}``."""
    from .agent_config import agent_display_name

    return {f"{seat}_agent": agent_display_name(agent) for seat, agent in seats.items()}


def seat_prompt(key: str, seats: dict[str, str], lang: str, **variables: Any) -> str:
    """The task of a seat (``prompts/<lang>/debate/<key>.txt``) with the names of the seats filled in."""
    from .prompt_loader import load_prompt

    return load_prompt(f"debate/{key}", lang=lang, **seat_names(seats), **variables)
