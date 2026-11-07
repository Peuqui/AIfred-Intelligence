<script setup lang="ts">
// A further document source (Nextcloud …) on the documents page: its folders as a trail, a search over the
// whole source, download, upload into a writable one (not indexed: the user decides per file), and
// indexing files, a selection or the open folder into the search, or taking a file out of it again.
import { computed, ref } from "vue";
import type { SourceRow } from "../api/types";
import { t, tCount } from "../i18n";
import { useDocuments } from "../stores/documents";

const docs = useDocuments();
const rows = computed(() => docs.remote?.items ?? []);
const running = computed(() => !!docs.indexing?.running);

const selected = ref<string[]>([]);
const allSelected = computed(() => !!rows.value.length && selected.value.length === rows.value.length);
function toggle(id: string): void {
  selected.value = selected.value.includes(id) ? selected.value.filter((other) => other !== id) : [...selected.value, id];
}
function toggleAll(): void {
  selected.value = allSelected.value ? [] : rows.value.map((row) => row.id);
}

async function enter(row: SourceRow): Promise<void> {
  selected.value = [];
  await docs.enterRemote(row.id, row.name);
}
async function back(depth: number): Promise<void> {
  selected.value = [];
  await docs.backRemote(depth);
}

const searchText = ref("");
async function search(): Promise<void> {
  selected.value = [];
  await docs.searchRemote(searchText.value);
}

// Indexing: the files the index reads and every folder (with what is below it).
function indexRows(chosen: SourceRow[]): Promise<void> {
  return docs.indexRemote(
    chosen.filter((row) => row.type === "file" && row.indexable).map((row) => ({ id: row.id, name: row.name })),
    chosen.filter((row) => row.type === "folder").map((row) => row.id),
  );
}
const indexSelected = (): Promise<void> => indexRows(rows.value.filter((row) => selected.value.includes(row.id)));

const picker = ref<HTMLInputElement | null>(null);
async function picked(): Promise<void> {
  const files = [...(picker.value?.files ?? [])];
  if (picker.value) picker.value.value = "";
  if (files.length) await docs.uploadRemote(files);
}
</script>

<template>
  <section class="source">
    <div class="bar">
      <nav class="trail">
        <button :title="t('doc_to_root')" @click="back(0)">{{ docs.sources.find((found) => found.key === docs.source)?.label }}</button>
        <template v-for="(part, depth) in docs.trail" :key="part.id">
          <span>/</span>
          <button @click="back(depth + 1)">{{ part.name }}</button>
        </template>
      </nav>
      <form class="search" @submit.prevent="search">
        <input v-model="searchText" :placeholder="t('doc_remote_search_placeholder')" />
        <button type="submit">{{ t("doc_remote_search") }}</button>
      </form>
      <div class="actions">
        <button v-if="docs.trail.length" :title="t('doc_up_one_level')" @click="back(docs.trail.length - 1)">⬆</button>
        <template v-if="docs.remote?.writable">
          <button :title="t('doc_hint')" @click="picker?.click()">⬆️ {{ t("upload_document") }}</button>
          <input ref="picker" type="file" multiple hidden @change="picked" />
        </template>
        <button
          v-if="rows.length && !docs.query"
          :title="t('doc_index_folder_recursive')"
          :disabled="running"
          @click="indexRows(rows)"
        >📚</button>
        <button :title="t('doc_refresh')" @click="back(docs.trail.length)">🔄</button>
        <button v-if="selected.length" :disabled="running" @click="indexSelected">
          📚 {{ t("doc_index") }} ({{ selected.length }})
        </button>
      </div>
    </div>
    <p v-if="docs.query" class="hits">{{ t("doc_remote_search_hits") }} „{{ docs.query }}“</p>

    <table v-if="rows.length" class="list">
      <thead>
        <tr>
          <th><input type="checkbox" :checked="allSelected" :title="t('doc_select_all')" @change="toggleAll" /></th>
          <th class="name"></th>
          <th></th>
          <th></th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.id">
          <td><input type="checkbox" :checked="selected.includes(row.id)" @change="toggle(row.id)" /></td>
          <td class="name">
            <button v-if="row.type === 'folder'" class="link" @click="enter(row)">📁 {{ row.name }}</button>
            <span v-else>📄 {{ row.name }}</span>
            <span class="size">{{ row.size }}</span>
            <span v-if="docs.query" class="where">{{ row.id }}</span>
          </td>
          <td class="dates">{{ row.modified }}</td>
          <td><span v-if="row.indexed" class="indexed">{{ tCount("doc_chunk_count", row.chunks) }}</span></td>
          <td class="row-actions">
            <a v-if="row.type === 'file'" :href="row.download_url" :title="t('doc_download')" download>⬇</a>
            <button v-if="row.indexed" :title="t('doc_deindex')" @click="docs.deindexRemote([row.id])">📤</button>
            <button
              v-else-if="row.type === 'file' && row.indexable"
              :title="t('doc_index')"
              :disabled="running"
              @click="indexRows([row])"
            >📚</button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else-if="docs.remote" class="empty">{{ t("doc_folder_empty") }}</p>
  </section>
</template>

<style scoped>
.source { display: flex; flex-direction: column; gap: 10px; }
.bar { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.trail { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; color: var(--aifred-text-muted); }
.search { display: flex; gap: 6px; }
.actions { display: flex; gap: 8px; margin-left: auto; flex-wrap: wrap; }
button, input:not([type="checkbox"]) { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 4px 8px; font: inherit; cursor: pointer; }
.trail button, .link, .row-actions button { background: none; border: none; padding: 2px 4px; }
.trail button:hover, .link:hover { text-decoration: underline; }
button:disabled { opacity: 0.5; cursor: default; }
input[type="checkbox"] { accent-color: var(--aifred-primary); }
.hits { margin: 0; color: var(--aifred-text-secondary); }
.list { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
.list th { text-align: left; font-weight: 500; font-size: 0.75rem; color: var(--aifred-text-muted); padding: 4px 6px; }
.list td { padding: 6px; border-top: 1px solid var(--aifred-border); vertical-align: middle; }
.name { width: 55%; overflow-wrap: anywhere; }
.size, .where { margin-left: 8px; font-size: 0.75rem; color: var(--aifred-text-muted); }
.dates { font-size: 0.75rem; color: var(--aifred-text-secondary); white-space: nowrap; }
.indexed { font-size: 0.75rem; color: var(--aifred-accent-cyan); white-space: nowrap; }
.row-actions { white-space: nowrap; text-align: right; }
.row-actions a { text-decoration: none; padding: 2px 4px; color: inherit; }
.empty { color: var(--aifred-text-muted); }
@media (max-width: 760px) {
  .dates { display: none; }
  .name { width: auto; }
}
</style>
