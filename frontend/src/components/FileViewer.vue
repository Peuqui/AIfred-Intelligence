<script setup lang="ts">
// The dialog of the viewer (lib/fileViewer.ts), mounted once in the shell: a file's text, from whichever page. Markdown
// shows formatted, by the renderer of the chat's answers, and so do CSV and Excel files: the server's preview gives them
// as Markdown tables (the index's parser, document_store._markdown_table). An image shows in the one picture view of the
// app (ImageLightbox, also the chat's and the cameras'). The box is as wide as the page (--file-dialog-width, App.vue) and as high as
// its text, up to the dialog's height (ModalDialog); a wide table scrolls sideways under a header that stays.
import { FileText } from "lucide-vue-next";
import { computed } from "vue";
import { t } from "../i18n";
import { viewed } from "../lib/fileViewer";
import { renderMarkdown } from "../lib/markdown";
import ImageLightbox from "./ImageLightbox.vue";
import ModalDialog from "./ModalDialog.vue";

const MARKDOWN_FILE = /\.(md|markdown|csv|xlsx)$/i;
const markdown = computed(() =>
  viewed.value?.text !== undefined && MARKDOWN_FILE.test(viewed.value.name) ? renderMarkdown(viewed.value.text) : null,
);
</script>

<template>
  <ImageLightbox
    v-if="viewed?.imageUrl"
    :count="1"
    :start="0"
    :pictures="() => [viewed?.imageUrl ?? '']"
    @close="viewed = null"
  />
  <ModalDialog v-else-if="viewed" @close="viewed = null">
    <header class="viewer-head">
      <strong><FileText :size="16" /> {{ viewed.name }}</strong>
      <button :title="t('dialog_close')" @click="viewed = null">×</button>
    </header>
    <!-- eslint-disable-next-line vue/no-v-html -- renderMarkdown sanitizes (DOMPurify), as for every chat answer -->
    <div v-if="markdown !== null" class="viewer-box viewer-markdown markdown" v-html="markdown" />
    <pre v-else class="viewer-box viewer-text">{{ viewed.text }}</pre>
  </ModalDialog>
</template>

<style scoped>
.viewer-head { display: flex; justify-content: space-between; align-items: center; gap: 20px; }
.viewer-head button { background: none; border: none; font-size: 1.2rem; }
.viewer-box { margin: 0; width: var(--file-dialog-width); overflow: auto; background: var(--aifred-page-bg); padding: 12px; border-radius: 8px; }
.viewer-text { white-space: pre-wrap; font-size: 0.85rem; }
.viewer-markdown { font-size: 0.95rem; line-height: 1.5; }
/* Tables (CSV, Excel, Markdown): the header stays on top while the rows scroll, every second row a shade darker. */
.viewer-markdown :deep(th) { position: sticky; top: -12px; background: var(--aifred-card-bg); white-space: nowrap; }
.viewer-markdown :deep(tbody tr:nth-child(even)) { background: var(--aifred-card-bg); }
.viewer-markdown :deep(td), .viewer-markdown :deep(th) { padding: 3px 8px; vertical-align: top; }
/* Every column at least as wide as a date (it broke at its hyphens: "2026-" / "11-10"); longer text wraps at its
   spaces, a long address anywhere in a column of its own width. A table wider than the dialog scrolls sideways. */
.viewer-markdown :deep(td) { min-width: 6.5em; }
.viewer-markdown :deep(td a) { word-break: break-all; display: inline-block; min-width: 18rem; }
</style>
