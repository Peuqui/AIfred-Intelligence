// A bubble's reading asked for with its button: the tab shows it as asked for until the first part of the reading
// arrives, so the user sees something happens and does not click again (21.11.2026: nothing showed until the sound).
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, test, vi } from "vitest";

const detail = {
  session_id: "s1",
  title: "T",
  config: { active_agent: "aifred", multi_agent_mode: "standard", symposion_agents: [], incognito: false, debate_seats: {} },
  chat_history: [],
  debug_messages: [],
  running: false,
  speech_regenerating: false,
  live_events: [],
  last_event_id: 0,
  read_until: "",
};
const followed: ((kind: string, data: Record<string, unknown>) => void)[] = [];
let refuse = false;

class FakeApiError extends Error {
  detail = "nothing_to_read";
}
vi.mock("../api/client", () => ({
  ApiError: FakeApiError,
  api: (path: string) => (refuse && path.endsWith("/messages/speech") ? Promise.reject(new FakeApiError()) : Promise.resolve(detail)),
}));
vi.mock("../api/events", () => ({
  RESOURCES: [],
  followSession: (_id: string, _afterId: number, handle: (kind: string, data: Record<string, unknown>) => void) => {
    followed.push(handle);
    return { close() {} };
  },
}));
// The playlist itself plays audio jsdom does not have; here only what the store hands it counts.
vi.mock("../lib/speech", async (original) => ({ ...(await original<typeof import("../lib/speech")>()), enqueue: vi.fn(), stop: vi.fn(), keep: vi.fn() }));

const { useChat } = await import("./chat");

beforeEach(() => {
  setActivePinia(createPinia());
  followed.length = 0;
  refuse = false;
});

async function opened() {
  const chat = useChat();
  await chat.open("s1");
  return { chat, handle: followed[0]! };
}

test("asked for at the click, still while the server stops what played before, over with the first part", async () => {
  const { chat, handle } = await opened();
  await chat.readAgain("t1");
  expect(chat.readingRequested).toBe("t1");
  handle("stop", {});
  expect(chat.readingRequested).toBe("t1");
  handle("tts", { url: "/audio/1.wav" });
  expect(chat.readingRequested).toBeNull();
});

test("a part the browser's own voice speaks ends it too", async () => {
  const { chat, handle } = await opened();
  await chat.readAgain("t1");
  handle("tts_speak", { utterance: { text: "Hallo" } });
  expect(chat.readingRequested).toBeNull();
});

test("a notice (the reading failed) ends it", async () => {
  const { chat, handle } = await opened();
  await chat.readAgain("t1");
  handle("notice", { text: "Sprachausgabe nicht möglich" });
  expect(chat.readingRequested).toBeNull();
  expect(chat.notice).toBe("Sprachausgabe nicht möglich");
});

test("a refused request ends it and says why", async () => {
  const { chat } = await opened();
  refuse = true;
  await chat.readAgain("t1");
  expect(chat.readingRequested).toBeNull();
  expect(chat.notice).toBe("nothing_to_read");
});

test("the stop button ends it", async () => {
  const { chat } = await opened();
  await chat.readAgain("t1");
  await chat.stop();
  expect(chat.readingRequested).toBeNull();
});
