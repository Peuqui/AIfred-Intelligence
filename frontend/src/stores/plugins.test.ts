// The one place the interface knows which plugins are on; a switch announced by the server loads it anew.
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { PluginOverview } from "../api/types";
import { resourceChanged } from "../lib/resourceChanges";

let answer: PluginOverview;
const api = vi.fn(async () => answer);
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, api: () => api() }));

const { usePlugins } = await import("./plugins");

const overview = (visionOn: boolean, mailOn: boolean): PluginOverview =>
  ({
    tools: [{ name: "vision", label: "Vision", enabled: visionOn }],
    channels: [{ name: "mail", label: "Mail", monitor: mailOn }],
    tiers: [],
  }) as unknown as PluginOverview;

beforeEach(() => {
  vi.useFakeTimers();
  setActivePinia(createPinia());
  api.mockClear();
  answer = overview(true, false);
});
afterEach(() => vi.useRealTimers());

test("tools and channels are on as the server says, unknown ones and an unloaded list are off", async () => {
  const plugins = usePlugins();
  expect(plugins.toolOn("vision")).toBe(false);
  await plugins.load();
  expect([plugins.toolOn("vision"), plugins.toolOn("nope"), plugins.channelOn("mail"), plugins.channelOn("nope")]).toEqual([true, false, false, false]);
});

test("the event \"plugins\" loads the list anew", async () => {
  const plugins = usePlugins();
  await plugins.load();
  answer = overview(false, true);
  resourceChanged("plugins");
  await vi.advanceTimersByTimeAsync(400);
  expect([plugins.toolOn("vision"), plugins.channelOn("mail")]).toEqual([false, true]);
});
