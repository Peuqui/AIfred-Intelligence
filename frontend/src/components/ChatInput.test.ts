// A dictated text in the input field: the field takes the focus with the cursor at the end, so Enter sends it (as in
// Agent-Orc); not on touch screens, where the focus would raise the on-screen keyboard.
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, test, vi } from "vitest";
import type { Settings } from "../api/types";

let touchFirst = false;
window.matchMedia = ((query: string) => ({
  get matches() {
    return query === "(pointer: coarse)" ? touchFirst : false;
  },
  media: query,
  addEventListener: () => undefined,
  removeEventListener: () => undefined,
})) as unknown as typeof window.matchMedia;

vi.mock("../i18n", () => ({ t: (key: string) => key, language: { value: "de" } }));
vi.mock("../api/client", () => ({ api: () => Promise.resolve({}), ApiError: class extends Error {} }));
// The reading's audio unlocks itself at the first key press with an Audio jsdom cannot play.
vi.mock("../lib/speech", async () => ({ speaking: (await import("vue")).ref(false) }));

const { default: ChatInput } = await import("./ChatInput.vue");

function mountInput() {
  return mount(ChatInput, {
    attachTo: document.body,
    props: {
      modelValue: "",
      running: false,
      queued: 0,
      images: [],
      uploading: false,
      transcribing: false,
      dictationsLanded: 0,
      settings: { dictation_engine: "stt" } as Settings,
    },
  });
}

beforeEach(() => {
  setActivePinia(createPinia());
  touchFirst = false;
  document.body.innerHTML = "";
});

test("a dictated text: the field takes the focus, the cursor after the text", async () => {
  const wrapper = mountInput();
  const field = wrapper.find("textarea").element;
  wrapper.find<HTMLButtonElement>("button.mic").element.focus();
  await wrapper.setProps({ modelValue: "Hallo Welt", dictationsLanded: 1 });
  await flushPromises();
  expect(document.activeElement).toBe(field);
  expect(field.selectionStart).toBe("Hallo Welt".length);
  expect(field.selectionEnd).toBe("Hallo Welt".length);
});

test("Enter right after the dictation sends the text", async () => {
  const wrapper = mountInput();
  await wrapper.setProps({ modelValue: "Hallo Welt", dictationsLanded: 1 });
  await flushPromises();
  (document.activeElement as HTMLElement).dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  expect(wrapper.emitted("send")).toEqual([["Hallo Welt"]]);
});

test("on a touch screen the focus stays where it was (no on-screen keyboard)", async () => {
  touchFirst = true;
  const wrapper = mountInput();
  const mic = wrapper.find<HTMLButtonElement>("button.mic").element;
  mic.focus();
  await wrapper.setProps({ modelValue: "Hallo Welt", dictationsLanded: 1 });
  await flushPromises();
  expect(document.activeElement).toBe(mic);
});
