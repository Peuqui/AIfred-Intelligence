<script setup lang="ts">
import { t } from "../i18n";
import { useSessions } from "../stores/sessions";

defineProps<{ current: string | null }>();
const emit = defineEmits<{ open: [sessionId: string]; create: []; remove: [sessionId: string] }>();
const sessions = useSessions();
</script>

<template>
  <nav class="sessions">
    <header>
      <span>{{ t("saved_chats") }}</span>
      <button class="new" @click="emit('create')">＋ {{ t("new_chat") }}</button>
    </header>
    <p v-if="!sessions.list.length" class="empty">{{ t("no_saved_chats") }}</p>
    <ul>
      <li v-for="session in sessions.list" :key="session.session_id" :class="{ active: session.session_id === current }">
        <button class="entry" @click="emit('open', session.session_id)">
          <span class="title">{{ session.title || t("untitled_chat") }}</span>
          <span class="meta">{{ session.last_message_display }} · {{ session.message_count }}</span>
        </button>
        <button class="remove" :title="t('delete_chat')" @click="emit('remove', session.session_id)">🗑</button>
      </li>
    </ul>
  </nav>
</template>

<style scoped>
.sessions { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
header { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-weight: 600; }
.new, .remove, .entry { background: none; border: none; color: inherit; cursor: pointer; font: inherit; }
.new { color: var(--aifred-primary); }
ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; overflow-y: auto; }
li { display: flex; align-items: center; border-radius: 6px; }
li.active, li:hover { background: var(--aifred-input-bg); }
.entry { flex: 1; min-width: 0; text-align: left; padding: 6px 8px; display: flex; flex-direction: column; }
.title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.meta { font-size: 0.75rem; color: var(--aifred-text-muted); }
.remove { opacity: 0.5; padding: 4px 8px; }
.remove:hover { opacity: 1; }
.empty { color: var(--aifred-text-muted); font-size: 0.85rem; }
</style>
