<script setup lang="ts">
// The jobs page: every scheduled job with its switch, agent, schedule, next and last run; new, change, its
// recent runs, delete. A job of an agent switched off fails at its time (the scheduler says why).
import { computed, onMounted, ref } from "vue";
import type { JobRow, JobRun } from "../api/types";
import JobForm from "../components/JobForm.vue";
import ModalDialog from "../components/ModalDialog.vue";
import { t } from "../i18n";
import { useJobs } from "../stores/jobs";

const jobs = useJobs();
onMounted(() => void jobs.load());

const agentLabel = computed(() => new Map((jobs.options?.agents ?? []).map((agent) => [agent.id, agent.label])));
const when = (stamp: string | null): string => (stamp ? stamp.slice(0, 16).replace("T", " ") : "–");

const editing = ref<{ job: JobRow | null } | null>(null);
const deleting = ref<JobRow | null>(null);
async function confirmDelete(): Promise<void> {
  if (!deleting.value) return;
  const id = deleting.value.job_id;
  deleting.value = null;
  await jobs.remove(id);
}

const runs = ref<{ job: JobRow; runs: JobRun[] } | null>(null);
async function showRuns(job: JobRow): Promise<void> {
  runs.value = { job, runs: await jobs.runs(job.job_id) };
}
</script>

<template>
  <main class="jobs">
    <header class="bar">
      <h2>{{ t("nav_jobs") }}</h2>
      <button v-if="jobs.options" @click="editing = { job: null }">＋ {{ t("scheduler_new_job") }}</button>
    </header>
    <p v-if="jobs.notice && !editing" class="notice">⚠️ {{ jobs.notice }}</p>
    <p v-if="jobs.options && !jobs.jobs.length" class="empty">{{ t("scheduler_no_jobs") }}</p>
    <table v-else-if="jobs.jobs.length" class="list">
      <thead>
        <tr>
          <th></th>
          <th>{{ t("agent_editor_name") }}</th>
          <th>{{ t("jobs_agent") }}</th>
          <th>{{ t("sched_label_schedule") }}</th>
          <th>{{ t("sched_next") }}</th>
          <th>{{ t("sched_last") }}</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="job in jobs.jobs" :key="job.job_id" :class="{ off: !job.enabled }">
          <td><input type="checkbox" :checked="job.enabled" @change="jobs.switchJob(job.job_id, !job.enabled)" /></td>
          <td class="name">{{ job.name }}<span class="delivery">{{ t(`sched_delivery_${job.delivery}`) }}{{ job.channel ? ` · ${job.channel}` : "" }}</span></td>
          <td>{{ agentLabel.get(job.agent) ?? job.agent }}</td>
          <td><span class="type">{{ t(`sched_type_${job.schedule_type}`) }}</span> <code>{{ job.schedule_expr }}</code></td>
          <td class="stamp">{{ job.enabled ? when(job.next_run) : "–" }}</td>
          <td class="stamp">{{ when(job.last_run) }}</td>
          <td class="row-actions">
            <button :title="t('jobs_runs')" @click="showRuns(job)">📜</button>
            <button :title="t('agent_editor_edit')" @click="editing = { job }">✏️</button>
            <button :title="t('agent_editor_delete')" @click="deleting = job">🗑️</button>
          </td>
        </tr>
      </tbody>
    </table>

    <JobForm v-if="editing && jobs.options" :job="editing.job" :options="jobs.options" @close="editing = null" />

    <ModalDialog v-if="deleting" @close="deleting = null">
      <strong>⏰ {{ deleting.name }}: {{ t("scheduler_delete_confirm") }}</strong>
      <div class="dialog-actions">
        <button @click="deleting = null">{{ t("agent_editor_cancel") }}</button>
        <button class="danger" @click="confirmDelete">{{ t("agent_editor_delete") }}</button>
      </div>
    </ModalDialog>

    <ModalDialog v-if="runs" @close="runs = null">
      <div class="runs">
        <strong>📜 {{ runs.job.name }}: {{ t("jobs_runs") }}</strong>
        <p v-if="!runs.runs.length" class="empty">–</p>
        <article v-for="run in runs.runs" :key="run.ran_at">
          <span class="stamp">{{ when(run.ran_at) }}</span>
          <p>{{ run.text }}</p>
        </article>
      </div>
    </ModalDialog>
  </main>
</template>

<style scoped>
.jobs { overflow-y: auto; padding: 12px 0; display: flex; flex-direction: column; gap: 12px; }
.bar { display: flex; align-items: center; gap: 14px; }
.bar button { margin-left: auto; }
h2 { margin: 0; font-size: 1rem; }
button { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 4px 8px; font: inherit; cursor: pointer; }
.danger { border-color: var(--aifred-danger); color: var(--aifred-danger); }
.notice { margin: 0; color: var(--aifred-accent-amber); }
.empty { margin: 0; color: var(--aifred-text-muted); }
.list { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
.list th { text-align: left; font-weight: 500; font-size: 0.75rem; color: var(--aifred-text-muted); padding: 4px 6px; }
.list td { padding: 6px; border-top: 1px solid var(--aifred-border); vertical-align: middle; }
.list tr.off { opacity: 0.55; }
.name { overflow-wrap: anywhere; }
.delivery { display: block; font-size: 0.75rem; color: var(--aifred-text-muted); }
.type { font-size: 0.75rem; color: var(--aifred-accent-cyan); }
.stamp { font-size: 0.8rem; color: var(--aifred-text-secondary); white-space: nowrap; }
.row-actions { white-space: nowrap; text-align: right; }
.row-actions button { background: none; border: none; padding: 2px 4px; }
input[type="checkbox"] { accent-color: var(--aifred-primary); }
.dialog-actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 10px; }
.runs { width: min(760px, calc(86 * var(--vw))); max-height: calc(70 * var(--dvh)); overflow-y: auto; display: flex; flex-direction: column; gap: 10px; }
.runs p { margin: 4px 0 0; white-space: pre-wrap; font-size: 0.85rem; }
@media (max-width: 760px) {
  .list th:nth-child(3), .list td:nth-child(3), .list th:nth-child(6), .list td:nth-child(6) { display: none; }
}
</style>
