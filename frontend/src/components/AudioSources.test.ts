// The audio player's box follows the plugin's switch live: off, it is gone and asks for nothing; on, it reads the sources.
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, test, vi } from "vitest";
import type { PluginOverview } from "../api/types";

const api = vi.fn();
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, api: (path: string) => api(path), PROGRESS_POLL_MS: 1000 }));
vi.mock("../i18n", () => ({ t: (key: string) => key, tCount: (key: string) => key, formatNumber: (value: number) => String(value) }));
vi.mock("./FileRequester.vue", () => ({ default: { template: "<i />" } }));

const { default: AudioSources } = await import("./AudioSources.vue");
const { usePlugins } = await import("../stores/plugins");

const overview = (enabled: boolean): PluginOverview =>
  ({ tools: [{ name: "audio_player", label: "Audio", enabled }], channels: [], tiers: [] }) as unknown as PluginOverview;
const library = { sources: [{ label: "Musik", type: "local_folder", target: "/x", is_symlink: true, indexed: 3 }], picker_root: "/mnt", indexing: "", message: "" };

beforeEach(() => {
  setActivePinia(createPinia());
  api.mockReset();
  api.mockResolvedValue(library);
});

test("off: nothing shown, nothing asked", async () => {
  usePlugins().overview = overview(false);
  const wrapper = mount(AudioSources);
  await flushPromises();
  expect(wrapper.find(".sources").exists()).toBe(false);
  expect(api).not.toHaveBeenCalled();
});

test("on: the sources are read and shown; the switch off takes the box away, on again brings it back", async () => {
  const plugins = usePlugins();
  plugins.overview = overview(true);
  const wrapper = mount(AudioSources);
  await flushPromises();
  expect(wrapper.find(".sources").exists()).toBe(true);
  expect(wrapper.text()).toContain("Musik");
  expect(api).toHaveBeenCalledWith("audio/sources");

  plugins.overview = overview(false);
  await flushPromises();
  expect(wrapper.find(".sources").exists()).toBe(false);

  api.mockClear();
  plugins.overview = overview(true);
  await flushPromises();
  expect(wrapper.find(".sources").exists()).toBe(true);
  expect(api).toHaveBeenCalledWith("audio/sources");
});
