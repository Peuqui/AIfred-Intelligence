<script setup lang="ts">
// The trash of the chats (maintenance): what was deleted from the chat list lies here until the nightly cleanup removes
// it (lib/session_trash.py). Bring a chat back, delete it for good, or empty the trash.
import { RotateCcw, Trash2, TriangleAlert } from "lucide-vue-next";
import { ref } from "vue";
import { ApiError, api } from "../api/client";
import type { TrashedSessionEntry } from "../api/types";
import { t } from "../i18n";

const entries = ref<TrashedSessionEntry[] | null>(null);
const notice = ref("");
const confirming = ref(false);

async function work(action: () => Promise<unknown>): Promise<void> {
  notice.value = "";
  try {
    await action();
    entries.value = await api<TrashedSessionEntry[]>("session-trash");
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
  }
}
const load = (): Promise<void> => work(async () => {});
const restore = (id: string): Promise<void> => work(() => api(`session-trash/${id}/restore`, { method: "POST" }));
const purge = (id: string): Promise<void> => work(() => api(`session-trash/${id}`, { method: "DELETE" }));
async function empty(): Promise<void> {
  confirming.value = false;
  await work(() => api("session-trash", { method: "DELETE" }));
}
async function opened(event: Event): Promise<void> {
  if ((event.target as HTMLDetailsElement).open) await load();
}
</script>

<template>
  <details @toggle="opened">
    <summary><Trash2 :size="16" /> {{ t("maintenance_session_trash") }}<template v-if="entries?.length"> ({{ entries.length }})</template></summary>
    <p class="hint">{{ t("maintenance_session_trash_intro") }}</p>
    <p v-if="notice" class="notice"><TriangleAlert :size="16" /> {{ notice }}</p>
    <p v-if="entries && !entries.length" class="hint">{{ t("maintenance_session_trash_empty") }}</p>
    <div v-if="entries?.length" class="bar">
      <button v-if="!confirming" class="danger" @click="confirming = true"><Trash2 :size="16" /> {{ t("maintenance_session_trash_empty_all") }}</button>
      <template v-else>
        <span>{{ t("maintenance_session_trash_empty_confirm") }}</span>
        <button class="danger" @click="empty">{{ t("confirm_yes") }}</button>
        <button @click="confirming = false">{{ t("db_cancel") }}</button>
      </template>
    </div>
    <div v-for="entry in entries" :key="entry.session_id" class="entry">
      <span class="title">{{ entry.title || t("maintenance_session_trash_untitled") }}</span>
      <button :title="t('maintenance_session_trash_restore')" @click="restore(entry.session_id)"><RotateCcw :size="16" /> {{ t("maintenance_session_trash_restore") }}</button>
      <button class="danger" :title="t('maintenance_session_trash_purge')" @click="purge(entry.session_id)"><Trash2 :size="16" /> {{ t("maintenance_session_trash_purge") }}</button>
      <span class="meta">{{ t("maintenance_session_trash_meta", { count: entry.message_count, deleted: entry.deleted_display, expires: entry.expires_display }) }}</span>
    </div>
  </details>
</template>

<style scoped>
.bar, .entry { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 0.85rem; }
.entry { padding: 6px 0; border-top: 1px solid var(--aifred-border); }
.title { flex: 1; min-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.meta { flex-basis: 100%; font-size: 0.78rem; color: var(--aifred-text-muted); }
.hint { margin: 6px 0; font-size: 0.8rem; color: var(--aifred-text-muted); }
.notice { margin: 0; color: var(--aifred-accent-amber); --icon-own: currentColor; }
button { padding: 2px 8px; font-size: 0.8rem; }
</style>
