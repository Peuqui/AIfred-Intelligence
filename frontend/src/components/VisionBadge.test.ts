// The chat's vision badge follows the vision plugin's switch: off, it is gone, asks for nothing and holds no stream;
// on, it loads and listens again.
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, test, vi } from "vitest";
import type { PluginOverview } from "../api/types";

const api = vi.fn();
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, api: (path: string) => api(path), apiUrl: (path: string) => `/api/${path}` }));
vi.mock("../i18n", () => ({ t: (key: string) => key, tCount: (key: string) => key, formatNumber: (value: number) => String(value) }));
vi.mock("./help/HelpButton.vue", () => ({ default: { template: "<i />" } }));
vi.mock("./VigilantiaEye.vue", () => ({ default: { template: "<i />" } }));

const opened: { url: string; closed: boolean }[] = [];
class FakeEventSource {
  closed = false;
  constructor(readonly url: string) {
    opened.push(this);
  }
  addEventListener(): void {}
  close(): void {
    this.closed = true;
  }
}
vi.stubGlobal("EventSource", FakeEventSource);

const { default: VisionBadge } = await import("./VisionBadge.vue");
const { usePlugins } = await import("../stores/plugins");

const overview = (enabled: boolean): PluginOverview =>
  ({ tools: [{ name: "vision", label: "Vision", enabled }], channels: [], tiers: [] }) as unknown as PluginOverview;
const recent = { armed: true, has_watched_camera: true, window_minutes: 10, recent: 0, events: [], refresh_seconds: 60 };

beforeEach(() => {
  setActivePinia(createPinia());
  opened.length = 0;
  api.mockReset();
  api.mockResolvedValue(recent);
});

test("off: nothing shown, nothing asked, no stream", async () => {
  usePlugins().overview = overview(false);
  const wrapper = mount(VisionBadge);
  await flushPromises();
  expect(wrapper.find(".vigilantia").exists()).toBe(false);
  expect(api).not.toHaveBeenCalled();
  expect(opened).toHaveLength(0);
});

test("on: loads, shows itself and listens", async () => {
  usePlugins().overview = overview(true);
  const wrapper = mount(VisionBadge);
  await flushPromises();
  expect(api).toHaveBeenCalledWith("vision/casus/recent");
  expect(wrapper.find(".vigilantia").exists()).toBe(true);
  expect(opened.map((source) => source.url)).toEqual(["/api/vision/casus/stream"]);
});

test("switched off live: the stream closes and the badge goes; on again: a new stream", async () => {
  const plugins = usePlugins();
  plugins.overview = overview(true);
  const wrapper = mount(VisionBadge);
  await flushPromises();
  plugins.overview = overview(false);
  await flushPromises();
  expect(opened[0]?.closed).toBe(true);
  expect(wrapper.find(".vigilantia").exists()).toBe(false);
  plugins.overview = overview(true);
  await flushPromises();
  expect(opened).toHaveLength(2);
  expect(opened[1]?.closed).toBe(false);
  expect(wrapper.find(".vigilantia").exists()).toBe(true);
});
