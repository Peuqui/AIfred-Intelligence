<script setup lang="ts">
// The describer list: who describes images, in which order (the first one switched on that can right now). This
// machine's image model, the main model when it sees itself, and the models of other machines, each with what holds
// it back or what it needs as measured. Burning in and unloading a model of another machine is on the maintenance
// page (RemoteDescribers.vue); the list follows its burn-in.
import { ChevronDown, ChevronUp, TriangleAlert } from "lucide-vue-next";
import { onActivated, onBeforeUnmount } from "vue";
import { t } from "../i18n";
import { useDescribers } from "../stores/describers";

const describers = useDescribers();

// Read anew whenever the page is shown: this machine's row names the image model, which the vision settings change.
onActivated(() => describers.load());
onBeforeUnmount(() => describers.stop());
</script>

<template>
  <article class="describers">
    <strong>{{ t("vision_settings_describers_label") }}</strong>
    <p class="hint">{{ t("vision_settings_remote_help") }}</p>
    <p class="hint"><RouterLink to="/maintenance">{{ t("vision_settings_remote_maintenance_link") }}</RouterLink></p>
    <p v-if="describers.notice" class="notice"><TriangleAlert :size="16" /> {{ describers.notice }}</p>
    <template v-if="describers.list">
      <div v-for="row in describers.list.rows" :key="`${row.index}-${row.source}`" class="entry" :class="{ off: !row.enabled }">
        <span class="order">
          <button :title="t('tts_entry_up')" :disabled="row.index === 0" @click="describers.post('/move', { index: row.index, delta: -1 })"><ChevronUp :size="14" /></button>
          <button :title="t('tts_entry_down')" :disabled="row.index === describers.list.rows.length - 1" @click="describers.post('/move', { index: row.index, delta: 1 })"><ChevronDown :size="14" /></button>
        </span>
        <input type="checkbox" :checked="row.enabled" @change="describers.post('/switch', { index: row.index, enabled: !row.enabled })" />
        <span class="label" :title="row.label">{{ row.label }}</span>
        <span v-if="row.note" class="note" :class="{ warn: row.warn }">{{ row.note }}</span>
      </div>
    </template>
  </article>
</template>

<style scoped>
.describers { display: flex; flex-direction: column; gap: 6px; padding: 12px 14px; background: var(--aifred-card-bg); border: 1px solid var(--aifred-border); border-radius: 10px; }
.entry { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 6px 0; border-top: 1px solid var(--aifred-border); }
.entry.off .label { opacity: 0.6; }
.order { display: inline-flex; gap: 2px; }
.order button { padding: 1px 5px; }
.label { flex: 1; min-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.note { flex-basis: 100%; font-size: 0.78rem; color: var(--aifred-text-muted); padding-left: 56px; overflow-wrap: anywhere; }
.note.warn { color: var(--aifred-accent-warning); }
button { padding: 3px 8px; font-size: 0.85rem; }
input[type="checkbox"] { accent-color: var(--aifred-primary); width: 16px; height: 16px; }
.hint { margin: 0; font-size: 0.8rem; color: var(--aifred-text-muted); }
.notice { margin: 0; color: var(--aifred-accent-amber); --icon-own: currentColor; }
</style>
