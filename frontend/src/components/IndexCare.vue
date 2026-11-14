<script setup lang="ts">
// Care of the documents' search index: how many documents and sections it holds, the entries whose file is gone
// from the local folder (taken out one by one or all), and the whole index cleared after asking (the files stay).
import { BookMinus, BrushCleaning, FileText, Trash2, TriangleAlert } from "lucide-vue-next";
import { onMounted, ref } from "vue";
import { ApiError, api } from "../api/client";
import type { IndexOverview } from "../api/types";
import { t, tCount } from "../i18n";
import ModalDialog from "./ModalDialog.vue";

const emit = defineEmits<{ close: []; changed: [] }>();
const overview = ref<IndexOverview | null>(null);
const notice = ref("");
const clearing = ref(false);

async function call(work: () => Promise<IndexOverview>): Promise<void> {
  notice.value = "";
  try {
    overview.value = await work();
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
  }
}
const post = (path: string, body: object): Promise<IndexOverview> => api<IndexOverview>(path, { method: "POST", body: JSON.stringify(body) });

onMounted(() => call(() => api<IndexOverview>("documents/index/overview")));

async function remove(filenames: string[]): Promise<void> {
  await call(() => post("documents/index/orphans/remove", { filenames }));
  emit("changed");
}

async function clearAll(): Promise<void> {
  clearing.value = false;
  await call(async () => {
    await post("documents/index/clear", {});
    return api<IndexOverview>("documents/index/overview");
  });
  emit("changed");
}
</script>

<template>
  <ModalDialog @close="emit('close')">
    <div class="care">
      <header>
        <strong><BrushCleaning :size="16" /> {{ t("db_index_title") }}</strong>
        <button class="close" :title="t('dialog_close')" @click="emit('close')">×</button>
      </header>
      <p v-if="notice" class="notice"><TriangleAlert :size="16" /> {{ notice }}</p>
      <template v-if="overview">
        <p class="counts">{{ tCount("db_document_count", overview.documents) }} · {{ tCount("doc_chunk_count", overview.chunks) }}</p>
        <section>
          <div class="head">
            <strong>{{ t("db_orphans_title") }}</strong>
            <button v-if="overview.orphans.length" @click="remove(overview.orphans.map((orphan) => orphan.filename))">{{ t("db_orphans_delete_all") }}</button>
          </div>
          <p v-if="!overview.orphans.length" class="empty">{{ t("db_orphans_none") }}</p>
          <div v-for="orphan in overview.orphans" :key="orphan.filename" class="orphan">
            <span class="name"><FileText :size="16" /> {{ orphan.filename }}</span>
            <span class="meta">{{ tCount("doc_chunk_count", orphan.chunks) }} · {{ orphan.indexed }}</span>
            <button :title="t('db_remove_from_index')" @click="remove([orphan.filename])"><BookMinus :size="16" /></button>
          </div>
        </section>
        <div class="actions">
          <button v-if="!clearing" class="danger" :disabled="!overview.chunks" @click="clearing = true"><Trash2 :size="16" /> {{ t("db_clear_index") }}</button>
          <template v-else>
            <span class="confirm">{{ t("db_clear_confirm") }}</span>
            <button @click="clearing = false">{{ t("db_cancel") }}</button>
            <button class="danger" @click="clearAll">{{ t("db_clear_index") }}</button>
          </template>
        </div>
      </template>
    </div>
  </ModalDialog>
</template>

<style scoped>
.care { width: min(640px, calc(86 * var(--vw))); display: flex; flex-direction: column; gap: 10px; }
header, .head { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.close { background: none; border: none; font-size: 1.2rem; }
button { padding: 4px 8px; }
.notice { margin: 0; color: var(--aifred-accent-amber); --icon-own: currentColor; }
.counts { margin: 0; color: var(--aifred-text-secondary); }
section { display: flex; flex-direction: column; gap: 6px; max-height: calc(45 * var(--dvh)); overflow-y: auto; }
.empty { margin: 0; color: var(--aifred-text-muted); }
.orphan { display: flex; align-items: center; gap: 10px; border-top: 1px solid var(--aifred-border); padding-top: 6px; }
.orphan .name { flex: 1; overflow-wrap: anywhere; }
.orphan .meta { font-size: 0.75rem; color: var(--aifred-text-muted); white-space: nowrap; }
.orphan button { background: none; border: none; }
.actions { display: flex; gap: 8px; justify-content: flex-end; align-items: center; flex-wrap: wrap; }
.confirm { flex-basis: 100%; font-size: 0.85rem; color: var(--aifred-accent-amber); text-align: right; }
</style>
