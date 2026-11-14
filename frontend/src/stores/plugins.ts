// What the plugins are switched to (GET /api/plugins): the one place the interface asks whether a tool plugin is on
// or a channel's main switch (monitor) is on. The settings page writes its answers here; the server announces a
// switch (lib/plugin_admin.py: notify_changed("plugins")), so every tab and device follows without reloading.
import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { api } from "../api/client";
import type { PluginOverview } from "../api/types";
import { onResourceChanged } from "../lib/resourceChanges";

export const usePlugins = defineStore("plugins", () => {
  const overview = ref<PluginOverview | null>(null);

  async function load(): Promise<void> {
    overview.value = await api<PluginOverview>("plugins");
  }

  const loaded = computed(() => overview.value !== null);
  const toolOn = (name: string): boolean => overview.value?.tools.find((row) => row.name === name)?.enabled ?? false;
  const channelOn = (name: string): boolean => overview.value?.channels.find((row) => row.name === name)?.monitor ?? false;
  const toolLabel = (name: string): string => overview.value?.tools.find((row) => row.name === name)?.label ?? name;

  onResourceChanged("plugins", () => void load());

  return { overview, loaded, load, toolOn, channelOn, toolLabel };
});
