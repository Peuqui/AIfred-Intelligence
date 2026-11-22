// The scrolling of the chat's box: where a chat opens, what holds while pictures load, what moves when something comes in.
// jsdom has no layout: a stand-in lays the bubbles and the line out one under the other (VIEW high box, GAP between the
// items, a bubble BUBBLE high unless a test says otherwise) and answers scrollTop, scrollHeight and the rectangles from it.
import { flushPromises, shallowMount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { savePlace } from "../lib/chatPlace";

window.matchMedia = ((query: string) => ({ matches: false, media: query, addEventListener: () => undefined, removeEventListener: () => undefined })) as unknown as typeof window.matchMedia;

vi.mock("../i18n", () => ({
  t: (key: string) => key,
  tCount: (key: string, count: number) => `${key} ${count}`,
}));
const posted: string[] = [];
let refuseSend = false;
vi.mock("../api/client", () => ({
  ApiError: class extends Error {},
  api: (path: string, init?: { body?: string }) => {
    if (path.endsWith("/read")) posted.push(String(init?.body));
    if (refuseSend && path.endsWith("/messages")) return Promise.reject(new Error("refused"));
    return Promise.resolve(path === "agents/colors" ? {} : []);
  },
}));
vi.mock("../api/events", () => ({ RESOURCES: [], followSession: () => ({ close() {} }) }));

// The content growing (a picture or a film loaded): the callbacks of the observers that watch the list's content.
let contentGrew: (() => void)[] = [];
vi.stubGlobal("ResizeObserver", class {
  constructor(private readonly callback: () => void) {}
  observe(target: Element): void {
    if (target.classList.contains("bubbles")) contentGrew.push(this.callback);
  }
  disconnect(): void {}
});

const VIEW = 300;
const BUBBLE = 100;
const LINE = 20;
const GAP = 10;
const heights: Record<string, number> = {};

const itemsOf = (box: Element): HTMLElement[] => [...box.querySelectorAll<HTMLElement>("[data-entry], [data-read-mark]")];
const heightOf = (item: HTMLElement): number => (item.dataset.entry ? (heights[item.dataset.entry] ?? BUBBLE) : LINE);
const offsetOf = (item: HTMLElement): number => {
  let offset = 0;
  for (const other of itemsOf(item.closest(".messages")!)) {
    if (other === item) break;
    offset += heightOf(other) + GAP;
  }
  return offset;
};
const isBox = (element: Element): boolean => element.classList.contains("messages");
const contentHeight = (box: Element): number => itemsOf(box).reduce((sum, item) => sum + heightOf(item) + GAP, -GAP);
const scrolledTo = new WeakMap<Element, number>();
const rectangle = (top: number, height: number): DOMRect =>
  ({ top, bottom: top + height, height, left: 0, right: 0, width: 0, x: 0, y: top, toJSON: () => ({}) }) as DOMRect;

Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get(this: HTMLElement) { return isBox(this) ? VIEW : 0; } });
Object.defineProperty(HTMLElement.prototype, "scrollHeight", { configurable: true, get(this: HTMLElement) { return isBox(this) ? contentHeight(this) : 0; } });
Object.defineProperty(HTMLElement.prototype, "scrollTop", {
  configurable: true,
  get(this: HTMLElement) { return scrolledTo.get(this) ?? 0; },
  // A browser keeps the position within the box.
  set(this: HTMLElement, value: number) { scrolledTo.set(this, Math.max(0, Math.min(this.scrollHeight - this.clientHeight, value))); },
});
Element.prototype.getBoundingClientRect = function (this: Element): DOMRect {
  if (isBox(this)) return rectangle(0, VIEW);
  const box = this.closest<HTMLElement>(".messages");
  const item = this as HTMLElement;
  if (box && (item.dataset.entry || item.dataset.readMark !== undefined)) {
    return rectangle(offsetOf(item) - box.scrollTop, heightOf(item));
  }
  return rectangle(0, 0);
};

const { default: ChatView } = await import("./ChatView.vue");
const { useChat } = await import("../stores/chat");
const { useSettings } = await import("../stores/settings");

const entry = (role: "user" | "assistant", timestamp: string) => ({
  role, timestamp, content: timestamp, agent: "aifred", agent_display_name: "AIfred", agent_emoji: "", mode: "", time_display: "", has_audio: false, tts_note: "",
});
const stamp = (index: number): string => `t${String(index).padStart(2, "0")}`;
// Entries 0..11: the questions (user) at the even places, the answers at the odd ones: 6 turns.
const entries = (count: number, from = 0) => Array.from({ length: count }, (_, at) => entry((from + at) % 2 === 0 ? "user" : "assistant", stamp(from + at)));
const history = entries(12);
const keyOf = (index: number): string => stamp(index) + (index % 2 === 0 ? "user" : "assistant");
// Where entry i stands in the content without a line: the line adds LINE + GAP to everything after it.
const at = (index: number): number => index * (BUBBLE + GAP);
const FIRST_FOLLOWING_END = at(12) - GAP - VIEW; // 1010: the end of the 12 entries without a line

beforeEach(() => {
  setActivePinia(createPinia());
  posted.length = 0;
  contentGrew = [];
  for (const key of Object.keys(heights)) delete heights[key];
  localStorage.clear();
});
afterEach(() => localStorage.clear());

async function shown(readUntil: string, list = history, sessionId = "s1") {
  const chat = useChat();
  chat.sessionId = sessionId;
  chat.history = list as never;
  chat.readUntil = readUntil;
  const wrapper = shallowMount(ChatView, { global: { stubs: { MessageBubble: false, ReadMark: false } } });
  await flushPromises();
  const box = wrapper.find(".messages").element as HTMLElement;
  return { chat, wrapper, box };
}

// The reader scrolls by hand: the wheel first, then the box moves.
async function readerScrollsTo(wrapper: Awaited<ReturnType<typeof shown>>["wrapper"], top: number): Promise<void> {
  const box = wrapper.find(".messages");
  await box.trigger("wheel");
  (box.element as HTMLElement).scrollTop = top;
  await box.trigger("scroll");
}

const grow = async (): Promise<void> => {
  contentGrew.forEach((callback) => callback());
  await flushPromises();
};

// --- opening ---

test("opening with unread: the line stands at the upper edge, nothing sticks over it", async () => {
  const { box } = await shown("t05");
  // The line stands above the question of entry 6 (position 660), at the start of its turn: the question of the turn
  // before (entry 4) no longer sticks there, the one of entry 6 comes below the line.
  expect(box.scrollTop).toBe(at(6) - 12);
});

test("opening with unread never goes beyond the end: what follows the line fits the screen, so the end", async () => {
  const { box } = await shown("t10");
  expect(box.scrollTop).toBe(box.scrollHeight - VIEW);
});

test("opening a chat nothing of which was read: the line stands above the first bubble, the box at its top", async () => {
  const { box } = await shown("");
  expect(box.scrollTop).toBe(0);
});

test("opening without unread and without a remembered place: the end", async () => {
  const { box } = await shown("t11");
  expect(box.scrollTop).toBe(FIRST_FOLLOWING_END);
});

test("opening without unread, with a remembered place: that bubble stands at the lower edge at its remembered distance", async () => {
  savePlace("s1", { key: keyOf(6), offset: 40 });
  const { box } = await shown("t11");
  // Entry 6 ends at at(6) + BUBBLE = 760; its end should stand 40 above the lower edge.
  expect(box.scrollTop).toBe(at(6) + BUBBLE - VIEW + 40);
});

test("a remembered bubble that is gone: the end", async () => {
  savePlace("s1", { key: "t99user", offset: 40 });
  const { box } = await shown("t11");
  expect(box.scrollTop).toBe(FIRST_FOLLOWING_END);
});

test("unread beats the remembered place", async () => {
  savePlace("s1", { key: keyOf(2), offset: 0 });
  const { box } = await shown("t05");
  expect(box.scrollTop).toBe(at(6) - 12);
});

test("the place of another session is not taken", async () => {
  savePlace("other", { key: keyOf(2), offset: 0 });
  const { box } = await shown("t11");
  expect(box.scrollTop).toBe(FIRST_FOLLOWING_END);
});

test("switching to another session places it by the rules of opening, whatever the reader did before", async () => {
  const { chat, wrapper, box } = await shown("t11");
  await readerScrollsTo(wrapper, 200);
  chat.sessionId = "s2";
  chat.history = entries(12) as never;
  chat.readUntil = "t05";
  await flushPromises();
  expect(box.scrollTop).toBe(at(6) - 12);
});

// --- holding while the content grows ---

test("pictures that load after opening: the line stays where it was put (the gap that opened in the middle)", async () => {
  const { box } = await shown("t05");
  heights[keyOf(2)] = 400; // a picture above the line loads: everything after it moves down by 300
  await grow();
  expect(box.scrollTop).toBe(at(6) - 12 + 300);
});

test("pictures that load after opening at the end: the box stays at the end", async () => {
  const { box } = await shown("t11");
  heights[keyOf(11)] = 500;
  await grow();
  expect(box.scrollTop).toBe(box.scrollHeight - VIEW);
  expect(box.scrollTop).toBe(FIRST_FOLLOWING_END + 400);
});

test("pictures that load after opening at a remembered place: the bubble stays at the lower edge", async () => {
  savePlace("s1", { key: keyOf(6), offset: 40 });
  const { box } = await shown("t11");
  heights[keyOf(0)] = 250; // above: everything moves down by 150
  await grow();
  expect(box.scrollTop).toBe(at(6) + 150 + BUBBLE - VIEW + 40);
});

test("marking read lets go of what the box held: 'all read' takes the line away and the view stays where it is", async () => {
  const { box, chat } = await shown("t05");
  const before = box.scrollTop;
  chat.readUntil = stamp(11); // as "all read" does (the line goes, the content gets shorter)
  await flushPromises();
  await grow();
  expect(box.scrollTop).toBe(before);
});

test("once the reader scrolls by hand, growing content no longer moves the box", async () => {
  const { wrapper, box } = await shown("t05");
  await readerScrollsTo(wrapper, 100);
  heights[keyOf(2)] = 400;
  await grow();
  expect(box.scrollTop).toBe(100);
});

test("the keys and the scroll bar also take the box in hand; a click inside a bubble does not", async () => {
  const { wrapper, box } = await shown("t05");
  const placed = box.scrollTop;
  await wrapper.find("article").trigger("pointerdown"); // on a bubble: not the scroll bar
  heights[keyOf(2)] = 400;
  await grow();
  expect(box.scrollTop).toBe(placed + 300);
  await wrapper.find(".messages").trigger("keydown", { key: "PageUp" });
  heights[keyOf(2)] = 500;
  await grow();
  expect(box.scrollTop).toBe(placed + 300);
});

test("the touch of a finger that drags, and the press on the scroll bar, take the box in hand", async () => {
  const { wrapper, box } = await shown("t05");
  const placed = box.scrollTop;
  await wrapper.find(".messages").trigger("touchmove");
  heights[keyOf(2)] = 400;
  await grow();
  expect(box.scrollTop).toBe(placed);
  const second = await shown("t05");
  const placedAgain = second.box.scrollTop;
  await second.wrapper.find(".messages").trigger("pointerdown"); // the target is the box itself: the scroll bar
  heights[keyOf(2)] = 400;
  await grow();
  expect(second.box.scrollTop).toBe(placedAgain);
});

// --- something comes in ---

test("something new while the reader reads: nothing moves, the button counts what came in below", async () => {
  const { chat, wrapper, box } = await shown("t11");
  await readerScrollsTo(wrapper, 200);
  expect(wrapper.find("button.new-below").exists()).toBe(false);
  chat.history = [...history, ...entries(1, 12).map((item) => ({ ...item, role: "assistant" as const }))] as never;
  await flushPromises();
  expect(box.scrollTop).toBe(200);
  expect(wrapper.find("button.new-below").text()).toBe("unread_below 1");
});

test("the button takes the box to the line above the oldest unread bubble, at the upper edge below the question, and marks nothing", async () => {
  const { chat, wrapper, box } = await shown("t11");
  await readerScrollsTo(wrapper, 200);
  chat.history = [...history, entry("assistant", "t12"), entry("assistant", "t13"), entry("assistant", "t14")] as never;
  await flushPromises();
  expect(wrapper.find("button.new-below").text()).toBe("unread_below 3");
  await wrapper.find("button.new-below").trigger("click");
  // The line stands after entry 11, at at(12) = 1320; the question of its turn is entry 10.
  expect(box.scrollTop).toBe(at(12) - 12 - BUBBLE);
  expect(chat.readUntil).toBe("t11");
  expect(posted).toEqual([]);
  // The one beyond the view is still counted, the ones in view are not.
  expect(wrapper.find("button.new-below").text()).toBe("unread_below 1");
});

test("a new question with its answer below the view: the button shows the line above the question, not the answer", async () => {
  heights[keyOf(13)] = 400; // a long answer: room below, so the end does not stop the box before the line
  const { chat, wrapper, box } = await shown("t11");
  await readerScrollsTo(wrapper, 200);
  chat.history = [...history, entry("user", "t12"), entry("assistant", "t13")] as never;
  await flushPromises();
  expect(wrapper.find("button.new-below").text()).toBe("unread_below 1");
  await wrapper.find("button.new-below").trigger("click");
  // Only the answer is unread, but the line stands above the question (entry 12): the line at the upper edge, nothing
  // sticks over it.
  expect(box.scrollTop).toBe(at(12) - 12);
  const question = box.querySelector<HTMLElement>(`[data-entry="${keyOf(12)}"]`)!.getBoundingClientRect();
  expect(question.top).toBeGreaterThan(0);
});

test("a later unread bubble below the view: the line lies behind the reader, the button goes to that bubble", async () => {
  heights[keyOf(13)] = 400;
  const { chat, wrapper, box } = await shown("t11");
  await readerScrollsTo(wrapper, 200);
  // A question after entry 14 (never unread) leaves room below it, so the box can put entry 14 at the upper edge.
  chat.history = [...history, entry("user", "t12"), entry("assistant", "t13"), entry("assistant", "t14"), entry("user", "t15")] as never;
  await flushPromises();
  await wrapper.find("button.new-below").trigger("click");
  expect(wrapper.find("button.new-below").text()).toBe("unread_below 1");
  await wrapper.find("button.new-below").trigger("click");
  // Entry 14 stands after the line and the 400 high entry 13; the question of its turn (entry 12) sticks above it.
  const later = at(14) + LINE + GAP + (400 - BUBBLE);
  expect(box.scrollTop).toBe(later - 12 - BUBBLE);
});

test("something new while the reader follows with nothing unread: the box goes to the end", async () => {
  const { chat, box } = await shown("t11");
  chat.history = [...history, entry("user", "t12")] as never; // a question is never unread
  await flushPromises();
  expect(box.scrollTop).toBe(box.scrollHeight - VIEW);
  expect(box.scrollTop).toBe(at(13) - GAP - VIEW);
});

test("something new while the reader follows with unread: the box moves on only until the line stands at the upper edge, and stays", async () => {
  const { chat, box } = await shown("t11");
  chat.history = [...history, entry("assistant", "t12"), entry("assistant", "t13"), entry("assistant", "t14")] as never;
  await flushPromises();
  // The line after entry 11 at at(12), under the question of entry 10; the content reaches far enough below it.
  const line = at(12) - 12 - BUBBLE;
  expect(box.scrollTop).toBe(line);
  chat.history = [...chat.history, entry("assistant", "t15")] as never;
  await flushPromises();
  expect(box.scrollTop).toBe(line);
});

test("a short new answer while the reader follows: the end is reached before the line, so the end", async () => {
  const { chat, box } = await shown("t11");
  chat.history = [...history, entry("assistant", "t12")] as never;
  await flushPromises();
  expect(box.scrollTop).toBe(box.scrollHeight - VIEW);
});

test("the answer being written follows the end while the reader follows", async () => {
  const { chat, box } = await shown("t11");
  chat.streaming = { agent: "aifred", displayName: "AIfred", emoji: "", text: "a" } as never;
  await flushPromises();
  heights[keyOf(11)] = 400; // stands for the streaming bubble growing the content
  chat.streaming = { agent: "aifred", displayName: "AIfred", emoji: "", text: "ab" } as never;
  await flushPromises();
  expect(box.scrollTop).toBe(box.scrollHeight - VIEW);
});

test("the reader's own message takes the box to the end although they read above, and they follow again", async () => {
  const settings = useSettings();
  settings.current = { ui_language: "de" } as never;
  const { chat, wrapper, box } = await shown("t11");
  await readerScrollsTo(wrapper, 200);
  wrapper.findComponent({ name: "ChatInput" }).vm.$emit("send", "hallo");
  await flushPromises();
  chat.history = [...history, entry("user", "t12")] as never;
  await flushPromises();
  expect(box.scrollTop).toBe(box.scrollHeight - VIEW);
  // Following again: the next answer, unread, moves the box on to its line.
  chat.history = [...chat.history, entry("assistant", "t13"), entry("assistant", "t14"), entry("assistant", "t15")] as never;
  await flushPromises();
  expect(box.scrollTop).toBe(at(12) - 12); // the line above the reader's question (entry 12), at the start of its turn
});

test("a refused message: what was typed comes back, the refusal shows, and the next user bubble is not taken for it", async () => {
  const settings = useSettings();
  settings.current = { ui_language: "de" } as never;
  const { chat, wrapper, box } = await shown("t11");
  await readerScrollsTo(wrapper, 200);
  refuseSend = true;
  try {
    wrapper.findComponent({ name: "ChatInput" }).vm.$emit("send", "hallo");
    await flushPromises();
  } finally {
    refuseSend = false;
  }
  expect(chat.draft).toBe("hallo");
  expect(chat.notice).toBe("Error: refused");
  chat.history = [...history, entry("user", "t12")] as never; // another tab's message
  await flushPromises();
  expect(box.scrollTop).toBe(200);
});

test("a message of someone else (not sent here) does not take a reading reader to the end", async () => {
  const { chat, wrapper, box } = await shown("t11");
  await readerScrollsTo(wrapper, 200);
  chat.history = [...history, entry("user", "t12")] as never;
  await flushPromises();
  expect(box.scrollTop).toBe(200);
});

// --- the place is kept ---

test("the lowest bubble in view and its distance are kept per session, and taken again by a fresh page", async () => {
  const first = await shown("t11");
  await readerScrollsTo(first.wrapper, 500);
  // View 500..800: entry 7 (770..870) is the lowest that begins in view; its end is 70 below the lower edge.
  expect(JSON.parse(localStorage.getItem("aifred-chat-place-s1")!)).toEqual({ key: keyOf(7), offset: -70 });
  first.wrapper.unmount();
  setActivePinia(createPinia()); // a reload: a new store, the page keeps nothing but the browser's storage
  const again = await shown("t11");
  expect(again.box.scrollTop).toBe(500);
});

test("scrolling does not set the mark", async () => {
  const { chat, wrapper } = await shown("t05");
  await readerScrollsTo(wrapper, 1000);
  await readerScrollsTo(wrapper, 0);
  expect(chat.readUntil).toBe("t05");
  expect(posted).toEqual([]);
});
