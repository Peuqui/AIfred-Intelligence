<script setup lang="ts">
// The content of a preview, for one place (lib/fileViewer.ts): the dialog (FileViewer) and the documents page show
// the same. A text, Markdown, CSV and Excel file show as text (CSV and Excel as the server's Markdown tables: the
// index's parser, document_store._markdown_table; Markdown formatted by the renderer of the chat's answers), an
// image as a picture, a PDF and an HTML page embedded in a frame (the server sends a page sandboxed), audio and video
// in the browser's player. A button always opens the file in a tab of its own (the browser's own PDF viewer and
// player; on a phone often the better view). A click on an image opens it large (ImageLightbox, as in the chat). An HTML
// page carries the hint how to save it as a file of its own.
import { Download, ExternalLink, FileText, TriangleAlert } from "lucide-vue-next";
import { computed, ref, watch } from "vue";
import { ApiError } from "../api/client";
import { t } from "../i18n";
import { type ViewPlace, fileKind } from "../lib/fileViewer";
import { renderMarkdown } from "../lib/markdown";
import ImageLightbox from "./ImageLightbox.vue";

const props = defineProps<{ place: ViewPlace }>();

const kind = computed(() => fileKind(props.place.name));
const enlarged = ref(false);
watch(() => props.place, () => (enlarged.value = false));
const loading = ref(false);
const error = ref("");
const text = ref("");

// A file that shows as text asks the server for it; a newer file replaces a slower older answer.
let asked = 0;
watch(
  () => props.place,
  async (place) => {
    const mine = ++asked;
    text.value = "";
    error.value = "";
    if (kind.value !== "text") return;
    loading.value = true;
    try {
      if (!place.text) throw new Error(t("preview_no_text"));
      const preview = await place.text();
      if (mine === asked) text.value = preview.text;
    } catch (failure) {
      if (mine === asked) error.value = failure instanceof ApiError ? failure.detail : String(failure instanceof Error ? failure.message : failure);
    } finally {
      if (mine === asked) loading.value = false;
    }
  },
  { immediate: true },
);

const MARKDOWN_FILE = /\.(md|markdown|csv|xlsx)$/i;
const markdown = computed(() => (MARKDOWN_FILE.test(props.place.name) ? renderMarkdown(text.value) : null));
</script>

<template>
  <section class="preview">
    <header class="preview-head">
      <strong class="name"><FileText :size="16" /> {{ place.name }}</strong>
      <a class="tab" :href="place.viewUrl" target="_blank" rel="noopener"><ExternalLink :size="16" /> {{ t("doc_open_page") }}</a>
      <slot name="actions" />
    </header>
    <p v-if="kind === 'page'" class="hint"><Download :size="16" /> {{ t("doc_page_hint") }}</p>
    <p v-if="error" class="notice"><TriangleAlert :size="16" /> {{ error }}</p>
    <template v-else-if="kind === 'text'">
      <p v-if="loading" class="loading">{{ t("preview_loading") }}</p>
      <!-- eslint-disable-next-line vue/no-v-html -- renderMarkdown sanitizes (DOMPurify), as for every chat answer -->
      <div v-else-if="markdown !== null" class="box markdown-box markdown" v-html="markdown" />
      <pre v-else class="box text-box">{{ text }}</pre>
    </template>
    <img v-else-if="kind === 'image'" class="box picture" :src="place.viewUrl" :alt="place.name" @click="enlarged = true" />
    <iframe v-else-if="kind === 'pdf' || kind === 'page'" class="frame" :src="place.viewUrl" :title="place.name" />
    <div v-else-if="kind === 'audio'" class="box player"><audio controls :src="place.viewUrl" /></div>
    <div v-else class="box player"><video controls :src="place.viewUrl" /></div>
    <ImageLightbox v-if="enlarged" :count="1" :start="0" :pictures="() => [place.viewUrl]" @close="enlarged = false" />
  </section>
</template>

<style scoped>
.preview { display: flex; flex-direction: column; gap: 8px; min-height: 0; min-width: 0; height: 100%; }
.preview-head { display: flex; align-items: center; gap: 8px 16px; flex-wrap: wrap; }
.name { margin-right: auto; overflow-wrap: anywhere; min-width: 0; }
.tab { color: var(--aifred-primary); text-decoration: none; white-space: nowrap; --icon-own: currentColor; }
.tab:hover { text-decoration: underline; }
.box { margin: 0; flex: 1; min-height: 0; overflow: auto; background: var(--aifred-page-bg); padding: 12px; border-radius: 8px; }
.text-box { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 0.85rem; }
.markdown-box { font-size: 0.95rem; line-height: 1.5; }
.picture { object-fit: contain; width: 100%; box-sizing: border-box; cursor: zoom-in; }
.hint { display: flex; gap: 8px; align-items: flex-start; margin: 0; padding: 8px 10px; font-size: 0.8rem; color: var(--aifred-text-secondary); background: var(--aifred-input-bg); border: 1px solid var(--aifred-border); border-radius: 8px; --icon-own: currentColor; }
.player { display: flex; align-items: center; justify-content: center; }
.player audio { width: 100%; }
.player video { max-width: 100%; max-height: 100%; }
.frame { flex: 1; min-height: 0; width: 100%; border: 1px solid var(--aifred-border); border-radius: 8px; }
.loading { margin: 0; color: var(--aifred-text-muted); }
.notice { margin: 0; color: var(--aifred-accent-amber); --icon-own: currentColor; overflow-wrap: anywhere; }
/* Tables (CSV, Excel, Markdown): the header stays on top while the rows scroll, every second row a shade darker. */
.markdown-box :deep(th) { position: sticky; top: -12px; background: var(--aifred-card-bg); white-space: nowrap; }
.markdown-box :deep(tbody tr:nth-child(even)) { background: var(--aifred-card-bg); }
.markdown-box :deep(td), .markdown-box :deep(th) { padding: 3px 8px; vertical-align: top; }
/* Every column at least as wide as a date (it broke at its hyphens: "2026-" / "11-10"); longer text wraps at its
   spaces, a long address anywhere in a column of its own width. A table wider than the box scrolls sideways. */
.markdown-box :deep(td) { min-width: 6.5em; }
.markdown-box :deep(td a) { word-break: break-all; display: inline-block; min-width: 18rem; }
</style>
