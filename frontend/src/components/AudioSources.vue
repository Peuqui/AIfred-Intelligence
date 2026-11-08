<script setup lang="ts">
// The audio player's sources: every folder under data/media/audio/ (mostly a symlink to a folder of the sandbox) and
// the streams of the plugin's settings, each with how many files its index holds; a folder of the sandbox added (a
// small folder browser), a source read anew into the index (a run of the server, followed until it ends), its index
// emptied, the source removed (asked first; the folder it points to stays). Below, the plugin's two settings: the
// sandbox and from how many entries a list in the spoken reply is cut.
import { onBeforeUnmount, onMounted, ref } from "vue";
import { ApiError, PROGRESS_POLL_MS, api } from "../api/client";
import type { AudioFolders, AudioLibrary } from "../api/types";
import { formatNumber, t, tCount } from "../i18n";

const library = ref<AudioLibrary | null>(null);
const notice = ref("");
const browsing = ref<AudioFolders | null>(null);
const removing = ref("");
const root = ref("");
const listMax = ref(0);
let polling: number | null = null;

async function call<T>(work: () => Promise<T>): Promise<T | null> {
  notice.value = "";
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

async function browse(path: string): Promise<void> {
  browsing.value = (await call(() => api<AudioFolders>(`audio/sources/folders?path=${encodeURIComponent(path)}`))) ?? browsing.value;
}
async function pick(): Promise<void> {
  if (!browsing.value) return;
  const done = await call(() => send<{ message: string }>("audio/sources", "POST", { path: browsing.value?.path ?? "" }));
  if (done) browsing.value = null;
  await reload(done?.message);
}
const parent = (path: string): string => path.split("/").slice(0, -1).join("/");

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
    notice.value = t("audio_settings_saved");
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
      <button v-if="!browsing" @click="browse('')">{{ t("audio_source_add") }}</button>
    </div>
    <p class="hint">{{ t("audio_sources_help") }}</p>
    <p v-if="library.message" class="status">{{ library.message }}</p>
    <p v-if="notice" class="notice">{{ notice }}</p>

    <div v-if="browsing" class="browser">
      <div class="line">
        <code>{{ library.picker_root }}/{{ browsing.path }}</code>
        <span class="grow"></span>
        <button class="primary" @click="pick">{{ t("audio_source_pick") }}</button>
        <button @click="browsing = null">{{ t("audio_source_cancel") }}</button>
      </div>
      <button v-if="browsing.path" class="folder" @click="browse(parent(browsing.path))">{{ t("audio_source_up") }}</button>
      <button v-for="folder in browsing.folders" :key="folder.path" class="folder" @click="browse(folder.path)">
        {{ folder.is_symlink ? "🔗" : "📁" }} {{ folder.name }}
      </button>
    </div>

    <div v-for="entry in library.sources" :key="entry.label" class="entry">
      <span class="label">{{ entry.type === "http_stream" ? "📻" : entry.is_symlink ? "🔗" : "📁" }} <strong>{{ entry.label }}</strong></span>
      <span class="target" :title="entry.target">{{ entry.target }}</span>
      <span class="count">{{ entry.type === "http_stream" ? t("audio_source_stream") : tCount("audio_source_indexed", entry.indexed) }}</span>
      <span v-if="entry.type !== 'http_stream'" class="actions">
        <button :disabled="library.indexing !== ''" @click="read(entry.label, false)">{{ library.indexing === entry.label ? "⏳" : "🔄" }} {{ t("audio_source_index") }}</button>
        <button :disabled="library.indexing !== ''" :title="t('audio_source_index_force')" @click="read(entry.label, true)">♻️</button>
        <button :disabled="library.indexing !== '' || !entry.indexed" :title="t('audio_source_clear')" @click="clear(entry.label)">🧹</button>
        <button v-if="removing !== entry.label" class="danger" :title="t('audio_source_remove')" @click="removing = entry.label">🗑️</button>
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
.browser { display: flex; flex-direction: column; gap: 4px; padding: 8px; border: 1px solid var(--aifred-border); border-radius: 8px; max-height: 320px; overflow-y: auto; }
.folder { text-align: left; }
.setting .name { min-width: 260px; font-weight: 600; font-size: 0.88rem; }
.wide { min-width: 260px; }
.number { width: 80px; }
.confirm { font-size: 0.82rem; color: var(--aifred-danger); }
select, input, button { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 3px 8px; font: inherit; font-size: 0.85rem; }
button { cursor: pointer; }
button:disabled { opacity: 0.5; cursor: default; }
button.primary { border-color: var(--aifred-primary); color: var(--aifred-primary); }
button.danger { color: var(--aifred-danger); }
.status { margin: 0; font-size: 0.85rem; }
.hint { margin: 0; font-size: 0.8rem; color: var(--aifred-text-muted); }
.notice { margin: 0; color: var(--aifred-accent-amber); }
</style>
