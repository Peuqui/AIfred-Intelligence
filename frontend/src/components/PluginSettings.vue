<script setup lang="ts">
// The plugins on the settings page (GET plugins): the channels with their switches (on, listening, answering on
// their own) and security tier, the tool plugins with theirs; 💡 shows a plugin's description under its row (on a phone
// there is no tooltip), ⚙️ opens its settings form. Switching on a channel that is not set up opens its form instead.
import { Lightbulb, Lock, Settings, TriangleAlert } from "lucide-vue-next";
import { computed, ref } from "vue";
import { ApiError, api } from "../api/client";
import type { PluginOverview } from "../api/types";
import { t } from "../i18n";
import { usePlugins } from "../stores/plugins";
import PluginForm from "./PluginForm.vue";

const plugins = usePlugins();
const overview = computed(() => plugins.overview);
const notice = ref("");
const editing = ref("");
// The plugins whose description is shown under their row.
const described = ref<string[]>([]);
const describe = (name: string): void => {
  described.value = described.value.includes(name) ? described.value.filter((other) => other !== name) : [...described.value, name];
};

async function call(work: () => Promise<PluginOverview>): Promise<void> {
  notice.value = "";
  try {
    plugins.overview = await work();
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
  }
}
const send = (path: string, method: string, body: object): Promise<PluginOverview> =>
  api<PluginOverview>(path, { method, body: JSON.stringify(body) });

function channel(name: string, configured: boolean, body: Record<string, boolean | number>): void {
  if (body.monitor && !configured) {
    editing.value = name;
    return;
  }
  void call(() => send(`plugins/channels/${name}`, "PATCH", body));
}
const tool = (name: string, enabled: boolean): Promise<void> => call(() => send(`plugins/tools/${name}`, "PUT", { enabled }));
const checked = (event: Event): boolean => (event.target as HTMLInputElement).checked;
const picked = (event: Event): number => Number((event.target as HTMLSelectElement).value);
</script>

<template>
  <section v-if="overview" class="plugins">
    <p v-if="notice" class="notice"><TriangleAlert :size="16" /> {{ notice }}</p>
    <h3>{{ t("plugin_channels") }}</h3>
    <div v-for="row in overview.channels" :key="row.name" class="row" :class="{ off: !row.monitor }">
      <span class="name" :title="row.description">{{ row.label }}</span>
      <select v-if="row.tier !== null" :value="String(row.tier)" :title="t('security_tiers_title')" :disabled="!row.monitor" @change="channel(row.name, row.configured, { tier: picked($event) })">
        <option v-for="tier in overview.tiers" :key="tier.id" :value="tier.id">{{ tier.label }}</option>
      </select>
      <label v-if="!row.always_reply" class="switch">
        <input type="checkbox" :checked="row.listener" :disabled="!row.monitor" @change="channel(row.name, row.configured, { listener: checked($event) })" /> {{ t("plugin_channel_listener") }}
      </label>
      <label v-if="!row.always_reply" class="switch">
        <input type="checkbox" :checked="row.auto_reply" :disabled="!row.monitor" @change="channel(row.name, row.configured, { auto_reply: checked($event) })" /> {{ t("auto_reply") }}
      </label>
      <span class="spacer" />
      <button v-if="row.description" class="icon help" :class="{ on: described.includes(row.name) }" :title="t('plugin_description_title')" @click="describe(row.name)"><Lightbulb :size="16" /></button>
      <button :title="t('cred_title_suffix')" @click="editing = row.name"><Settings :size="16" /></button>
      <label class="switch main">
        <input type="checkbox" :checked="row.monitor" @change="channel(row.name, row.configured, { monitor: checked($event) })" />
      </label>
      <p v-if="described.includes(row.name)" class="description">{{ row.description }}</p>
    </div>

    <h3>{{ t("plugin_tools") }}</h3>
    <div v-for="row in overview.tools" :key="row.name" class="row" :class="{ off: !row.enabled }">
      <span class="name" :title="row.description">{{ row.label }}</span>
      <span v-if="row.needs_connection" class="hint"><Lock :size="16" /> {{ t("plugin_needs_connection") }}</span>
      <span class="spacer" />
      <button v-if="row.description" class="icon help" :class="{ on: described.includes(row.name) }" :title="t('plugin_description_title')" @click="describe(row.name)"><Lightbulb :size="16" /></button>
      <button v-if="row.has_settings" :title="t('cred_title_suffix')" @click="editing = row.name"><Settings :size="16" /></button>
      <label class="switch main">
        <input type="checkbox" :checked="row.enabled" @change="tool(row.name, checked($event))" />
      </label>
      <p v-if="described.includes(row.name)" class="description">{{ row.description }}</p>
    </div>
    <PluginForm v-if="editing" :plugin="editing" @close="editing = ''" @saved="plugins.load()" />
  </section>
</template>

<style scoped>
.plugins { display: flex; flex-direction: column; gap: 6px; }
h3 { margin: 10px 0 2px; font-size: 0.95rem; color: var(--aifred-text-secondary); }
.row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 4px 8px; border-radius: 8px; background: var(--aifred-input-bg); }
.row.off .name { color: var(--aifred-text-muted); }
.name { min-width: 200px; font-size: 0.9rem; }
.spacer { flex: 1; }
.switch { display: inline-flex; align-items: center; gap: 4px; font-size: 0.8rem; color: var(--aifred-text-secondary); }
input[type="checkbox"] { accent-color: var(--aifred-primary); width: 16px; height: 16px; }
select { background: var(--aifred-card-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 2px 8px; font: inherit; font-size: 0.8rem; }
button { padding: 2px 8px; font-size: 0.8rem; }
.hint { font-size: 0.8rem; color: var(--aifred-accent-amber); --icon-own: currentColor; }
.description { flex-basis: 100%; margin: 0 0 4px; font-size: 0.85rem; line-height: 1.45; color: var(--aifred-text-secondary); white-space: pre-line; }
.notice { margin: 0; color: var(--aifred-accent-amber); --icon-own: currentColor; }
</style>
