<script setup lang="ts">
// The discussion modes: who speaks in which order and who decides, with the rounds and the consensus type of the settings.
import { computed } from "vue";
import { t, tCount } from "../../i18n";
import { useSettings } from "../../stores/settings";

const settings = useSettings();

const rows = computed(() => {
  const current = settings.current;
  if (!current) return [];
  const rounds = tCount("multi_agent_help_rounds", current.max_debate_rounds);
  const consensus = current.consensus_type === "unanimous" ? t("consensus_unanimous") : t("consensus_majority");
  return [
    { mode: t("multi_agent_standard"), flow: t("multi_agent_help_standard_flow"), decision: t("multi_agent_help_standard_decision") },
    { mode: t("multi_agent_critical_review"), flow: t("multi_agent_help_critical_review_flow"), decision: t("multi_agent_help_critical_review_decision") },
    {
      mode: t("multi_agent_code_review"),
      flow: t("multi_agent_help_code_review_flow", { rounds }),
      decision: t("multi_agent_help_code_review_decision"),
    },
    {
      mode: t("multi_agent_auto_consensus"),
      flow: t("multi_agent_help_auto_consensus_flow", { rounds }),
      decision: t("multi_agent_help_auto_consensus_decision", { consensus }),
    },
    { mode: t("multi_agent_tribunal"), flow: t("multi_agent_help_tribunal_flow", { rounds }), decision: t("multi_agent_help_tribunal_decision") },
    { mode: t("multi_agent_symposion"), flow: t("multi_agent_help_symposion_flow", { rounds }), decision: t("multi_agent_help_symposion_decision") },
  ];
});
</script>

<template>
  <div class="table">
    <table>
      <thead>
        <tr><th>{{ t("multi_agent_help_mode") }}</th><th>{{ t("multi_agent_help_flow") }}</th><th>{{ t("multi_agent_help_decision") }}</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.mode"><td class="mode">{{ row.mode }}</td><td>{{ row.flow }}</td><td>{{ row.decision }}</td></tr>
      </tbody>
    </table>
  </div>
  <section>
    <h3>{{ t("multi_agent_help_roles_title") }}</h3>
    <ul>
      <li>{{ t("multi_agent_help_role_main") }}</li>
      <li>{{ t("multi_agent_help_role_critic") }}</li>
      <li>{{ t("multi_agent_help_role_judge") }}</li>
    </ul>
    <p class="hint">{{ t("multi_agent_help_roles_hint") }}</p>
  </section>
</template>

<style scoped>
.table { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th { text-align: left; color: var(--aifred-accent-gold); }
th, td { padding: 8px 10px; border-bottom: 1px solid var(--aifred-border-light); vertical-align: top; }
.mode { font-weight: 600; white-space: nowrap; }
h3 { margin: 0 0 6px; font-size: 0.95rem; color: var(--aifred-accent-gold); }
ul { margin: 0; padding-left: 20px; }
.hint { margin: 8px 0 0; color: var(--aifred-text-secondary); font-size: 0.9rem; }
</style>
