<script setup lang="ts">
// Calibrating AIfred's model: the matrix of its variants with a tick per cell (llama.cpp: the profiles to calibrate;
// vLLM: the base cell re-measures the operating point, the others judge describer/TTS pairs), the options (mode and
// hybrid for llama.cpp; for vLLM re-measuring the persisted topology only and a dry run), the start after a question
// (a run stops llama-swap and takes every card), its console while it runs (read again every 2 s from the line seen
// last), the cancel, and dropping the measured VLM peaks. The ticks belong to this page; the server keeps the run.
import { CircleCheck, CircleStop, CircleX, Hourglass, Play, Square, Trash2, TriangleAlert } from "lucide-vue-next";
import { type Component, computed, onMounted, onUnmounted, ref, watch } from "vue";
import { ApiError, api } from "../api/client";
import type { CalibrationOverview, CalibrationRunState, MatrixCell } from "../api/types";
import { t } from "../i18n";

const props = defineProps<{ calibration: CalibrationOverview }>();
const emit = defineEmits<{ changed: [] }>();

const ticked = ref<string[]>([]);
const vllmResweep = ref(false);
const vllmDryRun = ref(false);
const confirming = ref(false);
const notice = ref("");
const done = ref("");
const run = ref<CalibrationRunState | null>(null);
const lines = ref<string[]>([]);
let next = 0;
let timer: ReturnType<typeof setTimeout> | undefined;

const backend = computed(() => props.calibration.backend_type);
const matrix = computed(() => props.calibration.matrix);
const baseCalibrated = computed(() => matrix.value?.rows[0]?.cells[0]?.status === "done");
const canStart = computed(() => !run.value?.active && (backend.value === "ollama" || ticked.value.length > 0));

async function call<T>(work: () => Promise<T>): Promise<T | null> {
  notice.value = "";
  try {
    return await work();
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
    return null;
  }
}
// The server's run: its lines from the one seen last; while it runs again in 2 s, at its end the matrix is read again.
async function follow(): Promise<void> {
  clearTimeout(timer);
  const state = await call(() => api<CalibrationRunState>(`calibration/run?after=${next}`));
  if (!state) return;
  const wasRunning = run.value?.running;
  run.value = state;
  lines.value = [...lines.value, ...state.lines];
  next = state.next;
  if (state.active) timer = setTimeout(() => void follow(), 2000);
  else if (wasRunning) emit("changed");
}
onMounted(() => void follow());
onUnmounted(() => clearTimeout(timer));
// Another model or backend: the ticks of the old matrix mean nothing there.
watch(() => [props.calibration.backend_type, props.calibration.model_id], () => { ticked.value = []; });

function toggle(key: string): void {
  ticked.value = ticked.value.includes(key) ? ticked.value.filter((other) => other !== key) : [...ticked.value, key];
}
async function start(): Promise<void> {
  confirming.value = false;
  done.value = "";
  const body = { cells: ticked.value, vllm_resweep: vllmResweep.value && baseCalibrated.value, vllm_dry_run: vllmDryRun.value };
  const state = await call(() => api<CalibrationRunState>("calibration/run", { method: "POST", body: JSON.stringify(body) }));
  if (!state) return;
  lines.value = [];
  next = 0;
  run.value = state;
  await follow();
}
async function cancel(): Promise<void> {
  await call(() => api("calibration/run/cancel", { method: "POST" }));
}
async function setOption(change: { mode?: string; allow_hybrid?: boolean }): Promise<void> {
  if (await call(() => api("calibration/options", { method: "PATCH", body: JSON.stringify(change) }))) emit("changed");
}
async function clearVlmCache(): Promise<void> {
  const answer = await call(() => api<{ cleared: number }>("calibration/vlm-cache", { method: "DELETE" }));
  if (answer) done.value = t("calibration_vlm_cache_cleared", { count: answer.cleared });
}

// The state of the run with its sign: waiting while it runs, then how it ended.
const OUTCOME_ICONS: Record<string, Component> = { done: CircleCheck, cancelled: CircleStop, failed: CircleX };
const runLine = computed(() => {
  const state = run.value;
  if (!state) return null;
  if (state.running) return { icon: Hourglass, text: t("calibration_running_since", { model: state.model_id, time: state.started_at }) };
  if (state.active) return { icon: Hourglass, text: t("calibration_active_elsewhere") };
  if (!state.outcome) return null;
  return { icon: OUTCOME_ICONS[state.outcome], text: t(`calibration_outcome_${state.outcome}`, { model: state.model_id, time: state.started_at }) };
});
// What a cell's dot says: llama.cpp a profile, vLLM the operating point (base cell) or a describer/TTS pair.
function cellTitle(cell: MatrixCell): string {
  if (backend.value !== "vllm") {
    return t({ done: "calibration_already_done", failed: "calibration_cell_failed", open: "calibration_cell_open" }[cell.status]);
  }
  if (cell.key === "|") return t(cell.status === "done" ? "calibration_dot_done" : "calibration_dot_missing");
  return t({ done: "calibration_pair_fits", failed: "calibration_pair_overflows", open: "calibration_not_measured" }[cell.status]);
}
</script>

<template>
  <div class="panel">
    <template v-if="matrix">
      <p class="subtitle">
        {{ backend === "vllm" ? t("calibration_vllm_burnin_title") : t("calibration_matrix_llamacpp_title", { model: calibration.model_id }) }}
      </p>
      <table class="matrix">
        <thead>
          <tr>
            <th></th>
            <th v-for="column in matrix.columns" :key="column.tts_key">{{ column.label }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in matrix.rows" :key="row.vlm_key">
            <th>{{ row.label }}</th>
            <td v-for="cell in row.cells" :key="cell.key">
              <label class="cell" :title="cellTitle(cell)">
                <input type="checkbox" :checked="ticked.includes(cell.key)" :disabled="run?.active" @change="toggle(cell.key)" />
                <span class="dot" :class="cell.status" :aria-label="cellTitle(cell)"></span>
              </label>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="matrix.tts_problem" class="notice"><TriangleAlert :size="16" /> {{ matrix.tts_problem }}</p>
    </template>

    <div v-if="backend === 'llamacpp'" class="line">
      <select :value="calibration.options.mode" :disabled="run?.active" @change="setOption({ mode: ($event.target as HTMLSelectElement).value })">
        <option value="legacy">{{ t("calibration_mode_legacy") }}</option>
        <option value="ai" :disabled="!calibration.options.ai_available">{{ t("calibration_ai_mode", { model: calibration.options.ai_model || "—" }) }}</option>
      </select>
      <label class="option" :title="t('calibration_hybrid_tooltip')">
        <input type="checkbox" :checked="calibration.options.allow_hybrid" :disabled="run?.active" @change="setOption({ allow_hybrid: !calibration.options.allow_hybrid })" />
        {{ t("calibration_hybrid_label") }}
      </label>
    </div>
    <template v-if="backend === 'vllm'">
      <p class="hint">{{ t("calibration_vllm_hint") }}</p>
      <div class="line">
        <label class="option" :title="baseCalibrated ? t('calibration_vllm_resweep_tooltip') : t('calibration_vllm_resweep_needs_point')">
          <input v-model="vllmResweep" type="checkbox" :disabled="!baseCalibrated || run?.active" />
          {{ t("calibration_vllm_resweep_label") }}
        </label>
        <label class="option" :title="t('calibration_vllm_dry_run_tooltip')">
          <input v-model="vllmDryRun" type="checkbox" :disabled="run?.active" />
          {{ t("calibration_vllm_dry_run_label") }}
        </label>
      </div>
    </template>

    <div class="line">
      <template v-if="confirming">
        <span>{{ t("calibration_start_confirm") }}</span>
        <button class="danger" @click="start">{{ t("confirm_yes") }}</button>
        <button @click="confirming = false">{{ t("db_cancel") }}</button>
      </template>
      <button v-else :disabled="!canStart" @click="confirming = true"><Play :size="16" /> {{ t("calibration_start") }}</button>
      <button v-if="run?.active" class="danger" @click="cancel"><Square :size="16" /> {{ t("calibration_cancel") }}</button>
      <button v-if="backend !== 'ollama'" :disabled="run?.active" :title="t('calibration_reset_vlm_cache_tooltip')" @click="clearVlmCache">
        <Trash2 :size="16" /> {{ t("calibration_reset_vlm_cache_button") }}
      </button>
    </div>
    <p v-if="notice" class="notice"><TriangleAlert :size="16" /> {{ notice }}</p>
    <p v-if="done" class="done">{{ done }}</p>
    <p v-if="runLine" class="run"><component :is="runLine.icon" :size="16" /> {{ runLine.text }}</p>
    <pre v-if="lines.length" class="console">{{ lines.join("\n") }}</pre>
  </div>
</template>

<style scoped>
.panel { display: flex; flex-direction: column; gap: 8px; }
.line { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.hint { color: var(--aifred-text-secondary); font-size: 0.85rem; margin: 0; }
.notice { margin: 0; color: var(--aifred-accent-amber); }
.done, .run { margin: 0; color: var(--aifred-accent-success); }
.subtitle { font-weight: 600; font-size: 0.85rem; margin: 12px 0 0; }
select, button { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 4px 8px; font: inherit; }
button { cursor: pointer; }
button:disabled, select:disabled { opacity: 0.5; cursor: default; }
button.danger { border-color: var(--aifred-danger); color: var(--aifred-danger); }
.option { display: inline-flex; align-items: center; gap: 6px; font-size: 0.85rem; }
input[type="checkbox"] { accent-color: var(--aifred-primary); }
.matrix { border-collapse: collapse; font-size: 0.8rem; }
.matrix th, .matrix td { padding: 4px 8px; text-align: center; }
.matrix tbody th { text-align: left; font-weight: 600; word-break: break-all; }
.cell { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; }
.dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; border: 1px solid var(--aifred-border); }
.dot.done { background: var(--aifred-accent-success); border-color: var(--aifred-accent-success); }
.dot.failed { background: var(--aifred-danger); border-color: var(--aifred-danger); }
.console { margin: 0; max-height: 360px; overflow: auto; padding: 8px; font-size: 0.75rem; line-height: 1.35; white-space: pre-wrap; word-break: break-word; background: var(--aifred-input-bg); border: 1px solid var(--aifred-border); border-radius: 6px; }
</style>
