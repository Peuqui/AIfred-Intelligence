// The read mark of the open chat: taken from the server, moved forward only (at once, then reported), moved by the
// event of another tab, and by a reading heard to its end.
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, test, vi } from "vitest";

const detail = {
  session_id: "s1",
  title: "T",
  config: { active_agent: "aifred", multi_agent_mode: "standard", symposion_agents: [], incognito: false, debate_seats: {} },
  chat_history: [] as unknown[],
  debug_messages: [],
  running: false,
  speech_regenerating: false,
  live_events: [],
  last_event_id: 0,
  read_until: "",
};
const followed: ((kind: string, data: Record<string, unknown>) => void)[] = [];
const posted: { path: string; body: string }[] = [];
let refuse = false;

class FakeApiError extends Error {
  detail = "no_such_bubble";
}
vi.mock("../api/client", () => ({
  ApiError: FakeApiError,
  api: (path: string, init?: { body?: string }) => {
    if (path.endsWith("/read")) {
      posted.push({ path, body: String(init?.body) });
      return refuse ? Promise.reject(new FakeApiError()) : Promise.resolve(undefined);
    }
    return Promise.resolve(detail);
  },
}));
vi.mock("../api/events", () => ({
  RESOURCES: [],
  followSession: (_id: string, _afterId: number, handle: (kind: string, data: Record<string, unknown>) => void) => {
    followed.push(handle);
    return { close() {} };
  },
}));

class FakeAudio {
  static playing: FakeAudio[] = [];
  volume = 1;
  currentTime = 0;
  duration = 1;
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  ontimeupdate: (() => void) | null = null;
  ondurationchange: (() => void) | null = null;
  constructor(public src: string) {}
  play(): Promise<void> {
    FakeAudio.playing.push(this);
    return Promise.resolve();
  }
  pause(): void {}
  addEventListener(): void {} // the audio player of lib/media.ts listens on its own Audio
}
vi.stubGlobal("Audio", FakeAudio);

const speech = await import("../lib/speech");
const { useChat } = await import("./chat");

const answer = (timestamp: string) => ({ role: "assistant", timestamp, content: "x" });
const question = (timestamp: string) => ({ role: "user", timestamp, content: "q" });

beforeEach(() => {
  setActivePinia(createPinia());
  followed.length = 0;
  posted.length = 0;
  FakeAudio.playing = [];
  refuse = false;
  detail.read_until = "";
  detail.chat_history = [question("t0"), answer("t1"), question("t2"), answer("t3"), answer("t4")];
  speech.stop();
});

async function opened() {
  const chat = useChat();
  await chat.open("s1");
  return { chat, handle: followed[0]! };
}

async function settle(): Promise<void> {
  for (let round = 0; round < 5; round += 1) await Promise.resolve();
}

test("opening takes the mark and the unread count from the server's chat", async () => {
  detail.read_until = "t1";
  const { chat } = await opened();
  expect(chat.readUntil).toBe("t1");
  expect(chat.unread).toBe(2);
});

test("a mark moves forward at once and is reported; an older or equal one does nothing", async () => {
  const { chat } = await opened();
  await chat.markRead("t3");
  expect(chat.readUntil).toBe("t3");
  expect(posted).toEqual([{ path: "sessions/s1/read", body: JSON.stringify({ timestamp: "t3" }) }]);
  await chat.markRead("t1");
  await chat.markRead("t3");
  expect(chat.readUntil).toBe("t3");
  expect(posted).toHaveLength(1);
});

test("a refused mark is taken back and says why", async () => {
  const { chat } = await opened();
  refuse = true;
  await chat.markRead("t3");
  expect(chat.readUntil).toBe("");
  expect(chat.notice).toBe("no_such_bubble");
});

test("mark all read goes to the newest bubble of the chat", async () => {
  const { chat } = await opened();
  await chat.markAllRead();
  expect(chat.readUntil).toBe("t4");
  expect(chat.unread).toBe(0);
});

test("the event of another tab sets the mark, an older one does not take it back", async () => {
  const { chat, handle } = await opened();
  handle("read_until", { timestamp: "t3" });
  expect(chat.readUntil).toBe("t3");
  handle("read_until", { timestamp: "t1" });
  expect(chat.readUntil).toBe("t3");
});

test("a bubble read aloud by its button, heard to its end, is read", async () => {
  const { chat, handle } = await opened();
  await chat.readAgain("t3");
  handle("tts", { url: "/a/1.wav" });
  await settle();
  handle("tts_end", {});
  FakeAudio.playing[0]!.onended?.();
  await settle();
  expect(chat.readUntil).toBe("t3");
});

test("the speaker of a bubble, heard to its end, is read", async () => {
  const { chat } = await opened();
  speech.toggleReading(["/a/old.wav"], [], "t3");
  await settle();
  expect(chat.readUntil).toBe("");
  FakeAudio.playing[0]!.onended?.();
  await settle();
  expect(chat.readUntil).toBe("t3");
});

test("a stopped reading is not read, nor is a reading that nobody asked for with a button", async () => {
  const { chat, handle } = await opened();
  await chat.readAgain("t3");
  handle("tts", { url: "/a/1.wav" });
  await settle();
  handle("stop", {});
  FakeAudio.playing[0]!.onended?.();
  await settle();
  expect(chat.readUntil).toBe("");
  handle("tts", { url: "/a/2.wav" }); // the turn's own reading (autoplay): no bubble claimed it
  await settle();
  handle("tts_end", {});
  FakeAudio.playing[1]!.onended?.();
  await settle();
  expect(chat.readUntil).toBe("");
  expect(posted).toHaveLength(0);
});

test("a reading cut off by another one marks only the one heard to its end", async () => {
  const { chat } = await opened();
  speech.toggleReading(["/a/1.wav"], [], "t1");
  await settle();
  speech.toggleReading(["/a/3.wav"], [], "t3");
  await settle();
  FakeAudio.playing[0]!.onended?.();
  FakeAudio.playing[1]!.onended?.();
  await settle();
  expect(chat.readUntil).toBe("t3");
  expect(posted).toHaveLength(1);
});
