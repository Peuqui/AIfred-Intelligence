// The scheduler page: every scheduled job and what a job can be set to. Every change answers with the jobs again.
import { defineStore } from "pinia";
import { ref } from "vue";
import { ApiError, api } from "../api/client";
import type { JobOptions, JobRow, JobRun, JobWrite } from "../api/types";
import { onResourceChanged } from "../lib/resourceChanges";

export const useJobs = defineStore("jobs", () => {
  const jobs = ref<JobRow[]>([]);
  const options = ref<JobOptions | null>(null);
  const notice = ref("");

  async function call(work: () => Promise<unknown>): Promise<boolean> {
    notice.value = "";
    try {
      await work();
      jobs.value = await api<JobRow[]>("jobs");
      return true;
    } catch (error) {
      notice.value = error instanceof ApiError ? error.detail : String(error);
      return false;
    }
  }

  const load = (): Promise<boolean> => call(async () => {
    options.value = await api<JobOptions>("jobs/options");
  });
  const body = (job: JobWrite): string => JSON.stringify(job);
  const save = (id: number | null, job: JobWrite): Promise<boolean> =>
    call(() => (id === null ? api("jobs", { method: "POST", body: body(job) }) : api(`jobs/${id}`, { method: "PUT", body: body(job) })));
  const switchJob = (id: number, enabled: boolean): Promise<boolean> =>
    call(() => api(`jobs/${id}`, { method: "PATCH", body: JSON.stringify({ enabled }) }));
  const remove = (id: number): Promise<boolean> => call(() => api(`jobs/${id}`, { method: "DELETE" }));
  const runs = (id: number): Promise<JobRun[]> => api<JobRun[]>(`jobs/${id}/runs`);

  // A job changed or ran (an agent's tool, another tab, the scheduler): the list is read anew, where it stands.
  onResourceChanged("jobs", async () => {
    try {
      jobs.value = await api<JobRow[]>("jobs");
    } catch (error) {
      notice.value = error instanceof ApiError ? error.detail : String(error);
    }
  });

  // When an expression fires next; its reason when it never does.
  async function preview(scheduleType: string, scheduleExpr: string): Promise<{ next?: string; problem?: string }> {
    try {
      const answer = await api<{ next_run: string }>("jobs/preview", {
        method: "POST",
        body: JSON.stringify({ schedule_type: scheduleType, schedule_expr: scheduleExpr }),
      });
      return { next: answer.next_run };
    } catch (error) {
      return { problem: error instanceof ApiError ? error.detail : String(error) };
    }
  }

  return { jobs, options, notice, load, save, switchJob, remove, runs, preview };
});
