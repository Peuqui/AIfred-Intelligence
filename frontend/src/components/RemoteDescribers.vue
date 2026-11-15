<script setup lang="ts">
// The describers of other machines (maintenance): burn in (measure what a model needs there; one at a time for every
// page, the server's run, followed here) and unload what is loaded there. The order and the switches are in the
// settings (DescriberList.vue); both read the one list (stores/describers.ts).
import { Flame, PowerOff, TriangleAlert } from "lucide-vue-next";
import { computed, onBeforeUnmount } from "vue";
import { t } from "../i18n";
import { useDescribers } from "../stores/describers";

const describers = useDescribers();
const remote = computed(() => describers.list?.rows.filter((row) => row.remote) ?? []);
const measuring = computed(() => describers.list?.measuring ?? "");

async function opened(event: Event): Promise<void> {
  if ((event.target as HTMLDetailsElement).open) await describers.load();
}
onBeforeUnmount(() => describers.stop());
</script>

<template>
  <details @toggle="opened">
    <summary><Flame :size="16" /> {{ t("maintenance_remote_describers") }}</summary>
    <p class="hint">{{ t("maintenance_remote_describers_intro") }}</p>
    <p v-if="describers.notice" class="notice"><TriangleAlert :size="16" /> {{ describers.notice }}</p>
    <p v-if="describers.list && !remote.length" class="hint">{{ t("maintenance_remote_describers_none") }}</p>
    <div v-for="row in remote" :key="row.source" class="entry">
      <span class="label" :title="row.label">{{ row.label }}</span>
      <span v-if="row.loaded" class="loaded">{{ t("maintenance_remote_loaded") }}</span>
      <button :disabled="measuring !== ''" :title="t('vision_settings_remote_measure_tooltip')" @click="describers.post('/burn-in', { source: row.source })">
        <Flame :size="16" /> {{ t(measuring === row.source ? "vision_settings_remote_measuring" : "vision_settings_remote_measure") }}
      </button>
      <button v-if="row.loaded" :disabled="measuring !== ''" :title="t('vision_settings_remote_unload_tooltip')" @click="describers.post('/unload', { source: row.source })">
        <PowerOff :size="16" /> {{ t("vision_settings_remote_unload") }}
      </button>
      <span v-if="row.note" class="note" :class="{ warn: row.warn }">{{ row.note }}</span>
    </div>
  </details>
</template>

<style scoped>
.entry { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 6px 0; border-top: 1px solid var(--aifred-border); }
.label { flex: 1; min-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.loaded { font-size: 0.75rem; color: var(--aifred-accent-success); }
.note { flex-basis: 100%; font-size: 0.78rem; color: var(--aifred-text-muted); overflow-wrap: anywhere; }
.note.warn { color: var(--aifred-accent-warning); }
.hint { margin: 6px 0; font-size: 0.8rem; color: var(--aifred-text-muted); }
.notice { margin: 0; color: var(--aifred-accent-amber); --icon-own: currentColor; }
</style>
