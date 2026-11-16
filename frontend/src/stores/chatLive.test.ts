// A page that opens (or comes back to the front) while a turn runs shows the answer so far, not only what is sent from
// then on (16.11.2026: after a reload the running reply was gone until its end).
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, test, vi } from "vitest";

const detail = {
  session_id: "s1",
  title: "T",
  config: { active_agent: "aifred", multi_agent_mode: "standard", symposion_agents: [], incognito: false, debate_seats: {} },
  chat_history: [],
  debug_messages: [],
  running: true,
  speech_regenerating: false,
  live_events: [] as { kind: string; data: Record<string, unknown> }[],
  last_event_id: 7,
};
const followed: { afterId: number; handle: (kind: string, data: Record<string, unknown>) => void }[] = [];

vi.mock("../api/client", () => ({ ApiError: class extends Error {}, api: () => Promise.resolve(detail) }));
vi.mock("../api/events", () => ({
  RESOURCES: [],
  followSession: (_id: string, afterId: number, handle: (kind: string, data: Record<string, unknown>) => void) => {
    followed.push({ afterId, handle });
    return { close() {} };
  },
}));

const { useChat } = await import("./chat");

beforeEach(() => {
  setActivePinia(createPinia());
  followed.length = 0;
});

test("the answer so far is shown at once and the stream goes on from the position the server gave", async () => {
  detail.live_events = [
    { kind: "agent_started", data: { agent: "codine", display_name: "Codine", emoji: "🛠️" } },
    { kind: "token", data: { text: "Ein brauner Hund" } },
    { kind: "tool_status", data: { text: "Suche" } },
  ];
  const chat = useChat();
  await chat.open("s1");
  expect(chat.streaming).toEqual({ agent: "codine", displayName: "Codine", emoji: "🛠️", text: "Ein brauner Hund" });
  expect(chat.toolStatus).toBe("Suche");
  expect(followed[0]?.afterId).toBe(7);
  followed[0]?.handle("token", { text: " auf der Wiese" });
  expect(chat.streaming?.text).toBe("Ein brauner Hund auf der Wiese");
});

test("a session without a running turn shows no stream", async () => {
  detail.live_events = [];
  const chat = useChat();
  await chat.open("s1");
  expect(chat.streaming).toBeNull();
});
