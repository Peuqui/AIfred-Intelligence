<script setup lang="ts">
import { t } from "../i18n";
import type { AgentSummary } from "../api/types";

defineProps<{ agents: AgentSummary[]; active: string }>();
const emit = defineEmits<{ choose: [agent: string] }>();
</script>

<template>
  <label class="agent">
    {{ t("active_agent_label") }}
    <select :value="active" @change="emit('choose', ($event.target as HTMLSelectElement).value)">
      <option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.emoji }} {{ agent.display_name }}</option>
    </select>
  </label>
</template>

<style scoped>
.agent { display: flex; align-items: center; gap: 8px; font-size: 0.85rem; color: var(--aifred-text-secondary); }
select {
  padding: 4px 8px;
  background: var(--aifred-input-bg);
  color: var(--aifred-text-primary);
  border: 1px solid var(--aifred-border);
  border-radius: 6px;
}
</style>
