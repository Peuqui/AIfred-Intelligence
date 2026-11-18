<script setup lang="ts">
// The dialog of the viewer (lib/fileViewer.ts), mounted once in the shell: a file's text, from whichever page. Markdown
// shows formatted, by the renderer of the chat's answers; an image shows in the one picture view of the app
// (ImageLightbox, also the chat's and the cameras').
import { FileText } from "lucide-vue-next";
import { computed } from "vue";
import { t } from "../i18n";
import { viewed } from "../lib/fileViewer";
import { renderMarkdown } from "../lib/markdown";
import ImageLightbox from "./ImageLightbox.vue";
import ModalDialog from "./ModalDialog.vue";

const MARKDOWN_FILE = /\.(md|markdown)$/i;
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
.viewer-box { margin: 0; width: min(820px, calc(86 * var(--vw))); max-height: calc(70 * var(--dvh)); overflow: auto; background: var(--aifred-page-bg); padding: 12px; border-radius: 8px; }
.viewer-text { white-space: pre-wrap; font-size: 0.85rem; }
.viewer-markdown { font-size: 0.95rem; line-height: 1.5; }
</style>
