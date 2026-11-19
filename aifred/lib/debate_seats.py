"""The seats of the debate modes (critical_review, recensio, auto_consensus, tribunal): the author, the
critic and the judge. The author is the agent that answers the user first; it refines and defends its own
answer. Who sits where is the line-up of the mode (lib/mode_lineups.py), never a default: a seat nobody was
chosen for is empty. Any agent can take a seat and speaks there with its own persona; the seat brings the
task (``prompts/<lang>/debate/<key>.txt``, the same for every agent in it), which names the others by
their seats, never by a fixed name.

Peuqui 07.11.2026: critic and judge were tags on the agents (agents.json ``role``), grown
historically; an agent is just an agent, AIfred the base agent by its id.
"""

from __future__ import annotations

from typing import Any

from .user_error import Invalid

SEAT_MAIN = "main"       # the author
SEAT_CRITIC = "critic"
SEAT_JUDGE = "judge"
# The seats that speak after the author's direct answer, per debate mode (the author speaks in every one).
SPEAKING_SEATS: dict[str, tuple[str, ...]] = {
    "critical_review": (SEAT_CRITIC,),
    "recensio": (SEAT_MAIN, SEAT_CRITIC),
    "auto_consensus": (SEAT_MAIN, SEAT_CRITIC, SEAT_JUDGE),
    "tribunal": (SEAT_MAIN, SEAT_CRITIC, SEAT_JUDGE),
}
CHOSEN_SEATS = (SEAT_CRITIC, SEAT_JUDGE)


def chosen_seats(mode: str) -> tuple[str, ...]:
    """The seats of ``mode`` besides the author (critic, judge); standard and symposion have none."""
    return tuple(seat for seat in SPEAKING_SEATS.get(mode, ()) if seat != SEAT_MAIN)


def mode_places(mode: str) -> tuple[str, ...]:
    """The places of ``mode`` that have to be filled: the author and the chosen seats of a debate mode."""
    return (SEAT_MAIN, *chosen_seats(mode)) if mode in SPEAKING_SEATS else ()


class SeatError(Invalid):
    """A seat names no agent that can take it (deleted, or a system agent)."""


def check_seat_agent(seat: str, agent: str) -> None:
    """``agent`` can take ``seat``: it is selectable (exists, no system agent, not switched off)."""
    from .agent_config import agent_display_name, selectable_agents

    if seat not in CHOSEN_SEATS:
        raise SeatError("no_seat", seat=seat)
    if agent not in selectable_agents():
        if seat == SEAT_CRITIC:
            raise SeatError("critic_unfit", agent=agent_display_name(agent))
        raise SeatError("judge_unfit", agent=agent_display_name(agent))


def seat_names(seats: dict[str, str]) -> dict[str, str]:
    """The names the debate prompts use: ``{main_agent}``, ``{critic_agent}``, ``{judge_agent}``."""
    from .agent_config import agent_display_name

    return {f"{seat}_agent": agent_display_name(agent) for seat, agent in seats.items()}


def seat_prompt(key: str, seats: dict[str, str], lang: str, **variables: Any) -> str:
    """The task of a seat (``prompts/<lang>/debate/<key>.txt``) with the names of the seats filled in."""
    from .prompt_loader import load_prompt

    return load_prompt(f"debate/{key}", lang=lang, **seat_names(seats), **variables)


def round_rules(key: str, first: bool, seats: dict[str, str], lang: str, **variables: Any) -> str:
    """What a seat's task says about its round: ``debate/<key>_first`` for the first, ``<key>_later`` for
    every further one. The code picks the block; the model never weighs "IF round = 1" itself."""
    return seat_prompt(f"{key}_{'first' if first else 'later'}", seats, lang, **variables)
