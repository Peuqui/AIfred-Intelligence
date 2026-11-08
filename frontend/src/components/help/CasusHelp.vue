<script setup lang="ts">
// The actions of the events tab: describe one event, describe all per occurrence, show them grouped.
import { t } from "../../i18n";

const ROWS = [
  [t("casus_help_sparkles_label"), t("casus_help_sparkles_body")],
  [t("casus_help_bulk_label"), t("casus_help_bulk_body")],
  [t("casus_help_grouped_label"), t("casus_help_grouped_body")],
];
</script>

<template>
  <p class="intro">{{ t("casus_help_intro") }}</p>
  <section v-for="[label, body] in ROWS" :key="label">
    <h3>{{ label }}</h3>
    <p>{{ body }}</p>
  </section>
</template>

<style scoped>
.intro { margin: 0; color: var(--aifred-text-secondary); }
section { padding-top: 10px; border-top: 1px solid var(--aifred-border); }
h3 { margin: 0 0 4px; font-size: 0.95rem; color: var(--aifred-accent-gold); }
p { margin: 0; font-size: 0.9rem; }
</style>
