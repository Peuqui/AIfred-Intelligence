<script setup lang="ts">
// The vision page: one tab per task, each its own address (#/vision/<tab>), so the live view also opens in a
// window of its own (a second screen) without code of its own. Switched off, App.vue shows the plugin's hint instead
// (route meta `plugin`, PluginGate).
import { ScrollText, Settings, User, Video } from "lucide-vue-next";
import { computed } from "vue";
import { RouterLink, useRoute } from "vue-router";
import VisionEvents from "../components/VisionEvents.vue";
import VisionLive from "../components/VisionLive.vue";
import VisionPersons from "../components/VisionPersons.vue";
import VisionSettings from "../components/VisionSettings.vue";
import HelpButton from "../components/help/HelpButton.vue";
import { t } from "../i18n";

const route = useRoute();
const tab = computed(() => String(route.params.tab ?? "live"));
const tabs = [
  { id: "live", icon: Video, label: "vision_tab_live" },
  { id: "events", icon: ScrollText, label: "vision_tab_events" },
  { id: "persons", icon: User, label: "vision_tab_persons" },
  { id: "settings", icon: Settings, label: "vision_tab_settings" },
];
</script>

<template>
  <main class="vision">
    <header class="page-head">
    <nav class="tabs">
      <RouterLink v-for="entry in tabs" :key="entry.id" :to="`/vision/${entry.id}`" :class="{ on: tab === entry.id }"><component :is="entry.icon" :size="16" /> {{ t(entry.label) }}</RouterLink>
      <HelpButton topic="vigilantia" />
    </nav>
    <!-- a tab's own head (Casus: filters and pages) is placed here, so it stays at the top with the tabs -->
    <div id="vision-head" class="tab-head"></div>
    </header>
    <VisionLive v-if="tab === 'live'" />
    <VisionEvents v-else-if="tab === 'events'" />
    <VisionPersons v-else-if="tab === 'persons'" />
    <VisionSettings v-else-if="tab === 'settings'" />
  </main>
</template>

<style scoped>
.vision { display: flex; flex-direction: column; gap: 10px; padding: 4px 0 12px; }
.tab-head { display: flex; flex-direction: column; gap: 8px; }
.tab-head:empty { display: none; }
.tabs { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.tabs a { color: var(--aifred-text-secondary); text-decoration: none; padding: 3px 10px; border-radius: 6px; border: 1px solid transparent; }
.tabs a.on { color: var(--aifred-text-primary); border-color: var(--aifred-border); background: var(--aifred-input-bg); }
</style>
