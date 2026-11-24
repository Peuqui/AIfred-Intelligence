<script setup lang="ts">
// The sub-tasks of a job: a frame, the parts (each with its task and positions), the columns of the archive and
// the trend. Edits the plan of the job form in place; the server checks it when the job is saved.
import { Plus, Trash2 } from "lucide-vue-next";
import { computed } from "vue";
import type { JobOptions } from "../api/types";
import { t } from "../i18n";
import type { PlanForm } from "../lib/taskPlan";

const props = defineProps<{ plan: PlanForm; options: JobOptions }>();
defineEmits<{ remove: [] }>();

const numberColumns = computed(() => props.plan.columns.filter((column) => column.type === props.options.trend_column_type && column.name));

function addPart(): void {
  props.plan.parts.push({ id: "", title: "", task: "", positions: [{ id: "", name: "" }] });
}
function removeColumn(index: number): void {
  const [removed] = props.plan.columns.splice(index, 1);
  if (removed && removed.name === props.plan.trend) props.plan.trend = "";
}
</script>

<template>
  <details class="plan wide" open>
    <summary>{{ t("jobs_plan_title") }}</summary>
    <div class="body">
    <p class="hint">{{ t("jobs_plan_hint") }}</p>
    <label>{{ t("jobs_plan_frame") }}<textarea v-model="plan.frame" class="frame"></textarea></label>

    <section v-for="(part, partIndex) in plan.parts" :key="partIndex" class="part" data-test="part">
      <div class="row">
        <strong>{{ t("jobs_plan_part_n", { number: partIndex + 1 }) }}</strong>
        <button type="button" class="remove" data-test="remove-part" :aria-label="t('jobs_plan_remove_part')" @click="plan.parts.splice(partIndex, 1)"><Trash2 :size="16" /> {{ t("jobs_plan_remove_part") }}</button>
      </div>
      <div class="row">
        <label class="short">{{ t("jobs_plan_part_id") }}<input v-model="part.id" required /></label>
        <label class="grow">{{ t("jobs_plan_part_title") }}<input v-model="part.title" required /></label>
      </div>
      <label>{{ t("jobs_plan_part_task") }}<textarea v-model="part.task" required></textarea></label>
      <div v-for="(position, positionIndex) in part.positions" :key="positionIndex" class="row" data-test="position">
        <label class="short">{{ t("jobs_plan_position_id") }}<input v-model="position.id" required /></label>
        <label class="grow">{{ t("jobs_plan_position_name") }}<input v-model="position.name" required /></label>
        <button type="button" class="remove" data-test="remove-position" :aria-label="t('jobs_plan_remove_position')" @click="part.positions.splice(positionIndex, 1)"><Trash2 :size="16" /></button>
      </div>
      <button type="button" class="add" data-test="add-position" @click="part.positions.push({ id: '', name: '' })"><Plus :size="16" /> {{ t("jobs_plan_add_position") }}</button>
    </section>
    <button type="button" class="add" data-test="add-part" @click="addPart"><Plus :size="16" /> {{ t("jobs_plan_add_part") }}</button>

    <fieldset>
      <legend>{{ t("jobs_plan_columns") }}</legend>
      <p class="hint">{{ t("jobs_plan_columns_hint") }}</p>
      <div v-for="(column, columnIndex) in plan.columns" :key="columnIndex" class="row" data-test="column">
        <label class="grow">{{ t("jobs_plan_column_name") }}<input v-model="column.name" required /></label>
        <label class="short">{{ t("jobs_plan_column_type") }}
          <select v-model="column.type">
            <option v-for="type in options.column_types" :key="type" :value="type">{{ t(`jobs_plan_type_${type}`) }}</option>
          </select>
        </label>
        <button type="button" class="remove" data-test="remove-column" :aria-label="t('jobs_plan_remove_column')" @click="removeColumn(columnIndex)"><Trash2 :size="16" /></button>
      </div>
      <button type="button" class="add" data-test="add-column" @click="plan.columns.push({ name: '', type: options.column_types[0] ?? '' })"><Plus :size="16" /> {{ t("jobs_plan_add_column") }}</button>
      <label>{{ t("jobs_plan_trend") }}
        <select v-model="plan.trend" data-test="trend">
          <option value="">{{ t("jobs_plan_trend_none") }}</option>
          <option v-for="column in numberColumns" :key="column.name" :value="column.name">{{ column.name }}</option>
        </select>
      </label>
      <p class="hint">{{ t("jobs_plan_trend_hint") }}</p>
    </fieldset>

    <button type="button" class="remove-all" data-test="remove-plan" @click="$emit('remove')"><Trash2 :size="16" /> {{ t("jobs_plan_remove") }}</button>
    </div>
  </details>
</template>

<style scoped>
.plan { border: 1px solid var(--aifred-border); border-radius: 8px; padding: 8px 10px; }
.body { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
summary { cursor: pointer; font-size: 0.85rem; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 0.85rem; color: var(--aifred-text-secondary); }
.part { border: 1px solid var(--aifred-border); border-radius: 8px; padding: 8px 10px; display: flex; flex-direction: column; gap: 6px; }
fieldset { border: 1px solid var(--aifred-border); border-radius: 8px; padding: 8px 10px; display: flex; flex-direction: column; gap: 6px; }
legend { font-size: 0.85rem; padding: 0 4px; }
.row { display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-end; }
.row strong { font-size: 0.85rem; }
.short { width: 9em; }
.grow { flex: 1; min-width: 10em; }
.hint { margin: 0; font-size: 0.8rem; color: var(--aifred-text-muted); }
input, select, textarea { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 4px 8px; font: inherit; min-width: 0; }
textarea { min-height: 5em; resize: vertical; }
.frame { min-height: 4em; }
button { padding: 4px 8px; display: inline-flex; align-items: center; gap: 4px; }
.add, .remove-all { align-self: flex-start; }
.remove { margin-left: auto; }
@media (max-width: 760px) {
  .short { width: 100%; }
}
</style>
