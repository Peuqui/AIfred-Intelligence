// Quick clicks on the symposion's pills while the server is slow (09.11.2026: each click built on the server's last
// answer, so a second click before the first answer dropped the first agent again).
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, test, vi } from "vitest";
import type { SessionConfig } from "../api/types";

const requests: { path: string; body: unknown; answer: (config: SessionConfig) => void }[] = [];

vi.mock("../api/client", () => ({
  ApiError: class extends Error {},
  api: (path: string, init?: RequestInit) =>
    new Promise((resolve) => requests.push({ path, body: JSON.parse(String(init?.body)), answer: resolve })),
}));

const { useChat } = await import("./chat");

const start: SessionConfig = {
  active_agent: "aifred",
  multi_agent_mode: "symposion",
  symposion_agents: [],
  incognito: false,
  debate_seats: { critic: "sokrates", judge: "salomo" },
};

// What a pill does (AgentPills.choose): the list it shows, with this agent added.
function addGuest(chat: ReturnType<typeof useChat>, agent: string): Promise<void> {
  return chat.configure({ symposion_agents: [...chat.config.symposion_agents, agent] });
}

function request(index: number): (typeof requests)[number] {
  const sent = requests[index];
  if (!sent) throw new Error(`request ${index} was not sent`);
  return sent;
}

beforeEach(() => {
  setActivePinia(createPinia());
  requests.length = 0;
});

test("a second click before the first answer keeps the first agent, and the changes go out in order", async () => {
  const chat = useChat();
  chat.sessionId = "s1";
  chat.config = start;

  const first = addGuest(chat, "pater");
  const second = addGuest(chat, "rabbi");
  expect(chat.config.symposion_agents).toEqual(["pater", "rabbi"]);

  await vi.waitFor(() => expect(requests).toHaveLength(1));
  expect(request(0).body).toEqual({ symposion_agents: ["pater"] });
  request(0).answer({ ...start, symposion_agents: ["pater"] });
  await first;
  // The first answer does not take back what the second click shows.
  expect(chat.config.symposion_agents).toEqual(["pater", "rabbi"]);

  await vi.waitFor(() => expect(requests).toHaveLength(2));
  expect(request(1).body).toEqual({ symposion_agents: ["pater", "rabbi"] });
  request(1).answer({ ...start, symposion_agents: ["pater", "rabbi"] });
  await second;
  expect(chat.config.symposion_agents).toEqual(["pater", "rabbi"]);
});

test("a seat changed alone leaves the other seat", async () => {
  const chat = useChat();
  chat.sessionId = "s1";
  chat.config = start;
  void chat.configure({ debate_seats: { judge: "hal" } });
  expect(chat.config.debate_seats).toEqual({ critic: "sokrates", judge: "hal" });
});
