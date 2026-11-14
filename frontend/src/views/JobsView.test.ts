// The jobs page's run-now button: the endpoint it calls, disabled while the call runs, and the
// server's refusal text at 409 (shown like every other error, in the page's notice).

import { flushPromises, mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

const requests: { path: string; init?: RequestInit; answer: (value: unknown) => void; refuse: (error: unknown) => void }[] = [];

vi.mock("../api/client", () => ({
  ApiError: class extends Error {
    constructor(
      readonly status: number,
      readonly detail: string,
    ) {
      super(`${status}: ${detail}`);
    }
  },
  api: (path: string, init?: RequestInit) =>
    new Promise((resolve, refuse) => requests.push({ path, init, answer: resolve, refuse })),
}));

const { ApiError } = await import("../api/client");
import JobsView from "./JobsView.vue";

const job = {
  job_id: 1, name: "Morgengruß", enabled: true, delivery: "review", schedule_type: "cron",
  schedule_expr: "0 8 * * *", next_run: "2026-11-15T08:00:00", last_run: "",
};

// The view's mount reads the options and the list; both stay open until answered here.
function mountView(): ReturnType<typeof mount> {
  setActivePinia(createPinia());
  return mount(JobsView);
}

// Every answered change makes the store read the list anew (options, the list, after a run too),
// so the open requests are answered in rounds.
async function answerAll(): Promise<void> {
  for (let round = 0; round < 3; round++) {
    for (const request of [...requests]) {
      if (request.path === "jobs/options") request.answer({ agents: [], channels: [], rooms_channels: [] });
      if (request.path === "jobs") request.answer([job]);
    }
    await flushPromises();
  }
}

function runNowButton(view: ReturnType<typeof mount>) {
  const button = view.findAll("button").find((candidate) => candidate.text().includes("job_run_now"));
  if (!button) throw new Error("the run-now button is not on the page");
  return button;
}

describe("the run-now button", () => {
  beforeEach(() => {
    requests.length = 0;
  });

  it("calls the run endpoint and is disabled until the 202 is there", async () => {
    const view = mountView();
    await answerAll();
    const button = runNowButton(view);
    expect(button.attributes("disabled")).toBeUndefined();

    await button.trigger("click");
    expect(button.attributes("disabled")).toBeDefined();   // while the call runs
    const sent = requests.find((request) => request.path === "jobs/1/run");
    expect(sent).toBeDefined();
    expect(sent?.init?.method).toBe("POST");

    sent?.answer({ job_id: 1, name: "Morgengruß" });
    await answerAll();       // the store reads the list anew after the change, before runNow returns
    await nextTick();
    expect(button.attributes("disabled")).toBeUndefined();  // enabled again after the answer
  });

  it("shows the server's refusal text at 409", async () => {
    const view = mountView();
    await answerAll();
    const button = runNowButton(view);

    await button.trigger("click");
    const sent = requests.find((request) => request.path === "jobs/1/run");
    sent?.refuse(new ApiError(409, "Der Auftrag „Morgengruß“ läuft gerade schon."));
    await flushPromises();
    expect(view.find(".notice").text()).toContain("läuft gerade schon");
  });
});
