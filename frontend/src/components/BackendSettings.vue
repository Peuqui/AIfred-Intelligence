<script setup lang="ts">
// The LLM backend as the server sees it: the backend in use and the ones these GPUs can run, the cloud provider with
// the state of its key, the GPUs; and, when opened, the calibration: what the agents' models start with (context,
// layer split over the cards, speed profile) and the matrix of AIfred's model (llama.cpp: which of its profiles are
// calibrated; vLLM: its operating point and which describer/TTS pairs fit together on the side-channel card).
// Reading only for now: switching the backend and starting a calibration follow.
import { computed, onMounted, ref } from "vue";
import { ApiError, api } from "../api/client";
import type { BackendOverview, CalibrationOverview, GpuCard, LayerSplit, MatrixCell, ModelInUse } from "../api/types";
import { formatNumber, t } from "../i18n";

const overview = ref<BackendOverview | null>(null);
const calibration = ref<CalibrationOverview | null>(null);
const notice = ref("");

async function call<T>(work: () => Promise<T>): Promise<T | null> {
  notice.value = "";
  try {
    return await work();
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
    return null;
  }
}
onMounted(async () => {
  overview.value = await call(() => api<BackendOverview>("backend"));
});
async function openCalibration(event: Event): Promise<void> {
  if ((event.target as HTMLDetailsElement).open) calibration.value = await call(() => api<CalibrationOverview>("calibration"));
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
function contextLines(model: ModelInUse): string[] {
  const info = model.calibration;
  if (!info) return [];
  if (info.rope_contexts.length) {
    return [info.rope_contexts.map((rope) => rope.factor === 1
      ? t("calibration_rope_native", { context: formatNumber(rope.context) })
      : t("calibration_rope_factor", { factor: formatNumber(rope.factor, 1), context: formatNumber(rope.context) })).join(" · ")];
  }
  if (!info.context) return [t("calibration_not_calibrated")];
  const lines = [t("calibration_context", { context: formatNumber(info.context) })];
  if (info.cache_context) lines.push(t("calibration_context_cache", { context: formatNumber(info.cache_context) }));
  if (info.split) lines.push(t("calibration_layer_split", { split: splitText(info.split), active: info.split.active_cards, total: info.split.total_cards }));
  if (info.speed) {
    const speed = t("calibration_speed_split", { split: splitText(info.speed), active: info.speed.active_cards, total: info.speed.total_cards });
    lines.push(info.speed.context ? `${speed} · ${t("calibration_context", { context: formatNumber(info.speed.context) })}` : speed);
  }
  return lines;
}
// What a cell's dot says: llama.cpp a profile, vLLM the operating point (base cell) or a burn-in pair.
function cellTitle(cell: MatrixCell): string {
  if (calibration.value?.backend_type !== "vllm") {
    return t({ done: "calibration_already_done", failed: "calibration_cell_failed", open: "calibration_cell_open" }[cell.status]);
  }
  if (cell.key === "|") return t(cell.status === "done" ? "calibration_dot_done" : "calibration_dot_missing");
  return t({ done: "calibration_pair_fits", failed: "calibration_pair_overflows", open: "calibration_not_measured" }[cell.status]);
}
</script>

<template>
  <section v-if="overview" class="backend">
    <p v-if="notice" class="notice">{{ notice }}</p>
    <div class="line">
      <span class="name">{{ t("backend") }}</span>
      <strong>{{ backendLabel }}</strong>
      <span class="hint">{{ t("gpu_available_prefix") }}{{ overview.backends.map((choice) => choice.label).join(", ") }}</span>
    </div>
    <div v-if="cloud" class="line">
      <span class="name">{{ t("cloud_api_provider") }}</span>
      <span>{{ cloud.label }}</span>
      <span :class="cloud.key_configured ? 'ok' : 'missing'">{{ t(cloud.key_configured ? "cloud_api_key_configured" : "cloud_api_key_missing") }}</span>
    </div>
    <details v-if="overview.gpu">
      <summary>{{ t("gpu_details") }}</summary>
      <p v-for="kind in cardKinds" :key="kind.card.name" class="card">
        🎮 {{ t("gpu_card_line", { count: kind.count, name: kind.card.name, vram: formatNumber(kind.card.vram_mb / 1024), compute: formatNumber(kind.card.compute_capability, 1) }) }}
      </p>
      <p class="card">{{ t("gpu_total", { vram: formatNumber(overview.gpu.total_vram_gb) }) }}</p>
      <template v-if="overview.gpu.unsupported_backends.includes('vllm')">
        <p class="hint">{{ t("gpu_vllm_requires_compute") }}</p>
        <p class="hint">💡 {{ t("gpu_gguf_hint") }}</p>
      </template>
    </details>
    <details @toggle="openCalibration">
      <summary>{{ t("calibration_title") }}</summary>
      <template v-if="calibration">
        <p class="subtitle">{{ t("calibration_models_title") }}</p>
        <div v-for="model in calibration.models" :key="model.model_id" class="model">
          <div class="model-name">{{ model.model_id }} <span class="hint">{{ model.agents.join(", ") }}</span></div>
          <div v-for="line in contextLines(model)" :key="line" class="hint">{{ line }}</div>
        </div>
        <template v-if="calibration.matrix">
          <p class="subtitle">
            {{ calibration.backend_type === "vllm" ? t("calibration_vllm_burnin_title") : t("calibration_matrix_llamacpp_title", { model: calibration.model_id }) }}
          </p>
          <table class="matrix">
            <thead>
              <tr>
                <th></th>
                <th v-for="column in calibration.matrix.columns" :key="column.tts_key">{{ column.label }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in calibration.matrix.rows" :key="row.vlm_key">
                <th>{{ row.label }}</th>
                <td v-for="cell in row.cells" :key="cell.key">
                  <span class="dot" :class="cell.status" :title="cellTitle(cell)" :aria-label="cellTitle(cell)"></span>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-if="calibration.matrix.tts_problem" class="notice">⚠️ {{ calibration.matrix.tts_problem }}</p>
        </template>
      </template>
    </details>
  </section>
</template>

<style scoped>
.backend { display: flex; flex-direction: column; gap: 10px; padding: 14px 16px; background: var(--aifred-card-bg); border: 1px solid var(--aifred-border); border-radius: 10px; }
.line { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.name { min-width: 170px; font-weight: 600; font-size: 0.9rem; }
.hint { color: var(--aifred-text-secondary); font-size: 0.85rem; }
.notice { margin: 0; color: var(--aifred-accent-amber); }
.ok { color: var(--aifred-accent-success); font-size: 0.85rem; }
.missing { color: var(--aifred-danger); font-size: 0.85rem; }
summary { cursor: pointer; font-weight: 600; font-size: 0.9rem; }
details p { margin: 6px 0 0; }
.card { font-size: 0.85rem; color: var(--aifred-accent-cyan); }
.subtitle { font-weight: 600; font-size: 0.85rem; margin-top: 12px !important; }
.model { margin-top: 6px; }
.model-name { font-size: 0.9rem; word-break: break-all; }
.matrix { border-collapse: collapse; margin-top: 6px; font-size: 0.8rem; }
.matrix th, .matrix td { padding: 4px 8px; text-align: center; }
.matrix tbody th { text-align: left; font-weight: 600; word-break: break-all; }
.dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; border: 1px solid var(--aifred-border); }
.dot.done { background: var(--aifred-accent-success); border-color: var(--aifred-accent-success); }
.dot.failed { background: var(--aifred-danger); border-color: var(--aifred-danger); }
</style>
