<script setup lang="ts">
// The calibration (maintenance): what the agents' models start with (context, layer split over the cards, speed
// profile) and the matrix of AIfred's model with its run (CalibrationPanel.vue).
import { Target, TriangleAlert, Zap } from "lucide-vue-next";
import { ref } from "vue";
import { ApiError, api } from "../api/client";
import type { CalibrationOverview, LayerSplit, ModelInUse } from "../api/types";
import CalibrationPanel from "./CalibrationPanel.vue";
import HelpButton from "./help/HelpButton.vue";
import { formatNumber, t } from "../i18n";

const calibration = ref<CalibrationOverview | null>(null);
const open = ref(false);
const notice = ref("");

async function load(): Promise<void> {
  notice.value = "";
  try {
    calibration.value = await api<CalibrationOverview>("calibration");
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
  }
}
async function opened(event: Event): Promise<void> {
  open.value = (event.target as HTMLDetailsElement).open;
  if (open.value) await load();
}

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
  <details @toggle="opened">
    <summary><Target :size="16" /> {{ t("calibration_title") }}</summary>
    <p v-if="notice" class="notice"><TriangleAlert :size="16" /> {{ notice }}</p>
    <template v-if="calibration">
      <p class="lifecycle"><HelpButton topic="lifecycle" /> {{ t("model_lifecycle_help_tooltip") }}</p>
      <p class="subtitle">{{ t("calibration_models_title") }}</p>
      <div v-for="model in calibration.models" :key="model.model_id" class="model">
        <div class="model-name">{{ model.model_id }} <span class="hint">{{ model.agents.join(", ") }}</span></div>
        <div v-for="line in contextLines(model)" :key="line.text" class="hint"><Zap v-if="line.speed" :size="16" /> {{ line.text }}</div>
      </div>
      <CalibrationPanel v-if="calibration.backend_type !== 'cloud_api'" :calibration="calibration" @changed="load" />
    </template>
  </details>
</template>

<style scoped>
p { margin: 6px 0 0; }
.hint { color: var(--aifred-text-secondary); font-size: 0.85rem; }
.notice { color: var(--aifred-accent-amber); --icon-own: currentColor; }
.subtitle { font-weight: 600; font-size: 0.85rem; margin-top: 12px; }
.model { margin-top: 6px; }
.model-name { font-size: 0.9rem; word-break: break-all; }
.lifecycle { display: flex; align-items: center; gap: 8px; color: var(--aifred-text-secondary); }
</style>
