<script setup lang="ts">
// The vision page: one tab per task, each its own address (#/vision/<tab>), so the live view also opens in a
// window of its own (a second screen) without code of its own.
import { ScrollText, Settings, User, Video } from "lucide-vue-next";
import { computed, onMounted, ref } from "vue";
import { RouterLink, useRoute } from "vue-router";
import VisionEvents from "../components/VisionEvents.vue";
import VisionLive from "../components/VisionLive.vue";
import VisionPersons from "../components/VisionPersons.vue";
import VisionSettings from "../components/VisionSettings.vue";
import HelpButton from "../components/help/HelpButton.vue";
import { api } from "../api/client";
import type { PluginOverview } from "../api/types";
import { t } from "../i18n";

const route = useRoute();
// Off means off: the plugin's endpoints refuse while it is switched off, so the page says so instead of showing errors.
const visionOn = ref<boolean | null>(null);
onMounted(async () => {
  const overview = await api<PluginOverview>("plugins");
  visionOn.value = overview.tools.find((tool) => tool.name === "vision")?.enabled ?? false;
});
const tab = computed(() => String(route.params.tab ?? "live"));
const tabs = [
  { id: "live", icon: Video, label: "vision_tab_live" },
  { id: "events", icon: ScrollText, label: "vision_tab_events" },
  { id: "persons", icon: User, label: "vision_tab_persons" },
  { id: "settings", icon: Settings, label: "vision_tab_settings" },
];
</script>

<template>
  <main v-if="visionOn === false" class="vision">
    <p class="off">{{ t("plugin_off_hint") }}</p>
  </main>
  <main v-else-if="visionOn" class="vision">
    <nav class="tabs">
      <RouterLink v-for="entry in tabs" :key="entry.id" :to="`/vision/${entry.id}`" :class="{ on: tab === entry.id }"><component :is="entry.icon" :size="16" /> {{ t(entry.label) }}</RouterLink>
      <HelpButton topic="vigilantia" />
    </nav>
    <VisionLive v-if="tab === 'live'" />
    <VisionEvents v-else-if="tab === 'events'" />
    <VisionPersons v-else-if="tab === 'persons'" />
    <VisionSettings v-else-if="tab === 'settings'" />
  </main>
</template>

<style scoped>
.vision { display: flex; flex-direction: column; gap: 10px; padding: 12px 0; min-height: 0; overflow-y: auto; }
.off { color: var(--aifred-text-secondary); }
.tabs { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.tabs a { color: var(--aifred-text-secondary); text-decoration: none; padding: 3px 10px; border-radius: 6px; border: 1px solid transparent; }
.tabs a.on { color: var(--aifred-text-primary); border-color: var(--aifred-border); background: var(--aifred-input-bg); }
</style>
