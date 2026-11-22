// The wait for the cards in the chat's line (lib/turnProgress.ts, TurnProgress.vue): the text per state and the button
// that ends the hold, active only for this machine's own active hold, else greyed out with its reason as visible text.
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, test, vi } from "vitest";
import type { GpuHoldDetail } from "../lib/turnProgress";

vi.mock("../i18n", () => ({
  language: { value: "de" },
  t: (key: string, values: Record<string, string> = {}) => `${key}${Object.keys(values).length ? ` ${JSON.stringify(values)}` : ""}`,
  tCount: (key: string) => key,
}));

const api = vi.fn().mockResolvedValue({});
vi.mock("../api/client", () => ({ api: (...args: unknown[]) => api(...args), ApiError: class extends Error {} }));

const { progressLine } = await import("../lib/turnProgress");
const { default: TurnProgress } = await import("./TurnProgress.vue");

const HOLD: GpuHoldDetail = {
  machine: "Mini", owner: "vllm-research", reason: "v13", since: "2026-11-19T15:55:00+01:00", state: "active", local: true, end_requested_by: null,
};
const line = (hold: Partial<GpuHoldDetail>) =>
  progressLine(true, { phase: "gpu_hold", current: 0, total: 0, failed: 0, detail: { ...HOLD, ...hold } }, true)!;

beforeEach(() => setActivePinia(createPinia()));

test("an active hold names the machine, the holder, the reason and the time", () => {
  const shown = line({});
  expect(shown.text).toMatch(/^waiting_for_gpu_hold_active /);
  expect(JSON.parse(shown.text.slice("waiting_for_gpu_hold_active ".length))).toMatchObject({
    machine: "Mini", owner: "vllm-research", reason: "v13",
  });
  expect(shown.text).toMatch(/"time":"\d\d:\d\d"/);
});

test("a granted hold has its own text", () => {
  expect(line({ state: "granted" }).text).toMatch(/^waiting_for_gpu_hold_granted /);
});

test("a gpu_hold phase without its detail is an error", () => {
  expect(() => progressLine(true, { phase: "gpu_hold", current: 0, total: 0, failed: 0 }, true)).toThrow("detail");
});

test("the end button is active for this machine's active hold, release now greyed out until the end is requested", () => {
  expect(line({}).end).toEqual({
    action: { verb: "end", service: "host", item: "vllm-research" },
    release: { verb: "release_now", service: "host", item: "vllm-research", blocked: "service_status_local_release_needs_request" },
    reason: "v13",
  });
});

test("an end request greys out the request with its reason and frees release now", () => {
  const end = line({ end_requested_by: "ki" }).end;
  expect(end?.action.blocked).toBe('service_status_local_end_waiting {"by":"ki","owner":"vllm-research"}');
  expect(end?.release.blocked).toBeUndefined();
});

test("another machine's hold cannot be ended here: greyed out with the machine as reason", () => {
  const end = line({ local: false, machine: "Aragon" }).end;
  expect(end?.action.blocked).toBe('waiting_for_gpu_hold_only_local {"machine":"Aragon"}');
  expect(end?.release.blocked).toBe('waiting_for_gpu_hold_only_local {"machine":"Aragon"}');
});

test("a granted hold can be ended as well", () => {
  expect(line({ state: "granted" }).end?.action.blocked).toBeUndefined();
});

test("other phases have no end button", () => {
  expect(progressLine(true, { phase: "compress", current: 0, total: 0, failed: 0 }, true)?.end).toBeUndefined();
});

test("the component shows the button enabled, and the greyed-out one with its reason as text", () => {
  const active = mount(TurnProgress, { props: { line: line({}) } });
  expect(active.find(".end-hold").attributes("disabled")).toBeUndefined();
  expect(active.find(".release-hold").attributes("disabled")).toBeDefined();
  expect(active.find(".hint").text()).toBe("service_status_local_release_needs_request");
  const blocked = mount(TurnProgress, { props: { line: line({ local: false, machine: "Aragon" }) } });
  expect(blocked.find(".end-hold").attributes("disabled")).toBeDefined();
  expect(blocked.findAll(".hint")).toHaveLength(1); // the one reason for both buttons, once
  expect(blocked.find(".hint").text()).toContain("waiting_for_gpu_hold_only_local");
});

test("the button asks first and ends the hold of its owner through the store's one action", async () => {
  api.mockClear();
  const confirm = vi.spyOn(window, "confirm");
  const wrapper = mount(TurnProgress, { props: { line: line({}) } });
  confirm.mockReturnValueOnce(false);
  await wrapper.find(".end-hold").trigger("click");
  expect(api).not.toHaveBeenCalled();
  confirm.mockReturnValueOnce(true);
  await wrapper.find(".end-hold").trigger("click");
  expect(confirm).toHaveBeenLastCalledWith('service_status_local_end_confirm {"owner":"vllm-research","reason":"v13"}');
  await vi.waitFor(() => expect(api).toHaveBeenCalledWith("service-status/end-hold", { method: "POST", body: '{"owner":"vllm-research"}' }));
});

test("once the end is requested the end button waits with its reason and release now asks harder, then ends at once", async () => {
  api.mockClear();
  const wrapper = mount(TurnProgress, { props: { line: line({ end_requested_by: "ki" }) } });
  expect(wrapper.find(".end-hold").attributes("disabled")).toBeDefined();
  expect(wrapper.find(".hint").text()).toContain("service_status_local_end_waiting");
  const confirm = vi.spyOn(window, "confirm");
  confirm.mockReturnValueOnce(false);
  await wrapper.find(".release-hold").trigger("click");
  expect(api).not.toHaveBeenCalled();
  confirm.mockReturnValueOnce(true);
  await wrapper.find(".release-hold").trigger("click");
  expect(confirm).toHaveBeenLastCalledWith('service_status_local_release_now_confirm {"owner":"vllm-research"}');
  await vi.waitFor(() => expect(api).toHaveBeenCalledWith("service-status/release-hold", { method: "POST", body: '{"owner":"vllm-research"}' }));
});
