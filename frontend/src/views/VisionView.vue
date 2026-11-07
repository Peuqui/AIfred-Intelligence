<script setup lang="ts">
// The vision page: one tab per task, each its own address (#/vision/<tab>), so the live view also opens in a
// window of its own (a second screen) without code of its own.
import { computed } from "vue";
import { RouterLink, useRoute } from "vue-router";
import VisionEvents from "../components/VisionEvents.vue";
import VisionLive from "../components/VisionLive.vue";
import VisionPersons from "../components/VisionPersons.vue";
import VisionSettings from "../components/VisionSettings.vue";
import { t } from "../i18n";

const route = useRoute();
const tab = computed(() => String(route.params.tab ?? "live"));
const tabs = [
  { id: "live", label: "vision_tab_live" },
  { id: "events", label: "vision_tab_events" },
  { id: "persons", label: "vision_tab_persons" },
  { id: "settings", label: "vision_tab_settings" },
];
</script>

<template>
  <main class="vision">
    <nav class="tabs">
      <RouterLink v-for="entry in tabs" :key="entry.id" :to="`/vision/${entry.id}`" :class="{ on: tab === entry.id }">{{ t(entry.label) }}</RouterLink>
    </nav>
    <VisionLive v-if="tab === 'live'" />
    <VisionEvents v-else-if="tab === 'events'" />
    <VisionPersons v-else-if="tab === 'persons'" />
    <VisionSettings v-else-if="tab === 'settings'" />
  </main>
</template>

<style scoped>
.vision { display: flex; flex-direction: column; gap: 10px; padding: 12px 0; min-height: 0; overflow-y: auto; }
.tabs { display: flex; gap: 6px; }
.tabs a { color: var(--aifred-text-secondary); text-decoration: none; padding: 3px 10px; border-radius: 6px; border: 1px solid transparent; }
.tabs a.on { color: var(--aifred-text-primary); border-color: var(--aifred-border); background: var(--aifred-input-bg); }
</style>
