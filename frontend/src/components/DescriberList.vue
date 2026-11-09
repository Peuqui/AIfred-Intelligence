<script setup lang="ts">
// The describer list: who describes images, in which order (the first one switched on that can right now). This
// machine's image model, the main model when it sees itself, and the models of other machines, each with what holds
// it back or what it needs as measured. A describer of another machine can be burnt in there; the run is the
// server's (one at a time, for every page), this list follows it until it ends.
import { onBeforeUnmount, onMounted, ref } from "vue";
import { ApiError, PROGRESS_POLL_MS, api } from "../api/client";
import type { Describers } from "../api/types";
import { t } from "../i18n";

const list = ref<Describers | null>(null);
const notice = ref("");
let polling: number | null = null;

async function call(work: () => Promise<Describers>): Promise<void> {
  notice.value = "";
  try {
    follow(await work());
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
  }
}
const post = (path: string, body: object): Promise<Describers> => api<Describers>(`vision/describers${path}`, { method: "POST", body: JSON.stringify(body) });

// While a burn-in runs the list is asked again; its end brings the row's new note.
function follow(describers: Describers): void {
  list.value = describers;
  if (describers.measuring && polling === null) {
    polling = window.setTimeout(() => {
      polling = null;
      void call(() => api<Describers>("vision/describers"));
    }, PROGRESS_POLL_MS);
  }
}

onMounted(() => call(() => api<Describers>("vision/describers")));
onBeforeUnmount(() => {
  if (polling !== null) window.clearTimeout(polling);
});
</script>

<template>
  <article class="describers">
    <strong>{{ t("vision_settings_describers_label") }}</strong>
    <p class="hint">{{ t("vision_settings_remote_help") }}</p>
    <p v-if="notice" class="notice">⚠️ {{ notice }}</p>
    <template v-if="list">
      <div v-for="row in list.rows" :key="`${row.index}-${row.source}`" class="entry" :class="{ off: !row.enabled }">
        <span class="order">
          <button :title="t('tts_entry_up')" :disabled="row.index === 0" @click="call(() => post('/move', { index: row.index, delta: -1 }))">▲</button>
          <button :title="t('tts_entry_down')" :disabled="row.index === list.rows.length - 1" @click="call(() => post('/move', { index: row.index, delta: 1 }))">▼</button>
        </span>
        <input type="checkbox" :checked="row.enabled" @change="call(() => post('/switch', { index: row.index, enabled: !row.enabled }))" />
        <span class="label" :title="row.label">{{ row.label }}</span>
        <button
          v-if="row.remote"
          :disabled="list.measuring !== ''"
          :title="t('vision_settings_remote_measure_tooltip')"
          @click="call(() => post('/burn-in', { source: row.source }))"
        >🔥 {{ t(list.measuring === row.source ? "vision_settings_remote_measuring" : "vision_settings_remote_measure") }}</button>
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
button { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 3px 8px; font: inherit; font-size: 0.85rem; cursor: pointer; }
button:disabled { opacity: 0.5; cursor: default; }
input[type="checkbox"] { accent-color: var(--aifred-primary); width: 16px; height: 16px; }
.hint { margin: 0; font-size: 0.8rem; color: var(--aifred-text-muted); }
.notice { margin: 0; color: var(--aifred-accent-amber); }
</style>
