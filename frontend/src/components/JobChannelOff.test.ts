// A job whose channel is switched off stays visible: the form shows the channel marked, a new choice does not offer it;
// the channel list is read anew when the plugins change.
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, test, vi } from "vitest";
import type { JobOptions, JobRow } from "../api/types";
import { resourceChanged } from "../lib/resourceChanges";

let options: JobOptions;
const api = vi.fn(async (path: string) => (path === "jobs/options" ? options : []));
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, api: (path: string) => api(path) }));
vi.mock("../i18n", () => ({ t: (key: string, values?: { channel?: string }) => (values?.channel ? `${key}:${values.channel}` : key) }));
vi.mock("./ModalDialog.vue", () => ({ default: { template: "<div><slot /></div>" } }));

const { default: JobForm } = await import("./JobForm.vue");
const { useJobs } = await import("../stores/jobs");

const withChannels = (ids: string[]): JobOptions =>
  ({
    schedule_types: [], delivery_modes: ["announce"], audio_types: [], tiers: [], default_tier: "", cron_presets: [], agents: [],
    channels: ids.map((id) => ({ id, label: id.toUpperCase() })), rooms_channels: [],
  }) as unknown as JobOptions;
const job = (channel: string): JobRow =>
  ({ job_id: 1, name: "n", agent: "a", message: "", delivery: "announce", channel, recipient: "", webhook_url: "", fixed_text: false,
    audio_type: "notification", max_tier: "", schedule_type: "interval", schedule_expr: "1h", ends_at: null, frame: "", parts: [], columns: [], trend: null }) as unknown as JobRow;

beforeEach(() => {
  setActivePinia(createPinia());
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  options = withChannels(["mail"]);
  api.mockClear();
});

async function optionTexts(existing: JobRow | null): Promise<string[]> {
  const jobs = useJobs();
  await jobs.load();
  const wrapper = mount(JobForm, { props: { job: existing, options: jobs.options as JobOptions } });
  if (!existing) await wrapper.findAll("select").find((select) => select.find("option[value=announce]").exists())?.setValue("announce");
  await flushPromises();
  return wrapper.findAll("select[required] option").map((option) => option.text());
}

test("an existing job with a channel that is off shows it marked, changeable to the channels that are on", async () => {
  expect(await optionTexts(job("echo"))).toEqual(["MAIL", "channel_off_marker:echo"]);
});

test("a new job and a job with a channel that is on are not offered the one that is off", async () => {
  expect(await optionTexts(null)).toEqual(["MAIL"]);
  expect(await optionTexts(job("mail"))).toEqual(["MAIL"]);
});

test("the channel list is read anew at the event plugins, once the page has read it", async () => {
  const jobs = useJobs();
  resourceChanged("plugins");
  await vi.advanceTimersByTimeAsync(400);
  expect(jobs.options).toBeNull();
  await jobs.load();
  options = withChannels(["mail", "echo"]);
  resourceChanged("plugins");
  await vi.advanceTimersByTimeAsync(400);
  expect(jobs.options?.channels.map((channel) => channel.id)).toEqual(["mail", "echo"]);
  expect(jobs.channelLabel("echo")).toBe("echo");
});
