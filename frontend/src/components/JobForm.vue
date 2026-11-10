<script setup lang="ts">
// A scheduled job, new or changed: name, agent, what it says, when (a cron template or the five fields, an
// interval, a point in time; the server says when it fires next before saving) and where the result goes
// (review, sent through a channel, possibly as a fixed text without a model, or a webhook) with its tier.
import { computed, ref, watch } from "vue";
import type { JobOptions, JobRow, JobWrite } from "../api/types";
import { t } from "../i18n";
import { type ScheduleFields, type ScheduleType, composeSchedule, decomposeSchedule } from "../lib/schedule";
import { useJobs } from "../stores/jobs";
import ModalDialog from "./ModalDialog.vue";

const props = defineProps<{ job: JobRow | null; options: JobOptions }>();
const emit = defineEmits<{ close: [] }>();
const jobs = useJobs();

const tomorrow = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
const START: ScheduleFields = {
  type: "cron", minute: "0", hour: "8", dom: "*", month: "*", dow: "*",
  intervalValue: 1, intervalUnit: "hours", date: tomorrow, time: "08:00",
};
const schedule = ref<ScheduleFields>(
  props.job ? decomposeSchedule(START, props.job.schedule_type as ScheduleType, props.job.schedule_expr) : { ...START },
);
const form = ref({
  name: props.job?.name ?? "",
  agent: props.job?.agent ?? props.options.agents[0]?.id ?? "",
  message: props.job?.message ?? "",
  delivery: props.job?.delivery ?? "review",
  channel: props.job?.channel ?? "",
  recipient: props.job?.recipient ?? "",
  webhook_url: props.job?.webhook_url ?? "",
  fixed_text: props.job?.fixed_text ?? false,
  audio_type: props.job?.audio_type ?? "notification",
  max_tier: props.job?.max_tier ?? 1,
});
// The end of a cron or interval schedule, optional: a day, and a time if the last day is to end earlier than its evening.
const [startEndDate = "", startEndTime = ""] = (props.job?.ends_at ?? "").split("T");
const endDate = ref(startEndDate);
const endTime = ref(startEndTime.slice(0, 5));
const endsAt = computed(() => (schedule.value.type === "once" || !endDate.value ? "" : endTime.value ? `${endDate.value}T${endTime.value}` : endDate.value));
const inRooms = computed(() => props.options.rooms_channels.includes(form.value.channel));

function preset(key: string): void {
  const chosen = props.options.cron_presets.find((candidate) => candidate.key === key);
  if (chosen) schedule.value = { ...schedule.value, minute: chosen.minute, hour: chosen.hour, dom: chosen.dom, month: chosen.month, dow: chosen.dow };
}

// When the schedule fires next, asked again whenever it changes.
const next = ref("");
const problem = ref("");
async function ask(): Promise<void> {
  const answer = await jobs.preview(schedule.value.type, composeSchedule(schedule.value));
  next.value = answer.next ?? "";
  problem.value = answer.problem ?? "";
}
watch(schedule, ask, { deep: true, immediate: true });

async function save(): Promise<void> {
  const job: JobWrite = { ...form.value, schedule_type: schedule.value.type, schedule_expr: composeSchedule(schedule.value), ends_at: endsAt.value };
  if (await jobs.save(props.job?.job_id ?? null, job)) emit("close");
}
</script>

<template>
  <ModalDialog @close="emit('close')">
    <form class="job" @submit.prevent="save">
      <strong class="title">⏰ {{ job ? job.name : t("scheduler_new_job") }}</strong>
      <p v-if="jobs.notice" class="notice">⚠️ {{ jobs.notice }}</p>
      <label>{{ t("agent_editor_name") }}<input v-model="form.name" required /></label>
      <label>{{ t("jobs_agent") }}
        <select v-model="form.agent">
          <option v-for="agent in options.agents" :key="agent.id" :value="agent.id">{{ agent.label }}</option>
        </select>
      </label>

      <fieldset class="wide">
        <legend>{{ t("sched_label_schedule") }}</legend>
        <div class="row">
          <select v-model="schedule.type" :title="t(`sched_tip_type_${schedule.type}`)">
            <option v-for="type in options.schedule_types" :key="type" :value="type">{{ t(`sched_type_${type}`) }}</option>
          </select>
          <template v-if="schedule.type === 'cron'">
            <select :title="t('sched_cron_preset')" @change="preset(($event.target as HTMLSelectElement).value)">
              <option value="">{{ t("sched_cron_preset") }} …</option>
              <option v-for="chosen in options.cron_presets" :key="chosen.key" :value="chosen.key">{{ t(chosen.key) }}</option>
            </select>
            <label class="cron">{{ t("sched_cron_minute") }}<input v-model="schedule.minute" /></label>
            <label class="cron">{{ t("sched_cron_hour") }}<input v-model="schedule.hour" /></label>
            <label class="cron">{{ t("sched_cron_dom") }}<input v-model="schedule.dom" /></label>
            <label class="cron">{{ t("sched_cron_month") }}<input v-model="schedule.month" /></label>
            <label class="cron">{{ t("sched_cron_dow") }}<input v-model="schedule.dow" /></label>
          </template>
          <template v-else-if="schedule.type === 'interval'">
            <span>{{ t("sched_interval_every") }}</span>
            <input v-model.number="schedule.intervalValue" type="number" min="1" class="number" />
            <select v-model="schedule.intervalUnit">
              <option v-for="unit in ['minutes', 'hours', 'days']" :key="unit" :value="unit">{{ t(`sched_interval_${unit}`) }}</option>
            </select>
          </template>
          <template v-else>
            <label class="cron">{{ t("sched_once_date") }}<input v-model="schedule.date" type="date" /></label>
            <label class="cron">{{ t("sched_once_time") }}<input v-model="schedule.time" type="time" /></label>
          </template>
        </div>
        <div v-if="schedule.type !== 'once'" class="row" :title="t('sched_end_hint')">
          <span>{{ t("sched_label_end") }}</span>
          <label class="cron">{{ t("sched_end_date") }}<input v-model="endDate" type="date" /></label>
          <label class="cron">{{ t("sched_end_time") }}<input v-model="endTime" type="time" :disabled="!endDate" /></label>
        </div>
        <p v-if="problem" class="notice">⚠️ {{ problem }}</p>
        <p v-else-if="next" class="next">{{ t("sched_next") }}: {{ next.replace("T", " ") }}</p>
      </fieldset>

      <fieldset class="wide">
        <legend>{{ t("sched_label_delivery") }}</legend>
        <div class="row">
          <select v-model="form.delivery" :title="t(`sched_tip_delivery_${form.delivery}`)">
            <option v-for="mode in options.delivery_modes" :key="mode" :value="mode">{{ t(`sched_delivery_${mode}`) }}</option>
          </select>
          <template v-if="form.delivery === 'announce'">
            <label class="cron">{{ t("sched_label_channel") }}
              <select v-model="form.channel" required>
                <option v-for="channel in options.channels" :key="channel.id" :value="channel.id">{{ channel.label }}</option>
              </select>
            </label>
            <label class="grow">{{ t("sched_label_recipient") }}<input v-model="form.recipient" :placeholder="t('sched_recipient_placeholder')" /></label>
            <label v-if="inRooms" class="cron">🔔
              <select v-model="form.audio_type">
                <option v-for="audio in options.audio_types" :key="audio" :value="audio">{{ t(`sched_audio_${audio}`) }}</option>
              </select>
            </label>
          </template>
          <label v-else-if="form.delivery === 'webhook'" class="grow">{{ t("jobs_webhook_url") }}<input v-model="form.webhook_url" type="url" required /></label>
          <label class="cron" :title="t(`sched_tip_tier_${form.max_tier}`)">{{ t("sched_label_tier") }}
            <select v-model.number="form.max_tier">
              <option v-for="tier in options.tiers" :key="tier" :value="tier">{{ tier }} · {{ t(`sched_tip_tier_${tier}`) }}</option>
            </select>
          </label>
        </div>
        <label v-if="form.delivery === 'announce'" class="check" :title="t('sched_tip_fixed_text')">
          <input v-model="form.fixed_text" type="checkbox" /> {{ t("sched_label_fixed_text") }}
        </label>
      </fieldset>

      <label class="wide">{{ form.fixed_text ? t("sched_label_message_fixed") : t("sched_label_message") }}
        <textarea v-model="form.message" required></textarea>
      </label>
      <div class="actions wide">
        <button type="button" @click="emit('close')">{{ t("agent_editor_cancel") }}</button>
        <button type="submit" :disabled="!!problem">{{ t("agent_editor_save") }}</button>
      </div>
    </form>
  </ModalDialog>
</template>

<style scoped>
.job { width: min(820px, calc(88 * var(--vw))); display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.title, .notice, .wide { grid-column: 1 / -1; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 0.85rem; color: var(--aifred-text-secondary); }
label.check { flex-direction: row; align-items: center; margin-top: 6px; }
fieldset { border: 1px solid var(--aifred-border); border-radius: 8px; padding: 8px 10px; display: flex; flex-direction: column; gap: 6px; }
legend { font-size: 0.85rem; padding: 0 4px; }
.row { display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-end; }
.cron input { width: 6em; }
.grow { flex: 1; min-width: 12em; }
.number { width: 5em; }
button, input:not([type="checkbox"]), select, textarea { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 4px 8px; font: inherit; }
button { cursor: pointer; }
button:disabled { opacity: 0.5; cursor: default; }
textarea { min-height: 7em; resize: vertical; }
.notice { margin: 0; color: var(--aifred-accent-amber); }
.next { margin: 0; font-size: 0.85rem; color: var(--aifred-accent-cyan); }
.actions { display: flex; gap: 8px; justify-content: flex-end; }
@media (max-width: 760px) {
  .job { grid-template-columns: 1fr; }
}
</style>
