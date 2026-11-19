import type { SessionConfig, SessionConfigUpdate } from "../api/types";

/** The session's choices with ``changes`` applied as the server applies them (PATCH sessions/{id}/config). The places
 * (active_agent, symposion_agents, debate_seats) are those of the current mode, of the new one when the mode changes in
 * the same update (lib/mode_lineups.py). A field left out or null stays; the seats given replace only those seats.
 * What the new mode's line-up is only the server knows (the session's own, or the last one chosen anywhere): until its
 * answer the places the update does not name show as not chosen, never another mode's. */
export function withConfigChanges(config: SessionConfig, changes: SessionConfigUpdate): SessionConfig {
  const mode = changes.multi_agent_mode ?? config.multi_agent_mode;
  const kept = mode === config.multi_agent_mode ? config : { ...config, active_agent: null, symposion_agents: [], debate_seats: {} };
  return {
    active_agent: changes.active_agent ?? kept.active_agent,
    multi_agent_mode: mode,
    symposion_agents: changes.symposion_agents ?? kept.symposion_agents,
    incognito: changes.incognito ?? config.incognito,
    interjections: changes.interjections ?? config.interjections,
    debate_seats: { ...kept.debate_seats, ...changes.debate_seats },
  };
}
