// The narrator's row on the audio page follows the narrator plugin's switch live: off, no row and no request for its voice.
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, test, vi } from "vitest";
import type { PluginOverview } from "../api/types";

const api = vi.fn();
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, api: (path: string) => api(path) }));
vi.mock("../i18n", () => ({ t: (key: string) => key, tCount: (key: string) => key, formatNumber: (value: number) => String(value) }));
vi.mock("../components/AudioSources.vue", () => ({ default: { template: "<i />" } }));
vi.mock("../components/TtsIcon.vue", () => ({ default: { template: "<i />" } }));
vi.mock("../components/FileRequester.vue", () => ({ default: { template: "<i />" } }));

const { default: AudioView } = await import("./AudioView.vue");
const { usePlugins } = await import("../stores/plugins");
const { useSettings } = await import("../stores/settings");

const overview = (enabled: boolean): PluginOverview =>
  ({ tools: [{ name: "narrator", label: "Narrator", enabled }], channels: [], tiers: [] }) as unknown as PluginOverview;
const answers: Record<string, unknown> = {
  "speech/list": { rows: [], service_error: "" },
  "speech/choices": { engines: [{ id: "edge", label: "Edge" }], units: [], languages: [], speeds: [], pitches: [], service_error: "" },
  "speech/recognition": { available: true },
  "speech/agents?engine=edge": [{ agent: "aifred", display_name: "AIfred", emoji: "", enabled: true, language: "de", voice: "", voices: [], speed: "", pitch: "" }],
  "speech/narrator/edge": { engine: "edge", voice: "Katja", voices: ["Katja"] },
};

beforeEach(() => {
  setActivePinia(createPinia());
  api.mockReset();
  api.mockImplementation((path: string) => Promise.resolve(answers[path]));
  useSettings().current = { enable_tts: true, tts_autoplay: false, tts_list_max: 5 } as never;
});

test("narrator off: no row, its voice is not asked for; on: the row appears, off again: it is gone", async () => {
  const plugins = usePlugins();
  plugins.overview = overview(false);
  const wrapper = mount(AudioView);
  await flushPromises();
  expect(wrapper.find("tr.narrator").exists()).toBe(false);
  expect(api).not.toHaveBeenCalledWith("speech/narrator/edge");

  plugins.overview = overview(true);
  await flushPromises();
  expect(wrapper.find("tr.narrator").exists()).toBe(true);
  expect(api).toHaveBeenCalledWith("speech/narrator/edge");

  plugins.overview = overview(false);
  await flushPromises();
  expect(wrapper.find("tr.narrator").exists()).toBe(false);
});

test("the cut of lists in the spoken reply is a speech setting, there while no plugin is on", async () => {
  usePlugins().overview = overview(false);
  const wrapper = mount(AudioView);
  await flushPromises();
  expect((wrapper.find("input.number").element as HTMLInputElement).value).toBe("5");
});
