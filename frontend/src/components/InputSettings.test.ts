// The dictation switch names the machine the STT-Suite runs on (the page may be open on another machine).
import { flushPromises, mount } from "@vue/test-utils";
import { expect, test, vi } from "vitest";
import type { Settings } from "../api/types";

// jsdom has no matchMedia; the device preferences read it at import.
window.matchMedia = ((query: string) => ({ matches: false, media: query, addEventListener: () => undefined, removeEventListener: () => undefined })) as unknown as typeof window.matchMedia;

const api = vi.fn();
vi.mock("../api/client", () => ({ api: (path: string) => api(path) }));
vi.mock("../i18n", () => ({ t: (key: string, params?: Record<string, string>) => (params ? `${key} ${JSON.stringify(params)}` : key) }));

const { default: InputSettings } = await import("./InputSettings.vue");
const settings = { dictation_engine: "stt_suite", show_transcription: false } as Settings;

test("the STT-Suite is named by the machine it answers from", async () => {
  api.mockResolvedValue({ available: true, machine: "Mini" });
  const wrapper = mount(InputSettings, { props: { settings } });
  await flushPromises();
  expect(api).toHaveBeenCalledWith("speech/recognition");
  expect(wrapper.text()).toContain('dictation_engine_stt_suite {"machine":"Mini"}');
});

test("an STT-Suite that does not answer is named without a machine", async () => {
  api.mockResolvedValue({ available: false, machine: null });
  const wrapper = mount(InputSettings, { props: { settings } });
  await flushPromises();
  expect(wrapper.text()).toContain("dictation_engine_stt_suite_silent");
});
