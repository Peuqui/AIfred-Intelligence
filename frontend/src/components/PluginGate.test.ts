// A plugin's page: the slot while it is on, the hint while it is off, and it follows the switch live.
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, test, vi } from "vitest";
import type { PluginOverview } from "../api/types";

vi.mock("../api/client", () => ({ ApiError: class extends Error {}, api: vi.fn() }));
vi.mock("../i18n", () => ({ t: (key: string) => key }));

const { default: PluginGate } = await import("./PluginGate.vue");
const { usePlugins } = await import("../stores/plugins");

const overview = (enabled: boolean): PluginOverview =>
  ({ tools: [{ name: "vision", label: "Vision", enabled }], channels: [], tiers: [] }) as unknown as PluginOverview;

beforeEach(() => setActivePinia(createPinia()));

function gate() {
  return mount(PluginGate, { props: { name: "vision" }, slots: { default: "<p id='page'>page</p>" } });
}

test("on: the page", () => {
  usePlugins().overview = overview(true);
  const wrapper = gate();
  expect(wrapper.find("#page").exists()).toBe(true);
  expect(wrapper.text()).not.toContain("plugin_off_hint");
});

test("off: the hint with the plugin's name instead of the page", () => {
  usePlugins().overview = overview(false);
  const wrapper = gate();
  expect(wrapper.find("#page").exists()).toBe(false);
  expect(wrapper.text()).toBe("Vision: plugin_off_hint");
});

test("not loaded yet: neither page nor hint", () => {
  const wrapper = gate();
  expect(wrapper.find("#page").exists()).toBe(false);
  expect(wrapper.text()).toBe("");
});

test("switching shows the other side at once", async () => {
  const plugins = usePlugins();
  plugins.overview = overview(true);
  const wrapper = gate();
  plugins.overview = overview(false);
  await wrapper.vm.$nextTick();
  expect(wrapper.find("#page").exists()).toBe(false);
  plugins.overview = overview(true);
  await wrapper.vm.$nextTick();
  expect(wrapper.find("#page").exists()).toBe(true);
});
