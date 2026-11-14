<script setup lang="ts">
// The LLM backend: the one in use (switchable to the ones these GPUs can run), the cloud provider with the state of its
// key, the GPUs, restarting the backend's service or AIfred, loading the default settings; and, when opened, the
// calibration: what the agents' models start with (context, layer split over the cards, speed profile) and the matrix
// of AIfred's model with its run (CalibrationPanel.vue).
import {
  CircleCheck, Cloud, Cpu, Hourglass, Lightbulb, Monitor, RefreshCw, RotateCcw, Target, TriangleAlert, Zap,
} from "lucide-vue-next";
import { type Component, computed, onMounted, ref } from "vue";
import { ApiError, api } from "../api/client";
import type { BackendOverview, CalibrationOverview, GpuCard, LayerSplit, ModelInUse } from "../api/types";
import CalibrationPanel from "./CalibrationPanel.vue";
import HelpButton from "./help/HelpButton.vue";
import { formatNumber, loadLanguage, t } from "../i18n";
import { useSettings } from "../stores/settings";

const settings = useSettings();
const overview = ref<BackendOverview | null>(null);
const calibration = ref<CalibrationOverview | null>(null);
const calibrationOpen = ref(false);
const notice = ref("");
// What went well, with the sign of its state (a restarted backend may still be starting).
const done = ref<{ icon: Component | null; text: string } | null>(null);
const busy = ref(false);              // a switch or a restart runs: the controls wait
const confirming = ref<"" | "defaults" | "aifred">("");

async function call<T>(work: () => Promise<T>): Promise<T | null> {
  notice.value = "";
  done.value = null;
  busy.value = true;
  try {
    return await work();
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
    return null;
  } finally {
    busy.value = false;
  }
}
async function loadCalibration(): Promise<void> {
  calibration.value = await call(() => api<CalibrationOverview>("calibration"));
}
onMounted(async () => {
  overview.value = await call(() => api<BackendOverview>("backend"));
});
async function openCalibration(event: Event): Promise<void> {
  calibrationOpen.value = (event.target as HTMLDetailsElement).open;
  if (calibrationOpen.value) await loadCalibration();
}
// A switch changes the backend of settings.json and the agents' models: the page reads both again.
async function changed(answer: BackendOverview | null): Promise<void> {
  if (!answer) return;
  overview.value = answer;
  const language = settings.current?.ui_language;
  await settings.load();
  if (settings.current && settings.current.ui_language !== language) await loadLanguage(settings.current.ui_language);
  if (calibrationOpen.value) await loadCalibration();
}
async function switchTo(event: Event): Promise<void> {
  const backend_type = (event.target as HTMLSelectElement).value;
  await changed(await call(() => api<BackendOverview>("backend", { method: "PUT", body: JSON.stringify({ backend_type }) })));
}
async function chooseProvider(event: Event): Promise<void> {
  const provider = (event.target as HTMLSelectElement).value;
  await changed(await call(() => api<BackendOverview>("backend/cloud-provider", { method: "PUT", body: JSON.stringify({ provider }) })));
}
async function restartBackend(): Promise<void> {
  const answer = await call(() => api<{ backend_type: string; ready_after_s: number | null }>("system/restart-backend", { method: "POST" }));
  if (!answer) return;
  done.value = answer.ready_after_s === null
    ? { icon: Hourglass, text: t("backend_not_ready_yet", { backend: backendLabel.value }) }
    : { icon: CircleCheck, text: t("backend_ready_after", { backend: backendLabel.value, seconds: formatNumber(answer.ready_after_s, 1) }) };
  if (calibrationOpen.value) await loadCalibration();
}
async function restartAifred(): Promise<void> {
  confirming.value = "";
  if (await call(() => api("system/restart-aifred", { method: "POST" }))) done.value = { icon: null, text: t("aifred_restarting") };
}
async function loadDefaults(): Promise<void> {
  confirming.value = "";
  if (!(await call(() => api("system/reset-defaults", { method: "POST" })))) return;
  await changed(await call(() => api<BackendOverview>("backend")));
  if (!notice.value) done.value = { icon: null, text: t("default_settings_loaded") };
}

const backendLabel = computed(() =>
  overview.value?.backends.find((choice) => choice.id === overview.value?.backend_type)?.label ?? overview.value?.backend_type ?? "",
);
const cloud = computed(() =>
  overview.value?.backend_type === "cloud_api" ? overview.value.cloud_providers.find((provider) => provider.id === overview.value?.cloud_provider) : undefined,
);
// One line per kind of card, in the order the server detected them (lowest compute capability first).
const cardKinds = computed(() => {
  const kinds = new Map<string, { card: GpuCard; count: number }>();
  for (const card of overview.value?.gpu?.cards ?? []) {
    const kind = kinds.get(card.name);
    if (kind) kind.count += 1;
    else kinds.set(card.name, { card, count: 1 });
  }
  return [...kinds.values()];
});

function splitText(split: LayerSplit): string {
  return split.layers.map((layers) => String(layers)).join(":");
}
// Each line with its sign: the speed profile carries the one of the speed switch.
function contextLines(model: ModelInUse): { text: string; speed: boolean }[] {
  const info = model.calibration;
  if (!info) return [];
  if (info.rope_contexts.length) {
    return [{ text: info.rope_contexts.map((rope) => rope.factor === 1
      ? t("calibration_rope_native", { context: formatNumber(rope.context) })
      : t("calibration_rope_factor", { factor: formatNumber(rope.factor, 1), context: formatNumber(rope.context) })).join(" · "), speed: false }];
  }
  if (!info.context) return [{ text: t("calibration_not_calibrated"), speed: false }];
  const lines = [{ text: t("calibration_context", { context: formatNumber(info.context) }), speed: false }];
  if (info.cache_context) lines.push({ text: t("calibration_context_cache", { context: formatNumber(info.cache_context) }), speed: false });
  if (info.split) {
    lines.push({ text: t("calibration_layer_split", { split: splitText(info.split), active: info.split.active_cards, total: info.split.total_cards }), speed: false });
  }
  if (info.speed) {
    const speed = t("calibration_speed_split", { split: splitText(info.speed), active: info.speed.active_cards, total: info.speed.total_cards });
    lines.push({ text: info.speed.context ? `${speed} · ${t("calibration_context", { context: formatNumber(info.speed.context) })}` : speed, speed: true });
  }
  return lines;
}
</script>

<template>
  <section v-if="overview" class="backend">
    <p v-if="notice" class="notice"><TriangleAlert :size="16" /> {{ notice }}</p>
    <p v-if="done" class="done"><component :is="done.icon" v-if="done.icon" :size="16" /> {{ done.text }}</p>
    <label class="line">
      <span class="name">{{ t("backend") }}</span>
      <select :value="overview.backend_type" :disabled="busy" @change="switchTo">
        <option v-for="choice in overview.backends" :key="choice.id" :value="choice.id">{{ choice.label }}</option>
      </select>
      <span v-if="busy" class="hint"><Hourglass :size="16" /> {{ t("backend_switching") }}</span>
    </label>
    <div v-if="cloud" class="line">
      <span class="name"><Cloud :size="16" /> {{ t("cloud_api_provider") }}</span>
      <select :value="cloud.id" :disabled="busy" @change="chooseProvider">
        <option v-for="provider in overview.cloud_providers" :key="provider.id" :value="provider.id">{{ provider.label }}</option>
      </select>
      <span v-if="cloud.key_configured" class="ok"><CircleCheck :size="16" /> {{ t("cloud_api_key_configured") }}</span>
      <span v-else class="missing"><TriangleAlert :size="16" /> {{ t("cloud_api_key_missing") }}</span>
    </div>
    <div class="line buttons">
      <button v-if="overview.backend_type !== 'cloud_api'" :disabled="busy" :title="t('backend_restart_info')" @click="restartBackend">
        <RefreshCw :size="16" /> {{ t("backend_restart", { backend: backendLabel }) }}
      </button>
      <template v-if="confirming === 'aifred'">
        <span>{{ t("restart_aifred_confirm") }}</span>
        <button class="danger" @click="restartAifred">{{ t("confirm_yes") }}</button>
        <button @click="confirming = ''">{{ t("db_cancel") }}</button>
      </template>
      <button v-else :disabled="busy" :title="t('aifred_restart_info')" @click="confirming = 'aifred'"><RefreshCw :size="16" /> {{ t("restart_aifred") }}</button>
      <template v-if="confirming === 'defaults'">
        <span>{{ t("load_default_settings_confirm") }}</span>
        <button class="danger" @click="loadDefaults">{{ t("confirm_yes") }}</button>
        <button @click="confirming = ''">{{ t("db_cancel") }}</button>
      </template>
      <button v-else :disabled="busy" @click="confirming = 'defaults'"><RotateCcw :size="16" /> {{ t("load_default_settings") }}</button>
    </div>
    <details v-if="overview.gpu">
      <summary><Monitor :size="16" /> {{ t("gpu_details") }}</summary>
      <p v-for="kind in cardKinds" :key="kind.card.name" class="card">
        <Cpu :size="16" /> {{ t("gpu_card_line", { count: kind.count, name: kind.card.name, vram: formatNumber(kind.card.vram_mb / 1024), compute: formatNumber(kind.card.compute_capability, 1) }) }}
      </p>
      <p class="card">{{ t("gpu_total", { vram: formatNumber(overview.gpu.total_vram_gb) }) }}</p>
      <template v-if="overview.gpu.unsupported_backends.includes('vllm')">
        <p class="hint">{{ t("gpu_vllm_requires_compute") }}</p>
        <p class="hint"><Lightbulb :size="16" /> {{ t("gpu_gguf_hint") }}</p>
      </template>
    </details>
    <details @toggle="openCalibration">
      <summary><Target :size="16" /> {{ t("calibration_title") }}</summary>
      <template v-if="calibration">
        <p class="lifecycle"><HelpButton topic="lifecycle" /> {{ t("model_lifecycle_help_tooltip") }}</p>
        <p class="subtitle">{{ t("calibration_models_title") }}</p>
        <div v-for="model in calibration.models" :key="model.model_id" class="model">
          <div class="model-name">{{ model.model_id }} <span class="hint">{{ model.agents.join(", ") }}</span></div>
          <div v-for="line in contextLines(model)" :key="line.text" class="hint"><Zap v-if="line.speed" :size="16" /> {{ line.text }}</div>
        </div>
        <CalibrationPanel v-if="calibration.backend_type !== 'cloud_api'" :calibration="calibration" @changed="loadCalibration" />
      </template>
    </details>
  </section>
</template>

<style scoped>
.backend { display: flex; flex-direction: column; gap: 10px; padding: 14px 16px; background: var(--aifred-card-bg); border: 1px solid var(--aifred-border); border-radius: 10px; }
.line { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.name { min-width: 170px; font-weight: 600; font-size: 0.9rem; }
.hint { color: var(--aifred-text-secondary); font-size: 0.85rem; }
.notice { margin: 0; color: var(--aifred-accent-amber); --icon-own: currentColor; }
.done { margin: 0; color: var(--aifred-accent-success); }
select { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 4px 8px; font: inherit; }
button { padding: 4px 8px; }
select:disabled { opacity: 0.5; cursor: default; }
.buttons { gap: 8px; }
.ok { color: var(--aifred-accent-success); font-size: 0.85rem; --icon-own: currentColor; }
.missing { color: var(--aifred-danger); font-size: 0.85rem; --icon-own: currentColor; }
summary { cursor: pointer; font-weight: 600; font-size: 0.9rem; }
details p { margin: 6px 0 0; }
.card { font-size: 0.85rem; color: var(--aifred-accent-cyan); --icon-own: currentColor; }
.subtitle { font-weight: 600; font-size: 0.85rem; margin-top: 12px !important; }
.model { margin-top: 6px; }
.model-name { font-size: 0.9rem; word-break: break-all; }
.lifecycle { display: flex; align-items: center; gap: 8px; color: var(--aifred-text-secondary); }
</style>
