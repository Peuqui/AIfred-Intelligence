<script setup lang="ts">
// The session's discussion mode and who sits where (lib/debate_seats.py): the critic, in auto-consensus
// and tribunal also the judge; the author is whoever answers first. The symposion's participants are the agent pills.
// A choice of this session only; a new one starts with the settings' default seats.
import { computed } from "vue";
import type { AgentSummary, DebateMode, SessionConfig, SessionConfigUpdate } from "../api/types";
import { t } from "../i18n";

const props = defineProps<{ config: SessionConfig; agents: AgentSummary[]; modes: DebateMode[] }>();
const emit = defineEmits<{ configure: [changes: SessionConfigUpdate] }>();

const seats = computed(() => props.modes.find((mode) => mode.mode === props.config.multi_agent_mode)?.seats ?? []);

function picked(event: Event): string {
  return (event.target as HTMLSelectElement).value;
}
</script>

<template>
  <div class="debate">
    <label :title="t('multi_agent_mode')">
      <select :value="config.multi_agent_mode" @change="emit('configure', { multi_agent_mode: picked($event) })">
        <option v-for="mode in modes" :key="mode.mode" :value="mode.mode">{{ t(`multi_agent_${mode.mode}`) }}</option>
      </select>
    </label>
    <label v-for="seat in seats" :key="seat" class="seat">
      {{ t(`debate_seat_${seat}`) }}
      <select :value="config.debate_seats[seat]" @change="emit('configure', { debate_seats: { [seat]: picked($event) } })">
        <option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.emoji }} {{ agent.display_name }}</option>
      </select>
    </label>
  </div>
</template>

<style scoped>
.debate { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 0.85rem; color: var(--aifred-text-secondary); }
.seat { display: flex; align-items: center; gap: 6px; }
select { padding: 4px 8px; background: var(--aifred-input-bg); color: var(--aifred-text-primary); border: 1px solid var(--aifred-border); border-radius: 6px; font: inherit; cursor: pointer; }
</style>
