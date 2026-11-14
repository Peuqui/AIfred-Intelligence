<script setup lang="ts">
// A page of a plugin: the slot while the plugin is on, the hint while it is off (it follows the switch live, stores/plugins.ts);
// the slot `off` adds what stays reachable below the hint (e.g. deleting stored data).
import { t } from "../i18n";
import { usePlugins } from "../stores/plugins";

defineProps<{ name: string }>();
const plugins = usePlugins();
</script>

<template>
  <slot v-if="plugins.toolOn(name)" />
  <main v-else-if="plugins.loaded" class="plugin-off">
    <p>{{ plugins.toolLabel(name) }}: {{ t("plugin_off_hint") }}</p>
    <slot name="off" />
  </main>
</template>

<style scoped>
.plugin-off { color: var(--aifred-text-secondary); padding: 12px 0; }
</style>
