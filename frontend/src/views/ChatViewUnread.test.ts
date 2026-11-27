// The unread marks in the chat: the line above the first unread answer, the click on a bubble below it, "mark all read".
import { flushPromises, shallowMount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, test, vi } from "vitest";

// jsdom has no matchMedia; the device preferences read it at import.
window.matchMedia = ((query: string) => ({ matches: false, media: query, addEventListener: () => undefined, removeEventListener: () => undefined })) as unknown as typeof window.matchMedia;

vi.mock("../i18n", () => ({
  t: (key: string) => key,
  tCount: (key: string, count: number) => `${key} ${count}`,
}));
const posted: string[] = [];
vi.mock("../api/client", () => ({
  ApiError: class extends Error {},
  api: (path: string, init?: { body?: string }) => {
    if (path.endsWith("/read")) posted.push(String(init?.body));
    return Promise.resolve(path === "agents/colors" ? {} : []);
  },
}));
vi.mock("../api/events", () => ({ RESOURCES: [], followSession: () => ({ close() {} }) }));
vi.stubGlobal("ResizeObserver", class { observe(): void {} disconnect(): void {} });

const { default: ChatView } = await import("./ChatView.vue");
const { useChat } = await import("../stores/chat");

const entry = (role: "user" | "assistant", timestamp: string, content: string) => ({
  role, timestamp, content, agent: "aifred", agent_display_name: "AIfred", agent_emoji: "", mode: "", time_display: "", has_audio: false, tts_note: "",
});
const history = [entry("user", "t0", "Frage eins"), entry("assistant", "t1", "Antwort eins"), entry("user", "t2", "Frage zwei"), entry("assistant", "t3", "Antwort zwei"), entry("assistant", "t4", "Antwort drei")];

beforeEach(() => {
  setActivePinia(createPinia());
  posted.length = 0;
});

async function shown(readUntil: string) {
  const chat = useChat();
  chat.sessionId = "s1";
  chat.history = history as never;
  chat.readUntil = readUntil;
  const wrapper = shallowMount(ChatView, { global: { stubs: { MessageBubble: false, ReadMark: false } } });
  await flushPromises();
  return { chat, wrapper };
}

// The bubbles and lines of the list in the order they stand, as text; a line the mark has passed (faded) as "(---)".
const sequence = (wrapper: Awaited<ReturnType<typeof shown>>["wrapper"]): string[] =>
  [...wrapper.element.querySelectorAll(".bubbles article, .bubbles .read-mark")].map((node) => {
    if (node.classList.contains("read-mark")) return node.classList.contains("passed") ? "(---)" : "---";
    return node.querySelector(".content")?.textContent?.trim() ?? "";
  });

test("the line stands directly above the first unread answer, below its question", async () => {
  const { wrapper } = await shown("t1");
  expect(sequence(wrapper)).toEqual(["Frage eins", "Antwort eins", "Frage zwei", "---", "Antwort zwei", "Antwort drei"]);
});

test("nothing read yet: the line stands above the first answer", async () => {
  const { wrapper } = await shown("");
  expect(sequence(wrapper).slice(0, 2)).toEqual(["Frage eins", "---"]);
  expect(sequence(wrapper).filter((item) => item === "---")).toHaveLength(1);
});

test("nothing unread: no line", async () => {
  const { wrapper } = await shown("t4");
  expect(sequence(wrapper)).not.toContain("---");
});

test("a click on a bubble below the line moves the mark to it; the line fades where it stands; a click above changes nothing", async () => {
  const { chat, wrapper } = await shown("t1");
  await wrapper.findAll("article")[3]!.trigger("click"); // "Antwort zwei"
  expect(chat.readUntil).toBe("t3");
  await flushPromises();
  // It does not move down above "Antwort drei": the line going away above and coming back below made everything jump.
  expect(sequence(wrapper)).toEqual(["Frage eins", "Antwort eins", "Frage zwei", "(---)", "Antwort zwei", "Antwort drei"]);
  await wrapper.findAll("article")[1]!.trigger("click"); // "Antwort eins", above the line
  expect(chat.readUntil).toBe("t3");
  expect(posted).toEqual([JSON.stringify({ timestamp: "t3" })]);
});

test("a click on a part of a bubble with a click of its own (link, button, the head of a collapsible) moves the mark too", async () => {
  for (const html of ['<a href="#">l</a>', "<button>b</button>", "<details><summary><b>Denken</b></summary></details>"]) {
    setActivePinia(createPinia());
    const { chat, wrapper } = await shown("t1");
    const answer = wrapper.findAll("article")[3]!;
    answer.element.insertAdjacentHTML("beforeend", html);
    await answer.find("a, button, summary b").trigger("click");
    expect(chat.readUntil, html).toBe("t3");
    wrapper.unmount();
  }
});

test("with nothing unread on opening, the line comes above the first answer that comes in; a faded line stays and none comes", async () => {
  const { chat, wrapper } = await shown("t4");
  chat.history = [...history, entry("assistant", "t5", "Antwort vier")] as never;
  await flushPromises();
  expect(sequence(wrapper).slice(-2)).toEqual(["---", "Antwort vier"]);
  chat.readUntil = "t5";
  await flushPromises();
  chat.history = [...chat.history, entry("assistant", "t6", "Antwort fünf")] as never;
  await flushPromises();
  expect(sequence(wrapper).slice(-3)).toEqual(["(---)", "Antwort vier", "Antwort fünf"]);
});

test("another chat opened: its line stands where its mark says, the faded one of the chat before is gone", async () => {
  const { chat, wrapper } = await shown("t1");
  chat.readUntil = "t4";
  await flushPromises();
  expect(sequence(wrapper)).toContain("(---)");
  chat.sessionId = "s2";
  chat.history = [entry("user", "u0", "Andere Frage"), entry("assistant", "u1", "Andere Antwort")] as never;
  chat.readUntil = "";
  await flushPromises();
  expect(sequence(wrapper)).toEqual(["Andere Frage", "---", "Andere Antwort"]);
});

test("mark all read shows the count, sets the newest bubble, and is grayed out without unread", async () => {
  const { chat, wrapper } = await shown("t1");
  const button = wrapper.find("button.all-read");
  expect(button.text()).toBe("mark_all_read 2");
  expect(button.attributes("disabled")).toBeUndefined();
  await button.trigger("click");
  expect(chat.readUntil).toBe("t4");
  await flushPromises();
  expect(wrapper.find("button.all-read").attributes("disabled")).toBeDefined();
  expect(wrapper.find("button.all-read").text()).toBe("mark_all_read 0");
});
