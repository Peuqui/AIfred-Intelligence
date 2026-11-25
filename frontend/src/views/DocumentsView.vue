<script setup lang="ts">
// The documents page: a bar with the source (the local documents folder, and the further ones the enabled plugins offer:
// Nextcloud …), below it the file browser (VueFinder, FileBrowser.vue) on the area of that source and beside it the
// preview of the file clicked (FilePreview.vue), a divider between them to drag. Clicking one file after the other to
// compare them is the point. Where the window has no room for a second column (the rule of the chat's session column,
// lib/sessionsColumn.ts), a click opens the preview in the dialog (the viewer, lib/fileViewer.ts) instead.
// The search index (the whole of what the page was about before it had VueFinder): the sections of a file as a badge on
// its icon, the entries of the context menu (lib/documentMenu.ts), the buttons in the menu bar, and in the status bar
// how far a run of the server's indexing is and how it ended. For the local folder and for every source alike.
import { BookPlus, BrushCleaning, Folder, Library, TriangleAlert } from "lucide-vue-next";
import { computed, defineAsyncComponent, onMounted, ref, watch } from "vue";
import type { DirEntry } from "vuefinder";
import DocumentItemIcon from "../components/DocumentItemIcon.vue";
import FilePreview from "../components/FilePreview.vue";
import IndexCare from "../components/IndexCare.vue";
import { t, tCount } from "../i18n";
import { documentsSplit } from "../lib/devicePrefs";
import type { IndexTarget } from "../lib/documentIndex";
import { indexMenuItems } from "../lib/documentMenu";
import { type ViewPlace, areaPlace, viewFile } from "../lib/fileViewer";
import { useSessionsColumn } from "../lib/sessionsColumn";
import { SPLIT, clampSplit, splitAt } from "../lib/splitter";
import { LOCAL, useDocuments } from "../stores/documents";

// VueFinder and its libraries are about 1 MB: loaded with the page, not with the app.
const FileBrowser = defineAsyncComponent(() => import("../components/FileBrowser.vue"));

const docs = useDocuments();
// A run another tab started shows its progress here too.
onMounted(() => {
  void docs.loadSources();
  void docs.watchIndexing();
});

const local = computed(() => docs.source === LOCAL);
const area = computed(() => (local.value ? "documents" : `source-${docs.source}`));
const sourceLabel = computed(() => docs.sources.find((offered) => offered.key === docs.source)?.label);
const sideBySide = useSessionsColumn().possible;
const previewed = ref<ViewPlace | null>(null);
const browser = ref<{ refresh: () => Promise<void> } | null>(null);
const folderShown = ref("");
watch(area, () => {
  previewed.value = null;
  docs.rejected = [];
  folderShown.value = "";
});

// A file shows beside the list, or in the dialog when there is no room beside it.
function show(entry: DirEntry): void {
  const place = areaPlace(area.value, entry);
  if (sideBySide.value) previewed.value = place;
  else viewFile(place);
}

// The reason a file was refused on upload (the server names the file in it); the same reason shows once.
function refused(reason: string): void {
  if (!docs.rejected.includes(reason)) docs.rejected = [...docs.rejected, reason];
}

// ── the search index ──
const caring = ref(false);
const run = computed(() => docs.indexing);

// The list shows the sections of each file: it is listed again when a run has ended or files left the index.
watch(run, (now) => {
  if (now && !now.running) void browser.value?.refresh();
});

async function deindex(files: IndexTarget[]): Promise<void> {
  await docs.deindex(files);
  await browser.value?.refresh();
}

const menuItems = computed(() =>
  indexMenuItems({
    index: (targets) => void docs.index(targets),
    deindex: (files) => void deindex(files),
    copyPath: local.value ? (path) => void navigator.clipboard.writeText(path) : undefined,
  }),
);

// The folder shown, as an entry to index: everything below it.
const indexThisFolder = (): Promise<void> => docs.index([{ type: "dir", path: folderShown.value || `${area.value}://`, basename: "" }]);

const outcome = computed(() => {
  const progress = run.value;
  if (!progress || progress.running || !progress.total) return "";
  let text = t("doc_index_folder_done", {
    indexed: progress.indexed, files: tCount("doc_file_count", progress.total), chunks: tCount("doc_chunk_count", progress.chunks),
  });
  if (progress.failed.length) text += t("doc_index_folder_failed_suffix", { failed: progress.failed.length });
  return text;
});

// ── the divider ──
const workspace = ref<HTMLElement | null>(null);

function dragDivider(): void {
  const move = (event: PointerEvent): void => {
    if (workspace.value) documentsSplit.value = splitAt(event.clientX, workspace.value.getBoundingClientRect());
  };
  const stop = (): void => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", stop);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", stop);
}

function nudgeDivider(event: KeyboardEvent): void {
  const step = { ArrowLeft: -SPLIT.keyStep, ArrowRight: SPLIT.keyStep }[event.key];
  if (step === undefined) return;
  event.preventDefault();
  documentsSplit.value = clampSplit(documentsSplit.value + step);
}
</script>

<template>
  <main class="documents">
    <header class="bar">
      <h2><Folder :size="16" /> {{ t("doc_manager_title") }}</h2>
      <label v-if="docs.sources.length > 1" class="source">
        {{ t("doc_source_label") }}
        <select :value="docs.source" @change="docs.source = ($event.target as HTMLSelectElement).value">
          <option v-for="option in docs.sources" :key="option.key" :value="option.key">{{ option.label }}</option>
        </select>
      </label>
    </header>
    <p v-if="docs.notice" class="notice">{{ docs.notice }}</p>
    <!-- Files the server refused on upload (type, name), until the user closes the message. -->
    <p v-for="reason in docs.rejected" :key="reason" class="notice">
      <TriangleAlert :size="16" /> {{ reason }}
      <button :title="t('dialog_close')" :aria-label="t('dialog_close')" @click="docs.rejected = docs.rejected.filter((other) => other !== reason)">×</button>
    </p>
    <div ref="workspace" class="workspace" :style="{ '--split': documentsSplit }">
      <div class="list">
        <FileBrowser
          ref="browser"
          :key="area"
          :area="area"
          :label="sourceLabel"
          :menu-items="menuItems"
          @open="show"
          @path-change="(path: string) => (folderShown = path)"
          @uploaded="docs.watchIndexing()"
          @refused="(_name: string, reason: string) => refused(reason)"
        >
          <template #icon="{ item, view }"><DocumentItemIcon :item="item" :view="view" /></template>
          <template #menubar-end>
            <span class="index-tools">
              <button :disabled="run?.running" @click="indexThisFolder"><BookPlus :size="14" /> {{ t("doc_index_this_folder") }}</button>
              <button @click="caring = true"><BrushCleaning :size="14" /> {{ t("db_index_title") }}</button>
            </span>
          </template>
          <template #status-bar>
            <span v-if="run?.running" class="progress">
              {{ t("doc_index_folder_progress", { current: Math.min(run.done + 1, run.total), total: run.total, name: run.current }) }}
            </span>
            <span v-else-if="outcome" class="outcome">
              <span><Library :size="14" /> {{ outcome }}</span>
              <button :title="t('dialog_close')" :aria-label="t('dialog_close')" @click="docs.indexing = null">×</button>
              <span v-for="failure in run?.failed" :key="failure.path" class="failure"><TriangleAlert :size="14" /> {{ failure.path }}: {{ failure.reason }}</span>
            </span>
          </template>
        </FileBrowser>
      </div>
      <template v-if="sideBySide">
        <div
          class="divider-handle"
          role="separator"
          aria-orientation="vertical"
          tabindex="0"
          :title="t('doc_split_handle')"
          :aria-label="t('doc_split_handle')"
          @pointerdown.prevent="dragDivider"
          @keydown="nudgeDivider"
        />
        <div class="side">
          <FilePreview v-if="previewed" :place="previewed" />
          <p v-else class="none">{{ t("doc_preview_none") }}</p>
        </div>
      </template>
    </div>
    <IndexCare v-if="caring" @close="caring = false" @changed="browser?.refresh()" />
  </main>
</template>

<style scoped>
.documents { padding: 12px 0; display: flex; flex-direction: column; gap: 10px; min-height: 0; }
.bar { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
h2 { margin: 0; font-size: 1rem; }
.source { display: flex; align-items: center; gap: 8px; color: var(--aifred-text-secondary); }
.source select { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 4px 8px; font: inherit; }
.notice { margin: 0; color: var(--aifred-accent-amber); --icon-own: currentColor; }
.notice button { background: none; border: none; padding: 0 4px; }
/* What is left of the window below the head of the page (App.vue gives the shell the window's height while this page is
   shown): the browser and the preview scroll inside, the page does not. */
.workspace { display: flex; flex: 1; min-height: 360px; }
.list { flex: 1 1 0; min-width: 0; }
.side { flex: 1 1 0; min-width: 320px; padding-left: 4px; }
.none { margin: 0; color: var(--aifred-text-muted); }
.index-tools { display: flex; gap: 6px; margin-left: auto; }
.index-tools button { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; white-space: nowrap; }
.progress { color: var(--aifred-accent-cyan); }
.outcome { display: flex; flex-wrap: wrap; align-items: center; gap: 2px 8px; color: var(--aifred-accent-cyan); --icon-own: currentColor; }
.outcome button { background: none; border: none; padding: 0 4px; }
.failure { flex-basis: 100%; color: var(--aifred-accent-amber); overflow-wrap: anywhere; }
/* With a preview beside it the list takes its share (min and max keep both columns usable); alone, the whole width. */
.workspace:has(.divider-handle) .list { flex: 0 0 calc(var(--split) * 100%); min-width: 260px; max-width: calc(100% - 330px); }
/* A grip wider than its line: easy to hit with a finger, which must not scroll the page while dragging. */
.divider-handle { flex: none; width: 14px; margin: 0 2px; cursor: col-resize; touch-action: none; position: relative; }
.divider-handle::after { content: ""; position: absolute; top: 0; bottom: 0; left: 6px; width: 2px; border-radius: 1px; background: var(--aifred-border); }
.divider-handle:hover::after, .divider-handle:focus-visible::after { background: var(--aifred-primary); }
.divider-handle:focus-visible { outline: none; }
</style>
