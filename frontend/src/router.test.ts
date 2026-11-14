// A plugin's pages carry the plugin in the route's meta (router.ts); the menu and the page's gate read it there.
import { expect, test, vi } from "vitest";

vi.mock("./api/client", () => ({ ApiError: class extends Error {}, api: vi.fn(), apiUrl: (path: string) => path }));

// The views read the device preferences at import; jsdom has no matchMedia.
window.matchMedia = ((query: string) => ({ matches: false, media: query, addEventListener: () => undefined, removeEventListener: () => undefined })) as unknown as typeof window.matchMedia;

const { router } = await import("./router");

test("every tab of the vision page and the menu's link to it name the vision plugin", () => {
  for (const path of ["/vision", "/vision/live", "/vision/events", "/vision/settings"]) {
    expect(router.resolve(path).meta.plugin, path).toBe("vision");
  }
});

test("the jobs page names its plugin, other pages none", () => {
  expect(router.resolve("/jobs").meta.plugin).toBe("scheduler_tool");
  expect(router.resolve("/agents").meta.plugin).toBeUndefined();
});

test("the vision address opens the live tab", async () => {
  await router.push("/vision");
  expect(router.currentRoute.value.path).toBe("/vision/live");
});
