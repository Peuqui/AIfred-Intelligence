<script setup lang="ts">
// The actions of the events tab: describe one event, describe all per occurrence, show them grouped.
import { Sparkles } from "lucide-vue-next";
import { t } from "../../i18n";

// The icon is the one on the button the row explains; the grouping is a plain checkbox, without one.
const ROWS = [
  { icon: Sparkles, label: t("casus_help_sparkles_label"), body: t("casus_help_sparkles_body") },
  { icon: Sparkles, label: t("casus_help_bulk_label"), body: t("casus_help_bulk_body") },
  { icon: null, label: t("casus_help_grouped_label"), body: t("casus_help_grouped_body") },
];
</script>

<template>
  <p class="intro">{{ t("casus_help_intro") }}</p>
  <section v-for="row in ROWS" :key="row.label">
    <h3><component :is="row.icon" v-if="row.icon" :size="14" /> {{ row.label }}</h3>
    <p>{{ row.body }}</p>
  </section>
</template>

<style scoped>
.intro { margin: 0; color: var(--aifred-text-secondary); }
section { padding-top: 10px; border-top: 1px solid var(--aifred-border); }
h3 { margin: 0 0 4px; font-size: 0.95rem; color: var(--aifred-accent-gold); }
p { margin: 0; font-size: 0.9rem; }
</style>
