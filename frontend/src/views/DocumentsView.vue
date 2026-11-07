<script setup lang="ts">
// The local documents folder (data/documents/): browse, new folder, rename, delete from disk and/or the
// search index, preview through the index's parser, download, the path a tool takes. Uploading and
// indexing, and the further sources (Nextcloud …), follow.
import { computed, onMounted, ref } from "vue";
import type { DocumentPreview, DocumentRow } from "../api/types";
import ModalDialog from "../components/ModalDialog.vue";
import { t } from "../i18n";
import { useDocuments } from "../stores/documents";

const docs = useDocuments();
onMounted(() => void docs.open(""));

const folder = computed(() => docs.listing?.folder ?? "");
// The folder as a trail of its parts, each a place to go back to.
const trail = computed(() => {
  const parts = folder.value ? folder.value.split("/") : [];
  return parts.map((name, index) => ({ name, path: parts.slice(0, index + 1).join("/") }));
});
const pathOf = (row: DocumentRow): string => (folder.value ? `${folder.value}/${row.name}` : row.name);

const selected = ref<string[]>([]);
const allSelected = computed(() => !!docs.listing?.items.length && selected.value.length === docs.listing.items.length);

async function go(path: string): Promise<void> {
  selected.value = [];
  await docs.open(path);
}

const up = (): Promise<void> => go(folder.value.split("/").slice(0, -1).join("/"));

function toggle(name: string): void {
  selected.value = selected.value.includes(name) ? selected.value.filter((other) => other !== name) : [...selected.value, name];
}

function toggleAll(): void {
  selected.value = allSelected.value ? [] : (docs.listing?.items.map((row) => row.name) ?? []);
}

// New folder and rename: one name field, open for one of them at a time.
const naming = ref<{ old: string | null; name: string } | null>(null);
async function named(): Promise<void> {
  if (!naming.value) return;
  const { old, name } = naming.value;
  naming.value = null;
  if (!name.trim() || name === old) return;
  if (old === null) await docs.newFolder(name.trim());
  else await docs.rename(old, name.trim());
}

// Deleting asks first: from the disk, from the index, or both.
const deleting = ref<{ files: string[]; folders: string[]; fromDisk: boolean; fromIndex: boolean } | null>(null);
function askDelete(names: string[]): void {
  const rows = docs.listing?.items.filter((row) => names.includes(row.name)) ?? [];
  deleting.value = {
    files: rows.filter((row) => row.type === "file").map((row) => row.name),
    folders: rows.filter((row) => row.type === "folder").map((row) => row.name),
    fromDisk: true,
    fromIndex: true,
  };
}
async function confirmDelete(): Promise<void> {
  if (!deleting.value) return;
  const { files, folders, fromDisk, fromIndex } = deleting.value;
  deleting.value = null;
  await docs.remove(files, folders, fromDisk, fromIndex);
  selected.value = [];
}

const preview = ref<DocumentPreview | null>(null);
async function show(row: DocumentRow): Promise<void> {
  preview.value = await docs.preview(pathOf(row));
}

async function copyPath(row: DocumentRow): Promise<void> {
  await navigator.clipboard.writeText(pathOf(row));
}
</script>

<template>
  <main class="documents">
    <header class="bar">
      <h2>{{ t("doc_manager_title") }}</h2>
      <nav class="trail">
        <button :title="t('doc_to_root')" @click="go('')">{{ t("doc_source_local") }}</button>
        <template v-for="part in trail" :key="part.path">
          <span>/</span>
          <button @click="go(part.path)">{{ part.name }}</button>
        </template>
      </nav>
      <div class="actions">
        <button v-if="folder" :title="t('doc_up_one_level')" @click="up">⬆</button>
        <button :title="t('doc_folder_create')" @click="naming = { old: null, name: '' }">📁＋</button>
        <button :title="t('doc_refresh')" @click="go(folder)">🔄</button>
        <button v-if="selected.length" class="danger" @click="askDelete(selected)">
          🗑️ {{ t("doc_delete_selected") }} ({{ selected.length }})
        </button>
      </div>
    </header>
    <p v-if="docs.notice" class="notice">⚠️ {{ docs.notice }}</p>

    <form v-if="naming" class="naming" @submit.prevent="named">
      <input v-model="naming.name" :placeholder="t('doc_folder_name')" autofocus />
      <button type="submit">{{ naming.old === null ? t("doc_create") : t("doc_rename") }}</button>
      <button type="button" @click="naming = null">{{ t("doc_cancel") }}</button>
    </form>

    <table v-if="docs.listing && docs.listing.items.length" class="list">
      <thead>
        <tr>
          <th><input type="checkbox" :checked="allSelected" :title="t('doc_select_all')" @change="toggleAll" /></th>
          <th class="name"></th>
          <th>{{ t("doc_dates_hint") }}</th>
          <th></th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in docs.listing.items" :key="row.name">
          <td><input type="checkbox" :checked="selected.includes(row.name)" @change="toggle(row.name)" /></td>
          <td class="name">
            <button v-if="row.type === 'folder'" class="link" @click="go(pathOf(row))">📁 {{ row.name }}</button>
            <button v-else class="link" :title="t('doc_preview')" @click="show(row)">📄 {{ row.name }}</button>
            <span class="size">{{ row.type === "folder" ? `${row.file_count}${t("doc_files_suffix")}` : row.size }}</span>
          </td>
          <td class="dates">{{ row.created }}<br />{{ row.modified }}</td>
          <td><span v-if="row.indexed" class="indexed">{{ t("doc_manager_chunks", { chunks: row.chunks }) }}</span></td>
          <td class="row-actions">
            <a v-if="row.type === 'file'" :href="docs.listing.download_prefix + encodeURIComponent(row.name)" :title="t('doc_download')" download>⬇</a>
            <button v-if="row.type === 'file'" :title="t('doc_copy_path')" @click="copyPath(row)">📋</button>
            <button :title="row.type === 'folder' ? t('doc_folder_rename') : t('doc_rename')" @click="naming = { old: row.name, name: row.name }">✏️</button>
            <button :title="row.type === 'folder' ? t('doc_folder_delete') : t('doc_delete')" @click="askDelete([row.name])">🗑️</button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else-if="docs.listing" class="empty">{{ folder ? t("doc_folder_empty") : t("doc_manager_empty") }}</p>

    <ModalDialog v-if="deleting" @close="deleting = null">
      <strong>{{ t("doc_delete_confirm_title") }}</strong>
      <p class="targets">{{ [...deleting.folders.map((name) => `📁 ${name}`), ...deleting.files.map((name) => `📄 ${name}`)].join(", ") }}</p>
      <label><input v-model="deleting.fromDisk" type="checkbox" /> {{ t("doc_delete_from_disk") }}</label>
      <label><input v-model="deleting.fromIndex" type="checkbox" /> {{ t("doc_delete_from_index") }}</label>
      <div class="dialog-actions">
        <button @click="deleting = null">{{ t("doc_cancel") }}</button>
        <button class="danger" :disabled="!deleting.fromDisk && !deleting.fromIndex" @click="confirmDelete">{{ t("doc_delete") }}</button>
      </div>
    </ModalDialog>

    <ModalDialog v-if="preview" @close="preview = null">
      <header class="preview-head">
        <strong>📄 {{ preview.name }}</strong>
        <button :title="t('dialog_close')" @click="preview = null">×</button>
      </header>
      <pre class="preview">{{ preview.text }}</pre>
    </ModalDialog>
  </main>
</template>

<style scoped>
.documents { overflow-y: auto; padding: 12px 0; display: flex; flex-direction: column; gap: 10px; }
.bar { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
h2 { margin: 0; font-size: 1rem; }
.trail { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; color: var(--aifred-text-muted); }
.actions { display: flex; gap: 8px; margin-left: auto; flex-wrap: wrap; }
button, input:not([type="checkbox"]) { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 4px 8px; font: inherit; cursor: pointer; }
.trail button, .link, .row-actions button { background: none; border: none; padding: 2px 4px; }
.trail button:hover, .link:hover { text-decoration: underline; }
.danger { border-color: var(--aifred-danger); color: var(--aifred-danger); }
button:disabled { opacity: 0.5; cursor: default; }
input[type="checkbox"] { accent-color: var(--aifred-primary); }
.notice { margin: 0; color: var(--aifred-accent-amber); }
.naming { display: flex; gap: 8px; flex-wrap: wrap; }
.list { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
.list th { text-align: left; font-weight: 500; font-size: 0.75rem; color: var(--aifred-text-muted); padding: 4px 6px; }
.list td { padding: 6px; border-top: 1px solid var(--aifred-border); vertical-align: middle; }
.name { width: 55%; overflow-wrap: anywhere; }
.size { margin-left: 8px; font-size: 0.75rem; color: var(--aifred-text-muted); }
.dates { font-size: 0.75rem; color: var(--aifred-text-secondary); white-space: nowrap; }
.indexed { font-size: 0.75rem; color: var(--aifred-accent-cyan); white-space: nowrap; }
.row-actions { white-space: nowrap; text-align: right; }
.row-actions a { text-decoration: none; padding: 2px 4px; color: inherit; }
.empty { color: var(--aifred-text-muted); }
.targets { margin: 0; max-width: 60ch; overflow-wrap: anywhere; color: var(--aifred-text-secondary); }
.dialog-actions { display: flex; gap: 10px; justify-content: flex-end; }
.preview-head { display: flex; justify-content: space-between; align-items: center; gap: 20px; }
.preview-head button { background: none; border: none; font-size: 1.2rem; }
.preview { margin: 0; width: min(820px, 86vw); max-height: 70dvh; overflow: auto; white-space: pre-wrap; font-size: 0.85rem; background: var(--aifred-page-bg); padding: 12px; border-radius: 8px; }
@media (max-width: 760px) {
  .dates { display: none; }
  .name { width: auto; }
}
</style>
