import type { SessionConfig, SessionConfigUpdate } from "../api/types";

/** The session's choices with ``changes`` applied as the server applies them (PATCH sessions/{id}/config): a field
 * left out or null stays, the seats given replace only those seats. */
export function withConfigChanges(config: SessionConfig, changes: SessionConfigUpdate): SessionConfig {
  return {
    active_agent: changes.active_agent ?? config.active_agent,
    multi_agent_mode: changes.multi_agent_mode ?? config.multi_agent_mode,
    symposion_agents: changes.symposion_agents ?? config.symposion_agents,
    incognito: changes.incognito ?? config.incognito,
    debate_seats: { ...config.debate_seats, ...changes.debate_seats },
  };
}
