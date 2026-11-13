<script setup lang="ts">
// The user's sessions, the last used first: open, start a new one, delete one, or select several (or all) and delete
// them. On a wide window a column at the left of the chat, always open (lib/sessionsColumn.ts), else an accordion
// above it; the switch between both shows only where the column is possible.
import { ChevronDown, ChevronRight, ListChecks, PanelLeft, PanelTop, RefreshCw, Trash2 } from "lucide-vue-next";
import { computed, ref } from "vue";
import { t } from "../i18n";
import { useSessionsColumn } from "../lib/sessionsColumn";
import { useSessions } from "../stores/sessions";

defineProps<{ current: string | null }>();
const emit = defineEmits<{ open: [sessionId: string]; create: []; remove: [sessionIds: string[]] }>();
const sessions = useSessions();
const column = useSessionsColumn();
const opened = ref(false); // the accordion closed at first, as the Reflex accordion was
const open = computed(() => column.shown.value || opened.value);
const selecting = ref(false);
const chosen = ref<string[]>([]);

// A chosen chat closes the accordion (in the column there is nothing to close): the chat shows.
function openSession(sessionId: string): void {
  emit("open", sessionId);
  opened.value = false;
}

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
  <nav class="sessions" :class="{ column: column.shown.value }">
    <header>
      <span v-if="column.shown.value" class="toggle">{{ t("saved_chats") }} <span class="count">{{ sessions.list.length }}</span></span>
      <button v-else class="toggle" @click="opened = !opened">
        <ChevronDown v-if="opened" :size="16" /><ChevronRight v-else :size="16" /> {{ t("saved_chats") }} <span class="count">{{ sessions.list.length }}</span>
      </button>
      <button
        v-if="column.possible.value"
        class="placement"
        :title="column.shown.value ? t('sessions_place_above') : t('sessions_place_column')"
        :aria-label="column.shown.value ? t('sessions_place_above') : t('sessions_place_column')"
        @click="column.toggle"
      >
        <PanelTop v-if="column.shown.value" :size="16" /><PanelLeft v-else :size="16" />
      </button>
      <button class="reload" :title="t('sessions_reload')" :aria-label="t('sessions_reload')" @click="sessions.refresh()">
        <RefreshCw :size="16" />
      </button>
      <button class="new" @click="emit('create')">＋ {{ t("new_chat") }}</button>
    </header>
    <!-- The choosing of chats in a row of its own, as soon as the list is open; its buttons (and the boxes at the chats)
         only while choosing: without boxes they would have nothing to act on (Peuqui 09.11.2026). -->
    <div v-if="open && sessions.list.length" class="bulk">
      <button class="select" :class="{ on: selecting }" :title="selecting ? t('sessions_select_done') : t('sessions_select')" @click="toggleSelecting"><ListChecks :size="16" /></button>
      <template v-if="selecting">
        <button @click="chosen = sessions.list.map((session) => session.session_id)">{{ t("select_all") }}</button>
        <button :disabled="!chosen.length" @click="removeChosen">{{ t("delete_selected") }} ({{ chosen.length }})</button>
        <button class="danger" @click="removeAll">{{ t("delete_all") }}</button>
      </template>
    </div>
    <p v-if="open && !sessions.list.length" class="empty">{{ t("no_saved_chats") }}</p>
    <ul v-if="open">
      <li v-for="session in sessions.list" :key="session.session_id" :class="{ active: session.session_id === current }">
        <input v-if="selecting" v-model="chosen" type="checkbox" :value="session.session_id" class="pick" />
        <button class="entry" @click="openSession(session.session_id)">
          <span class="title">{{ session.title || t("untitled_chat") }}</span>
          <span class="meta">{{ session.last_message_display }} · {{ session.message_count }}</span>
        </button>
        <button v-if="!selecting" class="remove" :title="t('delete_chat')" @click="emit('remove', [session.session_id])"><Trash2 :size="16" /></button>
      </li>
    </ul>
  </nav>
</template>

<style scoped>
.sessions { display: flex; flex-direction: column; gap: 8px; min-width: 0; padding: 8px 12px; border: 1px solid var(--aifred-border); border-radius: 8px; background: var(--aifred-card-bg); }
/* The whole bar up to „New chat“ opens and closes the list, not only its words. */
.toggle { flex: 1; align-self: stretch; background: none; border: none; color: inherit; cursor: pointer; font: inherit; font-weight: 600; padding: 4px 0; text-align: left; }
/* In the column the list fills its height and scrolls by itself. */
.sessions.column { box-sizing: border-box; overflow: hidden; }
.sessions.column ul { flex: 1; min-height: 0; }
/* The column is narrow: the title on a line of its own, the switch and „New chat“ below it on the right. */
.sessions.column header { flex-wrap: wrap; }
.sessions.column .toggle { flex-basis: 100%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sessions.column .placement { margin-left: auto; }
.reload:hover { color: var(--aifred-primary); }
.placement, .reload { display: inline-flex; align-items: center; background: none; border: none; padding: 2px; color: var(--aifred-text-secondary); cursor: pointer; }
.count { margin-left: 6px; font-weight: 400; color: var(--aifred-text-muted); }
header { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-weight: 600; }
.new, .remove, .entry { background: none; border: none; color: inherit; cursor: pointer; font: inherit; }
.new { color: var(--aifred-primary); }
.bulk { display: flex; flex-wrap: wrap; gap: 6px; }
.bulk button { background: none; border: 1px solid var(--aifred-border); border-radius: 6px; color: inherit; cursor: pointer; font: inherit; font-size: var(--text-small); padding: 2px 8px; }
.bulk button:disabled { opacity: 0.5; cursor: default; }
.bulk .danger { border-color: var(--aifred-danger); }
.bulk .select { color: var(--aifred-text-secondary); }
.bulk .select.on { border-color: var(--aifred-primary); color: var(--aifred-primary); }
.pick { accent-color: var(--aifred-primary); margin-left: 6px; }
ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; overflow-y: auto; }
li { display: flex; align-items: center; border-radius: 6px; }
li.active, li:hover { background: var(--aifred-input-bg); }
.entry { flex: 1; min-width: 0; text-align: left; padding: 6px 8px; display: flex; flex-direction: column; }
.title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.meta { font-size: var(--text-small); color: var(--aifred-text-muted); }
.remove { opacity: 0.5; padding: 4px 8px; }
.remove:hover { opacity: 1; }
.empty { color: var(--aifred-text-muted); font-size: var(--text-small); }
</style>
