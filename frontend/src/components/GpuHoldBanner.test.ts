// A hold shows first as the pill in the chat's title row, also a request (not big at once, as in Agent-Orc); a tap opens
// the band with the status line's buttons, "fold" and every change of the hold turn it back into the pill.
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, test, vi } from "vitest";
import { defineComponent, h } from "vue";
import type { LocalHoldStatus } from "../api/types";

vi.mock("../i18n", async () => {
  const { ref } = await import("vue");
  return {
    language: ref("de"),
    t: (key: string, values: Record<string, string> = {}) => `${key}${Object.keys(values).length ? ` ${JSON.stringify(values)}` : ""}`,
  };
});
const api = vi.fn();
vi.mock("../api/client", () => ({ api: (...args: unknown[]) => api(...args), ApiError: class extends Error {} }));

const { default: GpuHoldBanner } = await import("./GpuHoldBanner.vue");
const { default: GpuHoldPill } = await import("./GpuHoldPill.vue");
const { useServiceStatus } = await import("../stores/serviceStatus");

const Both = defineComponent({ render: () => [h(GpuHoldPill), h(GpuHoldBanner)] });

const hold = (state: LocalHoldStatus["state"], endBy: string | null = null): LocalHoldStatus => ({
  state, machine: "Mini", owner: "vllm-research", reason: "v13", since: "2026-11-24T08:00:00", model: null, granted_by: null,
  end_requested_by: endBy, error: "", used_mib: null, total_mib: null,
});

beforeEach(() => {
  setActivePinia(createPinia());
  api.mockReset();
  api.mockImplementation(async (path: string) => (path === "service-status/local-hold" ? hold("requested") : {}));
});

test("nothing without a hold: no pill, no band", async () => {
  const status = useServiceStatus();
  const wrapper = mount(Both);
  for (const state of ["free", "off", "unreachable"] as const) {
    status.localHold = hold(state);
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".pill").exists(), state).toBe(false);
    expect(wrapper.find(".band").exists(), state).toBe(false);
  }
});

test("every hold starts as the pill, a request too, with owner, state word and a pulsing dot only while undecided", async () => {
  const status = useServiceStatus();
  const wrapper = mount(Both);
  const cases = [["requested", null, "gpu_hold_short_requested", true], ["granted", null, "gpu_hold_short_granted", false],
    ["active", null, "gpu_hold_short_active", false], ["active", "Peuqui", "gpu_hold_short_ending", false]] as const;
  for (const [state, endBy, word, pulses] of cases) {
    status.localHold = hold(state, endBy);
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".band").exists(), state).toBe(false);
    expect(wrapper.find(".pill").text(), state).toBe(`vllm-research${word}`);
    expect(wrapper.find(".pill .dot").classes("pending"), state).toBe(pulses);
  }
});

test("a tap opens the band, fold closes it, a change of the hold folds it again", async () => {
  const status = useServiceStatus();
  status.localHold = hold("requested");
  const wrapper = mount(Both);
  await wrapper.find(".pill").trigger("click");
  expect(wrapper.find(".pill").exists()).toBe(false);
  expect(wrapper.find(".band").text()).toContain("service_status_local_requested");
  await wrapper.find("button.fold").trigger("click");
  expect(wrapper.find(".band").exists()).toBe(false);
  await wrapper.find(".pill").trigger("click");
  status.localHold = hold("granted");
  await wrapper.vm.$nextTick();
  expect(wrapper.find(".band").exists()).toBe(false);
  expect(wrapper.find(".pill").exists()).toBe(true);
});

test("the band's buttons: grant a request, end with the question, release only after the end was asked", async () => {
  const status = useServiceStatus();
  status.localHold = hold("requested");
  const wrapper = mount(Both);
  await wrapper.find(".pill").trigger("click");
  const [grant, end, release] = wrapper.findAll(".band button");
  expect(release?.attributes("disabled")).toBeDefined();
  expect(wrapper.find(".notes").text()).toContain("service_status_local_release_needs_request"); // the reason as text
  await grant?.trigger("click");
  await flushPromises();
  expect(api).toHaveBeenCalledWith("service-status/grant", { method: "POST", body: JSON.stringify({ owner: "vllm-research" }) });
  status.setHoldOpen(true); // the refetched hold is the same request
  await wrapper.vm.$nextTick();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  await end?.trigger("click");
  expect(confirm).toHaveBeenCalledOnce();
  expect(api).not.toHaveBeenCalledWith("service-status/end-hold", expect.anything());
  confirm.mockReturnValue(true);
  await wrapper.findAll(".band button")[1]?.trigger("click");
  expect(api).toHaveBeenCalledWith("service-status/end-hold", { method: "POST", body: JSON.stringify({ owner: "vllm-research" }) });
});
