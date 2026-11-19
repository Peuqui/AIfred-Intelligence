// A seat nobody was chosen for shows as not chosen (a disabled placeholder), never as another agent.
import { mount } from "@vue/test-utils";
import { expect, test, vi } from "vitest";
import type { AgentSummary, DebateMode, SessionConfig } from "../api/types";

vi.mock("../i18n", () => ({ t: (key: string) => key }));

const { default: DebateControls } = await import("./DebateControls.vue");

const agents = [{ id: "sokrates", display_name: "Sokrates", emoji: "🧐" }] as AgentSummary[];
const modes: DebateMode[] = [{ mode: "standard", seats: [] }, { mode: "tribunal", seats: ["critic", "judge"] }];
const tribunal: SessionConfig = {
  active_agent: null, multi_agent_mode: "tribunal", symposion_agents: [], incognito: false, interjections: true, debate_seats: { critic: "sokrates" },
};

test("the seat without an agent shows the placeholder, the chosen one its agent", () => {
  const selects = mount(DebateControls, { props: { config: tribunal, agents, modes } }).findAll("label.seat select");
  expect(selects).toHaveLength(2);
  expect((selects[0]?.element as HTMLSelectElement).value).toBe("sokrates");
  const judge = selects[1]?.element as HTMLSelectElement;
  expect(judge.value).toBe("");
  expect(judge.options[0]?.disabled).toBe(true);
  expect(judge.options[0]?.textContent).toBe("debate_seat_unchosen");
});
