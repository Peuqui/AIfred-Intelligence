<script setup lang="ts">
// The user's sessions, newest first: open, start a new one, delete one, or select several (or all) and delete them.
import { ref } from "vue";
import { t } from "../i18n";
import { useSessions } from "../stores/sessions";

defineProps<{ current: string | null }>();
const emit = defineEmits<{ open: [sessionId: string]; create: []; remove: [sessionIds: string[]] }>();
const sessions = useSessions();
const selecting = ref(false);
const chosen = ref<string[]>([]);

function toggleSelecting(): void {
  selecting.value = !selecting.value;
  chosen.value = [];
}

function removeChosen(): void {
  emit("remove", chosen.value);
  chosen.value = [];
}

function removeAll(): void {
  if (window.confirm(t("delete_all_confirm"))) emit("remove", sessions.list.map((session) => session.session_id));
  chosen.value = [];
}
</script>

<template>
  <nav class="sessions">
    <header>
      <span>{{ t("saved_chats") }}</span>
      <button class="select" :title="selecting ? t('sessions_select_done') : t('sessions_select')" @click="toggleSelecting">☑</button>
      <button class="new" @click="emit('create')">＋ {{ t("new_chat") }}</button>
    </header>
    <div v-if="selecting && sessions.list.length" class="bulk">
      <button @click="chosen = sessions.list.map((session) => session.session_id)">{{ t("select_all") }}</button>
      <button :disabled="!chosen.length" @click="removeChosen">{{ t("delete_selected") }} ({{ chosen.length }})</button>
      <button class="danger" @click="removeAll">{{ t("delete_all") }}</button>
    </div>
    <p v-if="!sessions.list.length" class="empty">{{ t("no_saved_chats") }}</p>
    <ul>
      <li v-for="session in sessions.list" :key="session.session_id" :class="{ active: session.session_id === current }">
        <input v-if="selecting" v-model="chosen" type="checkbox" :value="session.session_id" class="pick" />
        <button class="entry" @click="emit('open', session.session_id)">
          <span class="title">{{ session.title || t("untitled_chat") }}</span>
          <span class="meta">{{ session.last_message_display }} · {{ session.message_count }}</span>
        </button>
        <button v-if="!selecting" class="remove" :title="t('delete_chat')" @click="emit('remove', [session.session_id])">🗑</button>
      </li>
    </ul>
  </nav>
</template>

<style scoped>
.sessions { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
header { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-weight: 600; }
.new, .remove, .entry { background: none; border: none; color: inherit; cursor: pointer; font: inherit; }
.new { color: var(--aifred-primary); }
.select { background: none; border: none; color: var(--aifred-text-secondary); cursor: pointer; font: inherit; margin-left: auto; }
.bulk { display: flex; flex-wrap: wrap; gap: 6px; }
.bulk button { background: none; border: 1px solid var(--aifred-border); border-radius: 6px; color: inherit; cursor: pointer; font: inherit; font-size: 0.8rem; padding: 2px 8px; }
.bulk button:disabled { opacity: 0.5; cursor: default; }
.bulk .danger { border-color: var(--aifred-danger); }
.pick { accent-color: var(--aifred-primary); margin-left: 6px; }
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
