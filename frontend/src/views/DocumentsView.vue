<script setup lang="ts">
// The documents page: a bar with the source (the local documents folder, and the further ones the enabled plugins offer:
// Nextcloud …), below it the file browser (VueFinder, FileBrowser.vue) on the area of that source and beside it the
// preview of the file clicked (FilePreview.vue), a divider between them to drag. Clicking one file after the other to
// compare them is the point. Where the window has no room for a second column (the rule of the chat's session column,
// lib/sessionsColumn.ts), a click opens the preview in the dialog (the viewer, lib/fileViewer.ts) instead.
import { Folder } from "lucide-vue-next";
import { computed, defineAsyncComponent, onMounted, ref, watch } from "vue";
import type { DirEntry } from "vuefinder";
import FilePreview from "../components/FilePreview.vue";
import { t } from "../i18n";
import { documentsSplit } from "../lib/devicePrefs";
import { type ViewPlace, areaPlace, viewFile } from "../lib/fileViewer";
import { useSessionsColumn } from "../lib/sessionsColumn";
import { SPLIT, clampSplit, splitAt } from "../lib/splitter";
import { LOCAL, useDocuments } from "../stores/documents";

// VueFinder and its libraries are about 1 MB: loaded with the page, not with the app.
const FileBrowser = defineAsyncComponent(() => import("../components/FileBrowser.vue"));

const docs = useDocuments();
onMounted(() => docs.loadSources());

const area = computed(() => (docs.source === LOCAL ? "documents" : `source-${docs.source}`));
const sideBySide = useSessionsColumn().possible;
const previewed = ref<ViewPlace | null>(null);
const browser = ref<{ clearSelection: () => void } | null>(null);
watch(area, () => (previewed.value = null));

// A file shows beside the list, or in the dialog when there is no room beside it. In the dialog the selection is
// cleared again: the same file tapped once more must show again.
function show(entry: DirEntry): void {
  const place = areaPlace(area.value, entry);
  if (sideBySide.value) {
    previewed.value = place;
    return;
  }
  viewFile(place);
  browser.value?.clearSelection();
}

// One file selected is a click on it: the preview follows (a folder or several files leave it as it is).
function selected(items: DirEntry[]): void {
  const [only] = items;
  if (items.length === 1 && only?.type === "file") show(only);
}

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
    <div ref="workspace" class="workspace" :style="{ '--split': documentsSplit }">
      <div class="list">
        <FileBrowser ref="browser" :key="area" :area="area" @select="selected" @open="show" />
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
  </main>
</template>

<style scoped>
.documents { padding: 12px 0; display: flex; flex-direction: column; gap: 10px; }
.bar { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
h2 { margin: 0; font-size: 1rem; }
.source { display: flex; align-items: center; gap: 8px; color: var(--aifred-text-secondary); }
.source select { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 4px 8px; font: inherit; }
.notice { margin: 0; color: var(--aifred-accent-amber); }
/* The height of the window less the head of the page (bar, status line, this page's own bar): the browser and the preview
   scroll inside, the page does not. */
.workspace { display: flex; height: calc(100 * var(--vh) - 230px); min-height: 360px; }
.list { flex: 1 1 0; min-width: 0; }
.side { flex: 1 1 0; min-width: 320px; padding-left: 4px; }
.none { margin: 0; color: var(--aifred-text-muted); }
/* With a preview beside it the list takes its share (min and max keep both columns usable); alone, the whole width. */
.workspace:has(.divider-handle) .list { flex: 0 0 calc(var(--split) * 100%); min-width: 260px; max-width: calc(100% - 330px); }
/* A grip wider than its line: easy to hit with a finger, which must not scroll the page while dragging. */
.divider-handle { flex: none; width: 14px; margin: 0 2px; cursor: col-resize; touch-action: none; position: relative; }
.divider-handle::after { content: ""; position: absolute; top: 0; bottom: 0; left: 6px; width: 2px; border-radius: 1px; background: var(--aifred-border); }
.divider-handle:hover::after, .divider-handle:focus-visible::after { background: var(--aifred-primary); }
.divider-handle:focus-visible { outline: none; }
</style>
