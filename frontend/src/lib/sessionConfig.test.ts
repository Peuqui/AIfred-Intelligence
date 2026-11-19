// The optimistic view of a config change mirrors the server's per-mode rule (lib/mode_lineups.apply_config_changes):
// the places are those of the current mode, and a mode change shows the new mode's line-up only when the server answers.
import { expect, test } from "vitest";
import type { SessionConfig } from "../api/types";
import { withConfigChanges } from "./sessionConfig";

const recensio: SessionConfig = {
  active_agent: "codine",
  multi_agent_mode: "recensio",
  symposion_agents: [],
  incognito: false,
  interjections: true,
  debate_seats: { critic: "hal" },
};

test("a place chosen in the current mode changes that mode's view only", () => {
  const changed = withConfigChanges(recensio, { debate_seats: { critic: "sokrates" } });
  expect(changed.debate_seats).toEqual({ critic: "sokrates" });
  expect(changed.active_agent).toBe("codine");
  expect(changed.multi_agent_mode).toBe("recensio");
});

test("a mode change shows no place of the old mode, only what the same update names", () => {
  const switched = withConfigChanges(recensio, { multi_agent_mode: "tribunal" });
  expect(switched).toMatchObject({ multi_agent_mode: "tribunal", active_agent: null, symposion_agents: [], debate_seats: {} });
  const named = withConfigChanges(recensio, { multi_agent_mode: "tribunal", debate_seats: { judge: "salomo" } });
  expect(named.debate_seats).toEqual({ judge: "salomo" });
  expect(named.active_agent).toBeNull();
});

test("the other fields survive a mode change", () => {
  expect(withConfigChanges({ ...recensio, incognito: true }, { multi_agent_mode: "standard" })).toMatchObject({ incognito: true, interjections: true });
});
