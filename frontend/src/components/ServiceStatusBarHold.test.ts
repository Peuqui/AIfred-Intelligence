// A hold of this machine's cards shows as a small pill in the closed status line (owner and state word, the dot pulses
// while a request waits); being part of the summary, a tap opens the bar with the hold's card and buttons.
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, test, vi } from "vitest";
import type { LocalHoldStatus } from "../api/types";

vi.mock("../i18n", async () => {
  const { ref } = await import("vue");
  return { language: ref("de"), t: (key: string) => key };
});
const api = vi.fn();
vi.mock("../api/client", () => ({ api: (...args: unknown[]) => api(...args), ApiError: class extends Error {} }));

const { default: ServiceStatusBar } = await import("./ServiceStatusBar.vue");
const { useServiceStatus } = await import("../stores/serviceStatus");

const hold = (state: LocalHoldStatus["state"], endBy: string | null = null): LocalHoldStatus => ({
  state, machine: "Mini", owner: "vllm-research", reason: "v13", since: "2026-11-24T08:00:00", model: null, granted_by: null,
  end_requested_by: endBy, error: "", used_mib: null, total_mib: null,
});

beforeEach(() => {
  setActivePinia(createPinia());
  api.mockReset();
  api.mockImplementation(async () => new Promise(() => {})); // the bar's own asking never answers: the test sets the hold
});

test("no pill without a hold", async () => {
  const status = useServiceStatus();
  const wrapper = mount(ServiceStatusBar, { global: { stubs: { HelpButton: true } } });
  for (const state of ["free", "off", "unreachable"] as const) {
    status.localHold = hold(state);
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".hold-pill").exists(), state).toBe(false);
  }
});

test("every hold as the pill in the summary, with owner, state word, and a pulsing dot only while undecided", async () => {
  const status = useServiceStatus();
  const wrapper = mount(ServiceStatusBar, { global: { stubs: { HelpButton: true } } });
  const cases = [["requested", null, "gpu_hold_short_requested", true], ["granted", null, "gpu_hold_short_granted", false],
    ["active", null, "gpu_hold_short_active", false], ["active", "Peuqui", "gpu_hold_short_ending", false]] as const;
  for (const [state, endBy, word, pulses] of cases) {
    status.localHold = hold(state, endBy);
    await wrapper.vm.$nextTick();
    const pill = wrapper.find("summary .hold-pill");
    expect(pill.text(), state).toBe(`vllm-research${word}`);
    expect(pill.find(".pill-dot").classes("pending"), state).toBe(pulses);
  }
});
