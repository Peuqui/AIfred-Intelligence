// While vision is off the page still offers to delete what it stored: every event at once, a person by one, each asked first.
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, expect, test, vi } from "vitest";

const api = vi.fn();
vi.mock("../api/client", () => ({ ApiError: class extends Error { detail = ""; }, api: (path: string, init?: RequestInit) => api(path, init) }));
vi.mock("../i18n", () => ({ t: (key: string) => key, tCount: (key: string, count: number) => `${key}:${count}`, formatNumber: (value: number) => String(value) }));

const { default: VisionStored } = await import("./VisionStored.vue");

const stored = { events: 3, persons: [{ id: "7", label: "Anna" }] };

beforeEach(() => {
  api.mockReset();
  api.mockResolvedValue(stored);
});

async function shown() {
  const wrapper = mount(VisionStored);
  await flushPromises();
  return wrapper;
}

test("shows the counts and the persons", async () => {
  const wrapper = await shown();
  expect(api).toHaveBeenCalledWith("vision/stored", undefined);
  expect(wrapper.text()).toContain("vision_stored_events:3");
  expect(wrapper.text()).toContain("Anna");
});

test("all events: asked first, then one request", async () => {
  const wrapper = await shown();
  await wrapper.find("button.danger").trigger("click");
  expect(api).toHaveBeenCalledTimes(1);
  api.mockResolvedValue({ events: 0, persons: stored.persons });
  await wrapper.find("button.danger").trigger("click");   // now the confirmation
  await flushPromises();
  expect(api).toHaveBeenCalledWith("vision/casus/events/delete", { method: "POST", body: "{}" });
  expect(wrapper.text()).toContain("vision_stored_events:0");
});

test("a person: asked first, then deleted by its id", async () => {
  const wrapper = await shown();
  const buttons = () => wrapper.findAll("button.danger");
  await buttons()[2]!.trigger("click");
  expect(api).toHaveBeenCalledTimes(1);
  await buttons()[2]!.trigger("click");
  await flushPromises();
  expect(api).toHaveBeenCalledWith("vision/face/7", { method: "DELETE" });
});

test("all persons: the button only with persons, asked first, then exactly one request", async () => {
  const wrapper = await shown();
  const buttons = () => wrapper.findAll("button.danger");
  await buttons()[1]!.trigger("click");
  expect(api).toHaveBeenCalledTimes(1);
  api.mockResolvedValue({ events: 3, persons: [] });
  await buttons()[1]!.trigger("click");   // now the confirmation
  await flushPromises();
  expect(api.mock.calls.filter(([path]) => path === "vision/face/delete")).toEqual([["vision/face/delete", { method: "POST" }]]);
  expect(wrapper.text()).not.toContain("vision_stored_persons");
  expect(buttons()).toHaveLength(1);   // only the events' button is left
});
