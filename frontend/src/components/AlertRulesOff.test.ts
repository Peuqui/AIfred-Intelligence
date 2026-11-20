// An alert rule that leads to a channel that is off stays in the table, marked, and can be taken away;
// the table is read anew when the plugins change.
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, test, vi } from "vitest";
import type { AlertRules, PluginOverview } from "../api/types";

let rules: AlertRules;
const api = vi.fn(async (path: string) => (path === "vision/alert-rules" ? rules : path === "vision/settings" ? { model: "Vision-LLM-27B", modes: [] } : {}));
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, api: (path: string) => api(path) }));
vi.mock("../i18n", () => ({ t: (key: string, values?: { channel?: string }) => (values?.channel ? `${key}:${values.channel}` : key), tCount: (key: string) => key, formatNumber: (value: number) => String(value) }));
vi.mock("./RtspForm.vue", () => ({ default: { template: "<i />" } }));
vi.mock("./VigilantiaEye.vue", () => ({ default: { template: "<i />" } }));
vi.mock("./ZoneEditor.vue", () => ({ default: { template: "<i />" } }));

const { default: VisionSettings } = await import("./VisionSettings.vue");
const { usePlugins } = await import("../stores/plugins");

const table = (sinks: string[]): AlertRules =>
  ({ channels: [{ id: "mail", label: "Mail" }], rules: [{ category: "person", label: "Person", sinks, vlm: false }] }) as unknown as AlertRules;
const overview = (): PluginOverview => ({ tools: [], channels: [], tiers: [] }) as unknown as PluginOverview;

const mountOptions = { global: { stubs: { RouterLink: { template: "<a><slot /></a>" } } } };

beforeEach(() => {
  setActivePinia(createPinia());
  api.mockClear();
});

test("a sink on a channel that is off gets its own marked column; the channels that are on keep theirs", async () => {
  rules = table(["mail", "echo"]);
  const wrapper = mount(VisionSettings, mountOptions);
  await flushPromises();
  const heads = wrapper.findAll(".rules th").map((cell) => cell.text());
  expect(heads).toContain("Mail");
  expect(heads).toContain("channel_off_marker:echo");
  expect(wrapper.findAll(".rules th.off")).toHaveLength(1);
});

test("no sink on a channel that is off: no extra column; a switch of the plugins reads the table anew", async () => {
  rules = table(["mail"]);
  const wrapper = mount(VisionSettings, mountOptions);
  await flushPromises();
  expect(wrapper.findAll(".rules th.off")).toHaveLength(0);
  rules = table(["mail", "echo"]);
  usePlugins().overview = overview();
  await flushPromises();
  expect(wrapper.findAll(".rules th.off")).toHaveLength(1);
});

test("the image model is shown as the Vision-LLM of the main settings, with the hint, not as a choice", async () => {
  rules = table([]);
  const wrapper = mount(VisionSettings, mountOptions);
  await flushPromises();
  expect(wrapper.find(".model").text()).toBe("Vision-LLM-27B");
  expect(wrapper.text()).toContain("vision_settings_model_help");
  expect(wrapper.findAll("select")).toHaveLength(1);   // only the mode
});
