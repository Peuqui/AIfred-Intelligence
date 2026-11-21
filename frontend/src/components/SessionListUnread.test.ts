// The list of chats names how many answers of a chat are still unread.
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { expect, test, vi } from "vitest";

// jsdom has no matchMedia; the device preferences read it at import.
window.matchMedia = ((query: string) => ({ matches: false, media: query, addEventListener: () => undefined, removeEventListener: () => undefined })) as unknown as typeof window.matchMedia;

vi.mock("../i18n", () => ({ t: (key: string) => key, tCount: (key: string, count: number) => `${key} ${count}` }));
vi.mock("../api/client", () => ({ api: () => Promise.resolve([]), ApiError: class extends Error {} }));

const { default: SessionList } = await import("./SessionList.vue");
const { useSessions } = await import("../stores/sessions");
const { sessionsColumn } = await import("../lib/devicePrefs");

const row = (session_id: string, unread: number) => ({ session_id, title: session_id, last_message_display: "", message_count: 3, read_until: "", unread });

test("the open chat counts what it shows: its mark moved, its \"new\" goes at once", async () => {
  setActivePinia(createPinia());
  sessionsColumn.value = true;
  useSessions().list = [row("a", 1), row("b", 2)] as never;
  const { useChat } = await import("../stores/chat");
  const chat = useChat();
  chat.sessionId = "a";
  chat.history = [{ role: "assistant", content: "x", timestamp: "t1" }] as never;
  chat.readUntil = "";
  const wrapper = mount(SessionList, { props: { current: "a" } });
  await wrapper.find("button.toggle").trigger("click");
  expect(wrapper.findAll(".unread").map((node) => node.text())).toEqual(["session_unread 1", "session_unread 2"]);
  chat.readUntil = "t1"; // the bubble was clicked; the server's list still says 1
  await wrapper.vm.$nextTick();
  expect(wrapper.findAll(".unread").map((node) => node.text())).toEqual(["session_unread 2"]);
});

test("a chat with unread answers shows how many, one without shows nothing", () => {
  setActivePinia(createPinia());
  sessionsColumn.value = true;
  useSessions().list = [row("a", 3), row("b", 0)] as never;
  const wrapper = mount(SessionList, { props: { current: null } });
  wrapper.find("button.toggle").trigger("click");
  return wrapper.vm.$nextTick().then(() => {
    const unread = wrapper.findAll(".unread");
    expect(unread.map((node) => node.text())).toEqual(["session_unread 3"]);
  });
});
