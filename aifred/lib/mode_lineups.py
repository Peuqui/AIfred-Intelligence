"""Who sits where in each discussion mode: the line-up of a mode.

A debate mode (critical_review, recensio, auto_consensus, tribunal) has the author and the seats of
``debate_seats.SPEAKING_SEATS``; the symposion has its list of agents; the standard mode has the session's
``active_agent`` and nothing else. Every mode remembers its own line-up (``config["lineups"][mode]``), the
session's own one and, globally, the last one chosen (settings.json ``last_lineups``).

Last choice wins, no save button: any choice of a place in a mode is stored for that mode in the session and
globally. A session that enters a mode it has no line-up for takes the global one of that mode. There are no
defaults: a place nobody was chosen for is empty, and a turn that needs it is refused (``turn_debate.plan_turn``).

Two functions are the whole logic: ``apply_config_changes`` (every change of a session's config, whoever asks:
the API, the intent detection, the sticky address) and ``public_config`` (what the session's config means for
its current mode). Peuqui 19.11.2026: before, one pair of seats served all debate modes.
"""

from __future__ import annotations

import copy
from typing import Any

from .config import MULTI_AGENT_MODES
from .debate_seats import (
    SEAT_MAIN,
    SPEAKING_SEATS,
    SeatError,
    check_seat_agent,
    chosen_seats,
    mode_places,
)
from .i18n import t
from .prompt_loader import get_language
from .session_storage import get_session_config, session_rmw_lock, update_session_config
from .settings import persisted_settings, update_settings
from .user_error import Invalid, NotDone

MODE_STANDARD = "standard"
MODE_SYMPOSION = "symposion"
SYMPOSION_AGENTS = "agents"   # the place of the symposion's list in its line-up
LINEUP_MODES = (*SPEAKING_SEATS, MODE_SYMPOSION)


def mode_lineup(config: dict[str, Any], mode: str | None = None) -> dict[str, Any]:
    """The line-up of ``mode`` (default: the session's current one) in a session config: place → agent
    (``SYMPOSION_AGENTS`` → list of agents). A place never chosen is missing."""
    lineup: dict[str, Any] = config["lineups"].get(mode or config["multi_agent_mode"], {})
    return lineup


def current_author(config: dict[str, Any]) -> str | None:
    """Who answers first in the session's current mode when nobody is addressed: the active agent in the
    standard mode, the author of the line-up in a debate mode, nobody in the symposion (no author)."""
    mode = config["multi_agent_mode"]
    if mode == MODE_STANDARD:
        author: str = config["active_agent"]
        return author
    return mode_lineup(config).get(SEAT_MAIN)


def public_config(config: dict[str, Any]) -> dict[str, Any]:
    """The session's config as the API and the clients read it: ``active_agent`` the author of the current mode,
    ``debate_seats`` its critic and judge, ``symposion_agents`` the symposion's list; each empty when not chosen."""
    lineup = mode_lineup(config)
    return {
        "active_agent": current_author(config),
        "multi_agent_mode": config["multi_agent_mode"],
        "symposion_agents": list(lineup.get(SYMPOSION_AGENTS, [])),
        "incognito": config["incognito"],
        "interjections": config["interjections"],
        "debate_seats": {seat: lineup[seat] for seat in chosen_seats(config["multi_agent_mode"]) if seat in lineup},
    }


def incomplete(mode: str, places: list[str]) -> Invalid:
    """The refusal of a turn whose mode lacks ``places`` (what to choose, named in the UI language)."""
    names = ", ".join(t(_place_label_key(place), lang=get_language()) for place in places)
    return Invalid("lineup_incomplete", mode=t(f"multi_agent_{mode}", lang=get_language()), places=names)


def _place_label_key(place: str) -> str:
    return "symposion_agents" if place == SYMPOSION_AGENTS else f"debate_seat_{place}"


def apply_config_changes(session_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    """Apply ``changes`` (keys of ``public_config``) to the session; returns its config afterwards.

    The places apply to the mode the session is in once ``multi_agent_mode`` is applied: ``active_agent`` is the
    standard mode's agent there and the author of a debate mode otherwise, ``debate_seats`` change the seats of the
    debate mode, ``symposion_agents`` replace the symposion's list. A place chosen in a mode is stored in the session's
    line-up of it and as the global last choice of that mode. Entering a mode the session has no line-up for copies
    the global one. A change the mode has no place for is refused (422), nothing is stored then."""
    from .agent_config import get_agent_config

    mode = changes.get("multi_agent_mode")
    if mode is not None and mode not in MULTI_AGENT_MODES:
        raise Invalid("unknown_mode", value=mode)
    with session_rmw_lock:
        config = get_session_config(session_id)
        mode = mode or config["multi_agent_mode"]
        agents = [*([changes["active_agent"]] if "active_agent" in changes else []), *changes.get("symposion_agents", [])]
        unknown = [agent for agent in agents if get_agent_config(agent) is None]
        if unknown:
            raise Invalid("unknown_agent", agent=", ".join(unknown))
        places, standard_agent = _chosen_places(mode, changes)

        stored: dict[str, Any] = {key: changes[key] for key in ("multi_agent_mode", "incognito", "interjections") if key in changes}
        if standard_agent is not None:
            stored["active_agent"] = standard_agent
        lineups = copy.deepcopy(config["lineups"])
        last_chosen = persisted_settings()["last_lineups"]
        if mode in LINEUP_MODES and mode not in lineups and mode in last_chosen:
            lineups[mode] = copy.deepcopy(last_chosen[mode])
        if places:
            lineups[mode] = {**lineups.get(mode, {}), **places}
            if not update_settings({"last_lineups": {**last_chosen, mode: {**last_chosen.get(mode, {}), **places}}}):
                raise NotDone("settings_not_saved")
        stored["lineups"] = lineups
        update_session_config(session_id, **stored)
        return {**config, **stored}


def _chosen_places(mode: str, changes: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """The places ``changes`` choose in ``mode``, and the standard mode's agent when that is the mode (the one place
    that is no line-up). Refuses a place the mode does not have and an agent that cannot take the seat."""
    places: dict[str, Any] = {}
    standard_agent: str | None = None
    if "active_agent" in changes:
        if mode == MODE_STANDARD:
            standard_agent = changes["active_agent"]
        elif mode in SPEAKING_SEATS:
            places[SEAT_MAIN] = changes["active_agent"]
        else:
            raise _no_place(SEAT_MAIN, mode)
    for seat, agent in changes.get("debate_seats", {}).items():
        check_seat_agent(seat, agent)
        if seat not in mode_places(mode):
            raise _no_place(seat, mode)
        places[seat] = agent
    if "symposion_agents" in changes:
        if mode != MODE_SYMPOSION:
            raise _no_place(SYMPOSION_AGENTS, mode)
        places[SYMPOSION_AGENTS] = list(changes["symposion_agents"])
    return places, standard_agent


def _no_place(place: str, mode: str) -> SeatError:
    return SeatError("not_in_mode", place=t(_place_label_key(place), lang=get_language()), mode=t(f"multi_agent_{mode}", lang=get_language()))
