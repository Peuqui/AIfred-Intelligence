<script setup lang="ts">
// The audio player's sources: every folder under data/media/audio/ (mostly a symlink to a folder of the sandbox) and
// the streams of the plugin's settings, each with how many files its index holds; a folder of the sandbox added (the
// file requester over the area "audio", where folders and links are made too), a source read anew into the index (a
// run of the server, followed until it ends), its index emptied, the source removed (asked first; the folder it points
// to stays). Below, the plugin's two settings: the sandbox and from how many entries a list in the spoken reply is cut.
import { BrushCleaning, CircleCheck, Folder, Hourglass, Link, Radio, RefreshCcwDot, RefreshCw, Trash2 } from "lucide-vue-next";
import { defineAsyncComponent, onBeforeUnmount, onMounted, ref } from "vue";
import { ApiError, PROGRESS_POLL_MS, api } from "../api/client";
import type { AudioLibrary } from "../api/types";
import { formatNumber, t, tCount } from "../i18n";

const library = ref<AudioLibrary | null>(null);
const notice = ref("");
const saved = ref(false);
const choosing = ref(false);
// Loaded when it opens: the file requester brings VueFinder and its libraries.
const FileRequester = defineAsyncComponent(() => import("./FileRequester.vue"));
const removing = ref("");
const root = ref("");
const listMax = ref(0);
let polling: number | null = null;

async function call<T>(work: () => Promise<T>): Promise<T | null> {
  notice.value = "";
  saved.value = false;
  try {
    return await work();
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
    return null;
  }
}
const send = <T,>(path: string, method: string, body?: object): Promise<T> =>
  api<T>(path, { method, ...(body ? { body: JSON.stringify(body) } : {}) });
const source = (label: string): string => `audio/sources/${encodeURIComponent(label)}`;

// While a source is read the list is asked again; its end brings the counts and the outcome.
function follow(state: AudioLibrary): void {
  library.value = state;
  root.value = state.picker_root;
  listMax.value = state.tts_list_max;
  if (state.indexing && polling === null) {
    polling = window.setTimeout(async () => {
      polling = null;
      const fresh = await call(() => api<AudioLibrary>("audio/sources"));
      if (fresh) follow(fresh);
    }, PROGRESS_POLL_MS);
  }
}
async function reload(message?: string): Promise<void> {
  const fresh = await call(() => api<AudioLibrary>("audio/sources"));
  if (fresh) follow(fresh);
  if (message) notice.value = message;
}

async function pick(path: string): Promise<void> {
  const done = await call(() => send<{ message: string }>("audio/sources", "POST", { path }));
  if (done) choosing.value = false;
  await reload(done?.message);
}

async function read(label: string, force: boolean): Promise<void> {
  const state = await call(() => send<AudioLibrary>(`${source(label)}/index`, "POST", { force }));
  if (state) follow(state);
}
async function clear(label: string): Promise<void> {
  const done = await call(() => send<{ message: string }>(`${source(label)}/index`, "DELETE"));
  await reload(done?.message);
}
async function remove(label: string): Promise<void> {
  removing.value = "";
  const done = await call(() => send<{ message: string }>(source(label), "DELETE"));
  await reload(done?.message);
}
async function save(body: object): Promise<void> {
  const state = await call(() => send<AudioLibrary>("audio/settings", "PUT", body));
  if (state) {
    follow(state);
    saved.value = true;
  }
}

onMounted(() => reload());
onBeforeUnmount(() => {
  if (polling !== null) window.clearTimeout(polling);
});
</script>

<template>
  <section v-if="library" class="sources">
    <div class="head">
      <strong>{{ t("audio_sources_title") }}</strong>
      <button @click="choosing = true">{{ t("audio_source_add") }}</button>
    </div>
    <p class="hint">{{ t("audio_sources_help") }}</p>
    <p v-if="library.message" class="status">{{ library.message }}</p>
    <p v-if="notice" class="notice">{{ notice }}</p>
    <p v-if="saved" class="saved"><CircleCheck :size="16" /> {{ t("audio_settings_saved") }}</p>

    <FileRequester v-if="choosing" area="audio" :title="t('audio_source_add')" pick @pick="pick" @close="choosing = false" />

    <div v-for="entry in library.sources" :key="entry.label" class="entry">
      <span class="label"><Radio v-if="entry.type === 'http_stream'" :size="16" /><Link v-else-if="entry.is_symlink" :size="16" /><Folder v-else :size="16" /> <strong>{{ entry.label }}</strong></span>
      <span class="target" :title="entry.target">{{ entry.target }}</span>
      <span class="count">{{ entry.type === "http_stream" ? t("audio_source_stream") : tCount("audio_source_indexed", entry.indexed) }}</span>
      <span v-if="entry.type !== 'http_stream'" class="actions">
        <button :disabled="library.indexing !== ''" @click="read(entry.label, false)"><Hourglass v-if="library.indexing === entry.label" :size="16" /><RefreshCw v-else :size="16" /> {{ t("audio_source_index") }}</button>
        <button :disabled="library.indexing !== ''" :title="t('audio_source_index_force')" @click="read(entry.label, true)"><RefreshCcwDot :size="16" /></button>
        <button :disabled="library.indexing !== '' || !entry.indexed" :title="t('audio_source_clear')" @click="clear(entry.label)"><BrushCleaning :size="16" /></button>
        <button v-if="removing !== entry.label" class="danger" :title="t('audio_source_remove')" @click="removing = entry.label"><Trash2 :size="16" /></button>
        <template v-else>
          <span class="confirm">{{ t("audio_source_remove_confirm", { source: entry.label }) }}</span>
          <button class="danger" @click="remove(entry.label)">{{ t("audio_source_remove") }}</button>
          <button @click="removing = ''">{{ t("audio_source_cancel") }}</button>
        </template>
      </span>
    </div>

    <div class="line setting">
      <label class="name" :title="t('audio_settings_root_help')">{{ t("audio_settings_root") }}</label>
      <input v-model="root" class="wide" />
      <button :disabled="root === library.picker_root" @click="save({ picker_root: root })">{{ t("audio_settings_save") }}</button>
    </div>
    <div class="line setting">
      <label class="name" :title="t('audio_settings_list_max_help')">{{ t("audio_settings_list_max") }}</label>
      <input v-model.number="listMax" class="number" type="number" min="1" />
      <button :disabled="listMax === library.tts_list_max" @click="save({ tts_list_max: listMax })">{{ t("audio_settings_save") }}</button>
      <span class="hint">{{ formatNumber(library.tts_list_max) }}</span>
    </div>
  </section>
</template>

<style scoped>
.sources { display: flex; flex-direction: column; gap: 8px; }
.head, .line { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.head { justify-content: space-between; }
.grow { flex: 1; }
.entry { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 6px 0; border-top: 1px solid var(--aifred-border); }
.label { min-width: 160px; }
.target { flex: 1; min-width: 160px; font-size: 0.78rem; color: var(--aifred-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.count { font-size: 0.8rem; color: var(--aifred-text-secondary); }
.actions { display: inline-flex; gap: 4px; align-items: center; flex-wrap: wrap; }
.setting .name { min-width: 260px; font-weight: 600; font-size: 0.88rem; }
.wide { min-width: 260px; }
.number { width: 80px; }
.confirm { font-size: 0.82rem; color: var(--aifred-danger); }
select, input { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 3px 8px; font: inherit; font-size: 0.85rem; }
button { padding: 3px 8px; font-size: 0.85rem; }
.status { margin: 0; font-size: 0.85rem; }
.hint { margin: 0; font-size: 0.8rem; color: var(--aifred-text-muted); }
.notice { margin: 0; color: var(--aifred-accent-amber); }
.saved { margin: 0; color: var(--aifred-accent-success); --icon-own: currentColor; }
</style>
