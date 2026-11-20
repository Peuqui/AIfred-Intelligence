// The band over the chat: only while a request waits for a decision; its buttons are the status line's actions.
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, test, vi } from "vitest";
import type { LocalHoldStatus } from "../api/types";

vi.mock("../i18n", () => ({
  t: (key: string, values: Record<string, string> = {}) => `${key}${Object.keys(values).length ? ` ${JSON.stringify(values)}` : ""}`,
}));
const api = vi.fn();
vi.mock("../api/client", () => ({ api: (...args: unknown[]) => api(...args), ApiError: class extends Error {} }));

const { default: GpuHoldBanner } = await import("./GpuHoldBanner.vue");
const { useServiceStatus } = await import("../stores/serviceStatus");

const hold = (state: LocalHoldStatus["state"]): LocalHoldStatus => ({
  state, machine: "Mini", owner: "vllm-research", reason: "v13", since: null, model: null, granted_by: null, error: "",
});

beforeEach(() => {
  setActivePinia(createPinia());
  api.mockReset();
  api.mockImplementation(async (path: string) => (path === "service-status/local-hold" ? hold("requested") : {})); // still asked after a click
});

test("shown only while a request waits, gone in every other state and before the first answer", async () => {
  const status = useServiceStatus();
  const wrapper = mount(GpuHoldBanner);
  expect(wrapper.find(".band").exists()).toBe(false);
  for (const state of ["free", "granted", "active", "off", "unreachable"] as const) {
    status.localHold = hold(state);
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".band").exists(), state).toBe(false);
  }
});

test("a request: the band names owner and reason, the buttons grant and end (with the question)", async () => {
  const status = useServiceStatus();
  status.localHold = hold("requested");
  const wrapper = mount(GpuHoldBanner);
  expect(wrapper.text()).toContain('gpu_hold_banner {"owner":"vllm-research","reason":"v13"}');
  const [grant, end] = wrapper.findAll("button");
  await grant?.trigger("click");
  await flushPromises(); // the click is done (the button is disabled while it acts)
  expect(api).toHaveBeenCalledWith("service-status/grant", { method: "POST", body: JSON.stringify({ owner: "vllm-research" }) });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  await end?.trigger("click");
  expect(confirm).toHaveBeenCalledOnce();
  expect(api).not.toHaveBeenCalledWith("service-status/end-hold", expect.anything()); // declined: nothing ended
  confirm.mockReturnValue(true);
  await end?.trigger("click");
  expect(api).toHaveBeenCalledWith("service-status/end-hold", { method: "POST", body: JSON.stringify({ owner: "vllm-research" }) });
});

test("it disappears when the request is decided", async () => {
  const status = useServiceStatus();
  status.localHold = hold("requested");
  const wrapper = mount(GpuHoldBanner);
  expect(wrapper.find(".band").exists()).toBe(true);
  status.localHold = hold("granted");
  await wrapper.vm.$nextTick();
  expect(wrapper.find(".band").exists()).toBe(false);
});
