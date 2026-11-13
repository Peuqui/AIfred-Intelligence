<script setup lang="ts">
// One row of the agents' quick choices: the model and, for an agent (not the Automatik), persona
// (the agent's own emoji), reasoning, thinking as its model offers it and speed where the
// model has a -speed variant, and under the sampling section "the model's values". Below, the context window the
// agent's calls get and where it comes from; on Ollama a value by hand (a chat agent's holds until the server
// restarts, Vision's is saved — Peuqui 08.11.2026: it is for trying out, the calibration decides). The same row serves
// the quick-choice modal and the agent's card.
import { Brain, Dices, MessageCircleMore, RotateCcw, Ruler, Zap } from "lucide-vue-next";
import { computed } from "vue";
import type { AgentChoices, ChoicesChange, ModelOption, SamplingRange } from "../api/types";
import { formatNumber, t } from "../i18n";

const props = defineProps<{
  label: string;
  modelId: string;
  models: ModelOption[];
  inherits: boolean; // the row offers "(as AIfred's LLM)"
  row?: AgentChoices;
  ranges?: Record<string, SamplingRange>; // what each sampling value may be (config.SAMPLING_RANGES)
  busy: boolean;
  manualOffered?: boolean; // the backend takes a context by hand (Ollama)
}>();
const emit = defineEmits<{ change: [update: ChoicesChange] }>();

// AIfred, Sokrates, Salomo and Vision have a persona text of their own, every other agent the general one.
const OWN_PERSONA_TEXT = ["aifred", "sokrates", "salomo", "vision"];
const personalityTitle = computed(() =>
  props.row ? t(OWN_PERSONA_TEXT.includes(props.row.id) ? `personality_${props.row.id}_tooltip` : "personality_generic_tooltip") : "",
);

// A saved model the backend does not list (any more) stays visible instead of a blank choice.
const unlisted = computed(() => props.modelId !== "" && !props.models.some((model) => model.id === props.modelId));

function picked(event: Event): string {
  return (event.target as HTMLSelectElement).value;
}

// The labels of the sampling values; the temperature's is short.
const SAMPLING_LABELS: Record<string, string> = {
  temperature: "temp_label",
  top_k: "top_k_label",
  top_p: "top_p_label",
  min_p: "min_p_label",
  repeat_penalty: "repeat_penalty_label",
};

// A value outside its range is not sent: the field shows the stored one again (the server would refuse it).
function sampled(field: string, event: Event): void {
  const input = event.target as HTMLInputElement;
  const value = Number(input.value);
  const range = props.ranges?.[field];
  if (input.value === "" || !range || Number.isNaN(value) || value < range.min || value > range.max) {
    input.value = String(props.row?.sampling[field] ?? "");
    return;
  }
  emit("change", { sampling: { [field]: value } });
}

// A value the server refuses (out of range) is not sent: the field shows the current one again.
function manualValue(event: Event): void {
  const input = event.target as HTMLInputElement;
  const value = Number(input.value);
  if (!props.row || input.value === "" || !Number.isInteger(value) || value < 1) {
    input.value = String(props.row?.manual_context ?? "");
    return;
  }
  emit("change", { manual_context: { enabled: true, value } });
}
</script>

<template>
  <!-- Its own width decides the layout (a narrow card, a wide dialog), not the screen's. -->
  <div class="choices-row">
    <div class="row">
      <span class="name">{{ label }}</span>
      <select class="model" :value="modelId" :disabled="busy" @change="emit('change', { model_id: picked($event) })">
        <option v-if="inherits" value="">{{ t("sokrates_llm_same") }}</option>
        <option v-if="unlisted" :value="modelId">{{ modelId }}</option>
        <option v-for="model in models" :key="model.id" :value="model.id">
          {{ model.label }}{{ model.badge ? ` · ${model.badge}` : "" }}
        </option>
      </select>
      <div v-if="row" class="switches">
        <label :title="personalityTitle">
          <input type="checkbox" :checked="row.personality" :disabled="busy" @change="emit('change', { personality: !row.personality })" />
          {{ row.emoji }}
        </label>
        <label :title="t('reasoning_tooltip')">
          <input type="checkbox" :checked="row.reasoning" :disabled="busy" @change="emit('change', { reasoning: !row.reasoning })" />
          <MessageCircleMore :size="16" />
        </label>
        <label :title="t('thinking_tooltip')">
          <Brain :size="16" />
          <select :value="row.thinking" :disabled="busy || row.thinking_options.length < 2" @change="emit('change', { thinking: picked($event) })">
            <option v-for="option in row.thinking_options" :key="option.value" :value="option.value">{{ option.label }}</option>
          </select>
        </label>
        <label v-if="row.speed_available" :title="t('speed_switch_tooltip')">
          <input type="checkbox" :checked="row.speed" :disabled="busy" @change="emit('change', { speed: !row.speed })" />
          <Zap :size="16" />
        </label>
      </div>
      <div v-if="row" class="context">
        <span :title="manualOffered ? t('context_window_persist_hint') : t('context_window_fixed_hint')">
          <Ruler :size="16" /> {{ t("context_window") }}: {{ formatNumber(row.context) }} <span class="source">({{ row.context_source }})</span>
        </span>
        <template v-if="manualOffered">
          <label :title="t('context_window_persist_hint')">
            <input
              type="checkbox"
              :checked="row.manual_context_enabled"
              :disabled="busy"
              @change="emit('change', { manual_context: { enabled: !row.manual_context_enabled, value: row.manual_context } })"
            />
            {{ t("context_manual_label") }}
          </label>
          <input v-if="row.manual_context_enabled" type="number" min="1" :value="row.manual_context" :disabled="busy" @change="manualValue" />
        </template>
      </div>
      <details v-if="row && ranges" class="sampling">
        <summary><Dices :size="16" /> {{ t("sampling_section_label") }}</summary>
        <div class="values">
          <label v-for="(range, field) in ranges" :key="field">
            {{ t(SAMPLING_LABELS[field] ?? field) }}
            <input
              type="number"
              :min="range.min"
              :max="range.max"
              :step="range.step"
              :value="row.sampling[field]"
              :disabled="busy"
              @change="sampled(String(field), $event)"
            />
          </label>
          <button class="reset" :title="t('sampling_reset_tooltip')" :disabled="busy" @click="emit('change', { reset_sampling: true })"><RotateCcw :size="16" /></button>
        </div>
      </details>
    </div>
  </div>
</template>

<style scoped>
.row { display: grid; grid-template-columns: 160px minmax(0, 1fr); gap: 6px 12px; align-items: center; padding: 8px 0; border-bottom: 1px solid var(--aifred-border); }
.name { font-weight: 600; font-size: 0.85rem; overflow-wrap: anywhere; }
input[type="checkbox"] { accent-color: var(--aifred-primary); }
select { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 3px 6px; font: inherit; font-size: 0.85rem; }
.model { width: 100%; min-width: 0; text-overflow: ellipsis; }
.switches { grid-column: 2; display: flex; flex-wrap: wrap; gap: 14px; align-items: center; font-size: 0.85rem; }
.switches label { display: flex; align-items: center; gap: 4px; cursor: pointer; }
.context { grid-column: 2; display: flex; flex-wrap: wrap; gap: 12px; align-items: center; font-size: 0.85rem; color: var(--aifred-text-secondary); }
.context .source { color: var(--aifred-text-muted); }
.context label { display: flex; align-items: center; gap: 4px; cursor: pointer; }
.context input[type="number"] { width: 7em; background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 2px 4px; font: inherit; }
.sampling { grid-column: 2; font-size: 0.85rem; }
.sampling summary { cursor: pointer; color: var(--aifred-text-secondary); }
.values { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-top: 6px; }
.values label { display: flex; align-items: center; gap: 4px; }
.values input { width: 5.5em; background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 2px 4px; font: inherit; }
.reset { background: none; border: 1px solid var(--aifred-border); border-radius: 6px; color: inherit; cursor: pointer; padding: 1px 8px; }
/* Narrow (a card on a phone): the name above, the model across the whole width, then the rest; every part in the one
   column (the context line stayed in column 2 and made a hidden second one, squeezing the name to a letter a line). */
.choices-row { container-type: inline-size; }
@container (max-width: 520px) {
  .row { grid-template-columns: minmax(0, 1fr); }
  .name, .model, .switches, .context, .sampling { grid-column: 1; }
}
</style>
