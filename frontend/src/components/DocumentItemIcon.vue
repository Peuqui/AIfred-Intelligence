<script setup lang="ts">
// The icon of an entry in VueFinder's list (its ``icon`` slot) with the place of the file in the search index: the
// number of its sections as a badge, visible in the grid and in the list. The folder and the file are VueFinder's own
// drawings (same classes, so its theme and AIfred's colours in FileBrowser.vue apply); only a file with sections in the
// index gets the badge.
import { computed } from "vue";
import { tCount } from "../i18n";
import { type IndexedEntry, chunksOf } from "../lib/documentIndex";

const props = defineProps<{ item: IndexedEntry; view?: "grid" | "list" }>();
const chunks = computed(() => chunksOf(props.item));
</script>

<template>
  <span class="doc-icon" :class="`doc-icon--${view}`">
    <svg v-if="item.type === 'dir'" class="vuefinder__item-icon__folder" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" d="M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-6l-2-2H5a2 2 0 0 0-2 2" />
    </svg>
    <svg v-else class="vuefinder__item-icon__file" xmlns="http://www.w3.org/2000/svg" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" d="M7 21h10a2 2 0 0 0 2-2V9.414a1 1 0 0 0-.293-.707l-5.414-5.414A1 1 0 0 0 12.586 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2" />
    </svg>
    <span v-if="view === 'grid' && item.type !== 'dir' && item.extension" class="vuefinder__item-icon__extension">{{ item.extension.substring(0, 3) }}</span>
    <span v-if="chunks > 0" class="index-badge">{{ tCount("doc_chunk_count", chunks) }}</span>
  </span>
</template>

<style scoped>
.doc-icon { position: relative; display: flex; align-items: center; width: 100%; height: 100%; }
.doc-icon svg { flex: none; }
/* The badge sits on the upper edge of the grid's icon (the file type is in its middle, the name under it), and beside the
   list's icon. */
.index-badge { font-size: 0.65rem; line-height: 1; padding: 2px 6px; border-radius: 999px; white-space: nowrap; color: var(--aifred-accent-cyan); background: var(--aifred-page-bg); border: 1px solid var(--aifred-accent-cyan); }
.doc-icon--grid { justify-content: center; }
.doc-icon--grid .index-badge { position: absolute; left: 50%; top: -9px; transform: translateX(-50%); }
.doc-icon--list { gap: 6px; }
</style>

<style>
/* VueFinder fixes the icon box of the list (and the cell around it) to the icon's size and stretches its content to it;
   where the badge sits beside the icon the box is as wide as both, the drawing keeps its size. */
.vuefinder__explorer__item-list-icon:has(.index-badge), .vuefinder__item-icon--list:has(.index-badge) { width: max-content; min-width: 0; max-width: none; }
.vuefinder__item-icon--list .doc-icon { width: max-content !important; max-width: none !important; flex: none !important; }
.vuefinder__item-icon--list .doc-icon svg { width: var(--vf-icon-size, 16px) !important; height: var(--vf-icon-size, 16px) !important; }
</style>
