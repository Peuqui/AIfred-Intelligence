<script setup lang="ts">
// The session's discussion mode and who sits where (lib/debate_seats.py): the critic, in auto-consensus
// and tribunal also the judge; the author is whoever answers first. In the symposion the participants.
// A choice of this session only; a new one starts with the settings' default seats.
import { computed, ref } from "vue";
import type { AgentSummary, DebateMode, SessionConfig, SessionConfigUpdate } from "../api/types";
import { t } from "../i18n";
import HelpButton from "./help/HelpButton.vue";

const props = defineProps<{ config: SessionConfig; agents: AgentSummary[]; modes: DebateMode[] }>();
const emit = defineEmits<{ configure: [changes: SessionConfigUpdate] }>();

const seats = computed(() => props.modes.find((mode) => mode.mode === props.config.multi_agent_mode)?.seats ?? []);
const guestsOpen = ref(false);

function picked(event: Event): string {
  return (event.target as HTMLSelectElement).value;
}

function toggleGuest(agent: string): void {
  const guests = props.config.symposion_agents;
  emit("configure", { symposion_agents: guests.includes(agent) ? guests.filter((other) => other !== agent) : [...guests, agent] });
}
</script>

<template>
  <div class="debate">
    <label :title="t('multi_agent_mode')">
      <select :value="config.multi_agent_mode" @change="emit('configure', { multi_agent_mode: picked($event) })">
        <option v-for="mode in modes" :key="mode.mode" :value="mode.mode">{{ t(`multi_agent_${mode.mode}`) }}</option>
      </select>
    </label>
    <HelpButton topic="debate" />
    <label v-for="seat in seats" :key="seat" class="seat">
      {{ t(`debate_seat_${seat}`) }}
      <select :value="config.debate_seats[seat]" @change="emit('configure', { debate_seats: { [seat]: picked($event) } })">
        <option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.emoji }} {{ agent.display_name }}</option>
      </select>
    </label>
    <span v-if="config.multi_agent_mode === 'symposion'" class="guests">
      <button :aria-expanded="guestsOpen" @click="guestsOpen = !guestsOpen">
        {{ t("symposion_agents") }} ({{ config.symposion_agents.length }}) ▾
      </button>
      <span v-if="guestsOpen" class="panel">
        <label v-for="agent in agents" :key="agent.id">
          <input type="checkbox" :checked="config.symposion_agents.includes(agent.id)" @change="toggleGuest(agent.id)" />
          {{ agent.emoji }} {{ agent.display_name }}
        </label>
      </span>
    </span>
  </div>
</template>

<style scoped>
.debate { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 0.85rem; color: var(--aifred-text-secondary); }
.seat { display: flex; align-items: center; gap: 6px; }
select, button { padding: 4px 8px; background: var(--aifred-input-bg); color: var(--aifred-text-primary); border: 1px solid var(--aifred-border); border-radius: 6px; font: inherit; cursor: pointer; }
.guests { position: relative; }
.panel {
  position: absolute; top: calc(100% + 6px); left: 0; z-index: 10; min-width: 200px; padding: 8px 10px;
  display: flex; flex-direction: column; gap: 6px; background: var(--aifred-card-bg);
  border: 1px solid var(--aifred-border-light); border-radius: 8px;
}
.panel label { display: flex; align-items: center; gap: 6px; color: var(--aifred-text-primary); cursor: pointer; }
input[type="checkbox"] { accent-color: var(--aifred-primary); }
</style>
