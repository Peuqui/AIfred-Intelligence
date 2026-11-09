<script setup lang="ts">
// Who answers, as a pill per agent (Reflex's _agent_toggle_button): in the symposion every pill switched on takes part,
// numbered in the order they speak (the order of the clicks); in every other mode one pill is the active agent.
import { computed } from "vue";
import type { AgentSummary, SessionConfig, SessionConfigUpdate } from "../api/types";
import { t } from "../i18n";

const props = defineProps<{ config: SessionConfig; agents: AgentSummary[] }>();
const emit = defineEmits<{ configure: [changes: SessionConfigUpdate] }>();

const symposion = computed(() => props.config.multi_agent_mode === "symposion");

function isOn(agent: string): boolean {
  return symposion.value ? props.config.symposion_agents.includes(agent) : props.config.active_agent === agent;
}

function choose(agent: string): void {
  if (!symposion.value) {
    emit("configure", { active_agent: agent });
    return;
  }
  const guests = props.config.symposion_agents;
  emit("configure", { symposion_agents: guests.includes(agent) ? guests.filter((other) => other !== agent) : [...guests, agent] });
}
</script>

<template>
  <div class="agents" role="group" :aria-label="symposion ? t('symposion_agents') : t('active_agent_label')">
    <button
      v-for="agent in agents"
      :key="agent.id"
      type="button"
      class="pill"
      :class="{ on: isOn(agent.id) }"
      :aria-pressed="isOn(agent.id)"
      :title="agent.display_name"
      @click="choose(agent.id)"
    >
      <span v-if="symposion && isOn(agent.id)" class="order">{{ config.symposion_agents.indexOf(agent.id) + 1 }}</span>
      <span class="emoji">{{ agent.emoji }}</span>
      <span class="name">{{ agent.display_name }}</span>
    </button>
  </div>
</template>

<style scoped>
.agents { display: flex; flex-wrap: wrap; gap: 6px; min-width: 0; }
.pill { display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px; }
.order {
  min-width: 12px; padding: 0 2px; font-size: 0.6rem; font-weight: 700; line-height: 12px; text-align: center;
  color: var(--aifred-primary); background: var(--aifred-page-bg); border: 1px solid var(--aifred-accent-amber); border-radius: 3px;
}
/* On a narrow screen the emoji alone; the name stays in the tooltip (the row of ChatView is the container). */
@container (max-width: 600px) {
  .name { display: none; }
  .pill { padding: 3px 8px; }
}
</style>
