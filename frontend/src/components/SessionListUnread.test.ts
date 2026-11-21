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
