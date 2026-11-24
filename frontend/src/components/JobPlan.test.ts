// The sub-tasks in the job form: a job's plan is shown and goes back to the server as it was; parts and positions
// are added and removed; a job with parts cannot be a fixed text, and the page says why.
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
  schedule_types: ["cron"], delivery_modes: ["review", "announce"], audio_types: [], tiers: [1], default_tier: 1, cron_presets: [],
  agents: [{ id: "aifred", label: "AIfred" }], channels: [{ id: "mail", label: "Mail" }], rooms_channels: [],
  column_types: ["text", "zahl", "link"], trend_column_type: "zahl",
} as unknown as JobOptions;

const PLAN = {
  frame: "Nur Shops mit Versand nach Deutschland.",
  parts: [
    { id: "hpa", title: "HPA", task: "Preise suchen", positions: [{ id: "a1", name: "Adapter" }, { id: "a2", name: "Schlauch" }] },
    { id: "gun", title: "Pistole", task: "Shops prüfen", positions: [{ id: "g1", name: "Pistole" }] },
  ],
  columns: [{ name: "preis", type: "zahl" }, { name: "shop", type: "text" }],
  trend: { value: "preis" },
};
const job = (extra: Partial<JobRow> = {}): JobRow =>
  ({ job_id: 7, name: "Suche", agent: "aifred", message: "Fasse zusammen", delivery: "review", channel: "", recipient: "", webhook_url: "",
    fixed_text: false, audio_type: "notification", max_tier: 1, schedule_type: "cron", schedule_expr: "0 8 * * *", ends_at: "",
    frame: "", parts: [], columns: [], trend: null, ...extra }) as unknown as JobRow;
const withPlan = (): JobRow => job(PLAN as unknown as Partial<JobRow>);

beforeEach(() => {
  setActivePinia(createPinia());
  api.mockClear();
});

const open = async (existing: JobRow | null) => {
  const wrapper = mount(JobForm, { props: { job: existing, options } });
  await flushPromises();
  return wrapper;
};

test("a job without a plan offers to add sub-tasks and shows nothing of them", async () => {
  const wrapper = await open(job());
  expect(wrapper.find(".plan").exists()).toBe(false);
  expect(wrapper.text()).not.toContain("jobs_plan_closing");
  await wrapper.get("[data-test=add-plan]").trigger("click");
  expect(wrapper.find(".plan").exists()).toBe(true);
});

test("a job with a plan shows it, calls the message the closing, and sends the plan back unchanged on saving", async () => {
  const wrapper = await open(withPlan());
  expect(wrapper.findAll("[data-test=part]")).toHaveLength(2);
  expect(wrapper.findAll("[data-test=position]")).toHaveLength(3);
  expect(wrapper.findAll("[data-test=column]")).toHaveLength(2);
  expect((wrapper.get("[data-test=trend]").element as HTMLSelectElement).value).toBe("preis");
  expect(wrapper.text()).toContain("jobs_plan_closing_hint");
  await wrapper.get("form").trigger("submit");
  await flushPromises();
  const put = api.mock.calls.find(([path]) => path === "jobs/7");
  expect(put?.[1]?.method).toBe("PUT");
  expect(JSON.parse(put?.[1]?.body ?? "{}")).toMatchObject(PLAN);
});

test("parts and positions are added and removed", async () => {
  const wrapper = await open(withPlan());
  await wrapper.get("[data-test=add-part]").trigger("click");
  expect(wrapper.findAll("[data-test=part]")).toHaveLength(3);
  expect(wrapper.findAll("[data-test=position]")).toHaveLength(4);   // a new part starts with one position
  await wrapper.findAll("[data-test=add-position]")[0]?.trigger("click");
  expect(wrapper.findAll("[data-test=position]")).toHaveLength(5);
  await wrapper.findAll("[data-test=remove-position]")[0]?.trigger("click");
  expect(wrapper.findAll("[data-test=position]")).toHaveLength(4);
  await wrapper.findAll("[data-test=remove-part]")[1]?.trigger("click");
  expect(wrapper.findAll("[data-test=part]")).toHaveLength(2);
});

test("removing the number column of the trend clears the trend", async () => {
  const wrapper = await open(withPlan());
  await wrapper.findAll("[data-test=remove-column]")[0]?.trigger("click");
  expect((wrapper.get("[data-test=trend]").element as HTMLSelectElement).value).toBe("");
});

test("removing the sub-tasks sends a job without a plan", async () => {
  const wrapper = await open(withPlan());
  await wrapper.get("[data-test=remove-plan]").trigger("click");
  expect(wrapper.find(".plan").exists()).toBe(false);
  await wrapper.get("form").trigger("submit");
  await flushPromises();
  const sent = JSON.parse(api.mock.calls.find(([path]) => path === "jobs/7")?.[1]?.body ?? "{}");
  expect(sent).toMatchObject({ frame: "", parts: [], columns: [], trend: null });
});

test("a job with parts cannot be a fixed text, and the page says why; a fixed text cannot get parts", async () => {
  const withParts = await open(job({ ...(PLAN as unknown as Partial<JobRow>), delivery: "announce", channel: "mail" }));
  expect((withParts.get("[data-test=fixed-text]").element as HTMLInputElement).disabled).toBe(true);
  expect(withParts.get("[data-test=fixed-text-blocked]").text()).toBe("jobs_plan_fixed_text_blocked");

  const fixed = await open(job({ delivery: "announce", channel: "mail", fixed_text: true }));
  expect((fixed.get("[data-test=add-plan]").element as HTMLButtonElement).disabled).toBe(true);
  expect(fixed.get("[data-test=add-plan-blocked]").text()).toBe("jobs_plan_add_blocked");
});

test("the server's refusal of the plan shows on the form", async () => {
  const { ApiError } = await import("../api/client");
  api.mockImplementation(async (path: string) => {
    if (path === "jobs/7") throw Object.assign(new ApiError(422, ""), { detail: "Der Teil „hpa“ hat keine Positionen." });
    return { next_run: "2099-01-01T08:00" };
  });
  const wrapper = await open(withPlan());
  await wrapper.get("form").trigger("submit");
  await flushPromises();
  expect(wrapper.get("[role=alert]").text()).toContain("Der Teil „hpa“ hat keine Positionen.");
});
