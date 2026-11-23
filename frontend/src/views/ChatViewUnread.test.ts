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

// The bubbles and lines of the list in the order they stand, as text.
const sequence = (wrapper: Awaited<ReturnType<typeof shown>>["wrapper"]): string[] =>
  [...wrapper.element.querySelectorAll(".bubbles article, .bubbles .read-mark")].map((node) =>
    node.classList.contains("read-mark") ? "---" : (node.querySelector(".content")?.textContent?.trim() ?? ""));

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

test("a click on a bubble below the line moves the mark to it, the line follows; a click above changes nothing", async () => {
  const { chat, wrapper } = await shown("t1");
  await wrapper.findAll("article")[3]!.trigger("click"); // "Antwort zwei"
  expect(chat.readUntil).toBe("t3");
  await flushPromises();
  expect(sequence(wrapper)).toEqual(["Frage eins", "Antwort eins", "Frage zwei", "Antwort zwei", "---", "Antwort drei"]);
  await wrapper.findAll("article")[1]!.trigger("click"); // "Antwort eins", above the line
  expect(chat.readUntil).toBe("t3");
  expect(posted).toEqual([JSON.stringify({ timestamp: "t3" })]);
});

test("a click on a link of a bubble does not move the mark", async () => {
  const { chat, wrapper } = await shown("t1");
  const link = document.createElement("a");
  link.href = "#";
  wrapper.findAll("article")[3]!.element.appendChild(link);
  await wrapper.findAll("article")[3]!.find("a").trigger("click");
  expect(chat.readUntil).toBe("t1");
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
