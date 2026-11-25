// The switch "keep a file" (Akte) in the job form: a checkbox with its explanation as text in the form (not as a
// tooltip: a phone has no mouse-over), taken from the job, and sent back with it on saving.
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, test, vi } from "vitest";
import type { JobOptions, JobRow } from "../api/types";

const api = vi.fn(async (path: string, _init?: { method?: string; body?: string }): Promise<unknown> => (path === "jobs/preview" ? { next_run: "2099-01-01T08:00" } : []));
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, api: (path: string, init?: { method?: string; body?: string }) => api(path, init) }));
vi.mock("../i18n", () => ({ t: (key: string) => key, tCount: (key: string) => key }));
vi.mock("./ModalDialog.vue", () => ({ default: { template: "<div><slot /></div>" } }));

const { default: JobForm } = await import("./JobForm.vue");

const options = {
  schedule_types: ["cron"], delivery_modes: ["review"], audio_types: [], tiers: [1], default_tier: 1, cron_presets: [],
  agents: [{ id: "aifred", label: "AIfred" }], channels: [], rooms_channels: [], column_types: ["text"],
} as unknown as JobOptions;
const job = (keeps_notes: boolean): JobRow =>
  ({ job_id: 7, name: "Suche", agent: "aifred", message: "Fasse zusammen", delivery: "review", channel: "", recipient: "", webhook_url: "",
    fixed_text: false, audio_type: "notification", max_tier: 1, keeps_notes, schedule_type: "cron", schedule_expr: "0 8 * * *", ends_at: "",
    frame: "", parts: [], columns: [] }) as unknown as JobRow;

beforeEach(() => {
  setActivePinia(createPinia());
  api.mockClear();
});

const open = async (existing: JobRow | null) => {
  const wrapper = mount(JobForm, { props: { job: existing, options } });
  await flushPromises();
  return wrapper;
};
const savedBody = (): Record<string, unknown> => {
  const saved = api.mock.calls.find(([path, init]) => path.startsWith("jobs") && (init?.method === "POST" || init?.method === "PUT") && path !== "jobs/preview");
  return JSON.parse(String(saved?.[1]?.body));
};

test("the switch shows its explanation as text in the form, not only as a tooltip", async () => {
  const wrapper = await open(job(true));
  const box = wrapper.get("[data-test=keeps-notes]");
  expect(wrapper.text()).toContain("sched_label_keeps_notes");
  expect(wrapper.get("[data-test=keeps-notes-hint]").text()).toBe("sched_hint_keeps_notes");
  expect(box.attributes("title")).toBeUndefined();
  expect(wrapper.get("[data-test=keeps-notes-hint]").attributes("title")).toBeUndefined();
});

test.each([true, false])("a job with the switch %s shows it so and sends it back unchanged", async (keeps) => {
  const wrapper = await open(job(keeps));
  expect((wrapper.get("[data-test=keeps-notes]").element as HTMLInputElement).checked).toBe(keeps);
  await wrapper.get("form").trigger("submit");
  await flushPromises();
  expect(savedBody().keeps_notes).toBe(keeps);
});

test("a new job starts without a file, and the switch is sent as it is set", async () => {
  const wrapper = await open(null);
  expect((wrapper.get("[data-test=keeps-notes]").element as HTMLInputElement).checked).toBe(false);
  await wrapper.get("[data-test=keeps-notes]").setValue(true);
  await wrapper.get("input").setValue("Neu");
  await wrapper.get("textarea").setValue("Mach etwas");
  await wrapper.get("form").trigger("submit");
  await flushPromises();
  expect(savedBody().keeps_notes).toBe(true);
});
