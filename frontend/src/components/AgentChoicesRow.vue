<script setup lang="ts">
// One row of the agents' quick choices: the model and, for an agent (not the Automatik), 🎩 persona
// (the agent's own emoji), 💭 reasoning, 🧠 thinking as its model offers it and ⚡ speed where the
// model has a -speed variant, and under 🎲 the sampling with "the model's values". The same row serves the
// quick-choice modal and the agent's card.
import { computed } from "vue";
import type { AgentChoices, ChoicesChange, ModelOption, SamplingRange } from "../api/types";
import { t } from "../i18n";

const props = defineProps<{
  label: string;
  modelId: string;
  models: ModelOption[];
  inherits: boolean; // the row offers "(as AIfred's LLM)"
  row?: AgentChoices;
  ranges?: Record<string, SamplingRange>; // what each sampling value may be (config.SAMPLING_RANGES)
  busy: boolean;
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
</script>

<template>
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
        💭
      </label>
      <label :title="t('thinking_tooltip')">
        🧠
        <select :value="row.thinking" :disabled="busy || row.thinking_options.length < 2" @change="emit('change', { thinking: picked($event) })">
          <option v-for="option in row.thinking_options" :key="option.value" :value="option.value">{{ option.label }}</option>
        </select>
      </label>
      <label v-if="row.speed_available" :title="t('speed_switch_tooltip')">
        <input type="checkbox" :checked="row.speed" :disabled="busy" @change="emit('change', { speed: !row.speed })" />
        ⚡
      </label>
    </div>
    <details v-if="row && ranges" class="sampling">
      <summary>{{ t("sampling_section_label") }}</summary>
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
        <button class="reset" :title="t('sampling_reset_tooltip')" :disabled="busy" @click="emit('change', { reset_sampling: true })">↺</button>
      </div>
    </details>
  </div>
</template>

<style scoped>
.row { display: grid; grid-template-columns: 160px minmax(0, 1fr); gap: 6px 12px; align-items: center; padding: 8px 0; border-bottom: 1px solid var(--aifred-border); }
.name { font-weight: 600; font-size: 0.85rem; overflow-wrap: anywhere; }
input[type="checkbox"] { accent-color: var(--aifred-primary); }
select { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 3px 6px; font: inherit; font-size: 0.85rem; }
.model { width: 100%; min-width: 0; }
.switches { grid-column: 2; display: flex; flex-wrap: wrap; gap: 14px; align-items: center; font-size: 0.85rem; }
.switches label { display: flex; align-items: center; gap: 4px; cursor: pointer; }
.sampling { grid-column: 2; font-size: 0.85rem; }
.sampling summary { cursor: pointer; color: var(--aifred-text-secondary); }
.values { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-top: 6px; }
.values label { display: flex; align-items: center; gap: 4px; }
.values input { width: 5.5em; background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 2px 4px; font: inherit; }
.reset { background: none; border: 1px solid var(--aifred-border); border-radius: 6px; color: inherit; cursor: pointer; padding: 1px 8px; }
@media (max-width: 560px) {
  .row { grid-template-columns: minmax(0, 1fr); }
  .switches, .sampling { grid-column: 1; }
}
</style>
