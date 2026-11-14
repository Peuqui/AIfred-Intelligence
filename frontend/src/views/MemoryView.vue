<script setup lang="ts">
// The memory page: the agents' long-term memories (lib/agent_memory.py). Left every agent with its count (also a
// collection of an agent deleted since), right the entries of the one open, newest first, filtered by type:
// read, edit (summary and content; the summary is one line, the index's limit), delete one or all. An agent's
// card opens the page with that agent (#/memory?agent=<id>).
import { NotebookPen, Pencil, Trash2, TriangleAlert } from "lucide-vue-next";
import { computed, ref, watch } from "vue";
import { useRoute } from "vue-router";
import type { MemoryEntry } from "../api/types";
import ModalDialog from "../components/ModalDialog.vue";
import { t } from "../i18n";
import { useMemory } from "../stores/memory";

const memory = useMemory();
const route = useRoute();

const filter = ref("");
const types = computed(() => [...new Set(memory.entries.map((entry) => entry.type))].sort());
const shown = computed(() => memory.entries.filter((entry) => !filter.value || entry.type === filter.value));

async function open(id: string): Promise<void> {
  filter.value = "";
  editing.value = null;
  await memory.open(id);
}

const editing = ref<{ id: string; summary: string; content: string } | null>(null);
function startEdit(entry: MemoryEntry): void {
  editing.value = { id: entry.id, summary: entry.summary, content: entry.content };
}
async function saveEdit(): Promise<void> {
  if (!editing.value) return;
  if (await memory.edit(editing.value.id, editing.value.summary, editing.value.content)) editing.value = null;
}

const clearing = ref(false);
async function confirmClear(): Promise<void> {
  clearing.value = false;
  await memory.clear();
}

// Last: it runs at once and needs everything above (open() uses editing).
watch(() => route.query.agent, (agent) => void (typeof agent === "string" && agent ? open(agent) : memory.load()), { immediate: true });
</script>

<template>
  <main class="memory">
    <h2><NotebookPen :size="16" /> {{ t("nav_memory") }}</h2>
    <p v-if="memory.notice" class="notice"><TriangleAlert :size="16" /> {{ memory.notice }}</p>
    <div class="split">
      <nav class="agents">
        <button
          v-for="agent in memory.agents"
          :key="agent.agent_id"
          :class="{ active: agent.agent_id === memory.agent }"
          @click="open(agent.agent_id)"
        >
          <span>{{ agent.emoji }} {{ agent.display_name }}</span><span class="count">{{ agent.count }}</span>
        </button>
      </nav>
      <section class="entries">
        <p v-if="!memory.agent" class="empty">{{ t("memory_select_hint") }}</p>
        <template v-else>
          <div class="bar">
            <select v-model="filter">
              <option value="">{{ t("memory_filter_all") }} ({{ memory.entries.length }})</option>
              <option v-for="type in types" :key="type" :value="type">{{ type }}</option>
            </select>
            <button v-if="memory.entries.length" class="danger" @click="clearing = true"><Trash2 :size="16" /> {{ t("agent_editor_clear_memories") }}</button>
          </div>
          <p v-if="!shown.length" class="empty">{{ t("memory_no_entries") }}</p>
          <article v-for="entry in shown" :key="entry.id" class="entry">
            <header>
              <span class="type">{{ entry.type }}</span>
              <span class="date">{{ entry.date.slice(0, 16).replace("T", " ") }}</span>
              <code class="id">{{ entry.id.slice(0, 8) }}</code>
              <span class="actions">
                <button :title="t('agent_editor_edit')" @click="startEdit(entry)"><Pencil :size="16" /></button>
                <button :title="t('agent_editor_delete')" @click="memory.remove(entry.id)"><Trash2 :size="16" /></button>
              </span>
            </header>
            <form v-if="editing?.id === entry.id" class="edit" @submit.prevent="saveEdit">
              <input v-model="editing.summary" required />
              <textarea v-model="editing.content" required></textarea>
              <div class="edit-actions">
                <button type="button" @click="editing = null">{{ t("agent_editor_cancel") }}</button>
                <button type="submit">{{ t("agent_editor_save") }}</button>
              </div>
            </form>
            <template v-else>
              <strong class="summary">{{ entry.summary }}</strong>
              <p class="content">{{ entry.content }}</p>
            </template>
          </article>
        </template>
      </section>
    </div>

    <ModalDialog v-if="clearing" @close="clearing = false">
      <strong>{{ t("agent_editor_clear_memories") }}: {{ t("agent_editor_really_forget") }}</strong>
      <div class="dialog-actions">
        <button @click="clearing = false">{{ t("agent_editor_cancel") }}</button>
        <button class="danger" @click="confirmClear">{{ t("agent_editor_forget") }}</button>
      </div>
    </ModalDialog>
  </main>
</template>

<style scoped>
.memory { padding: 12px 0; display: flex; flex-direction: column; gap: 12px; }
h2 { margin: 0; font-size: 1rem; }
.notice { margin: 0; color: var(--aifred-accent-amber); --icon-own: currentColor; }
.split { display: grid; grid-template-columns: minmax(180px, 240px) 1fr; gap: 14px; align-items: start; }
/* The list of agents stays in view while the page scrolls the entries (no scroll container of its own around it). */
.agents { display: flex; flex-direction: column; gap: 4px; position: sticky; top: 8px; }
.agents button { display: flex; justify-content: space-between; gap: 8px; text-align: left; }
.agents button.active { border-color: var(--aifred-primary); }
.count { color: var(--aifred-text-muted); font-size: 0.8rem; }
.entries { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.bar { display: flex; gap: 8px; align-items: center; }
.bar .danger { margin-left: auto; }
input, select, textarea { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 4px 8px; font: inherit; }
button { padding: 4px 8px; }
.empty { color: var(--aifred-text-muted); margin: 0; }
.entry { border: 1px solid var(--aifred-border); border-radius: 8px; padding: 8px 10px; display: flex; flex-direction: column; gap: 4px; }
.entry header { display: flex; gap: 10px; align-items: center; font-size: 0.75rem; color: var(--aifred-text-muted); }
.type { color: var(--aifred-accent-cyan); }
.actions { margin-left: auto; display: flex; gap: 4px; }
.actions button { background: none; border: none; padding: 2px 4px; }
.summary { font-size: 0.9rem; }
.content { margin: 0; font-size: 0.85rem; color: var(--aifred-text-secondary); white-space: pre-wrap; overflow-wrap: anywhere; }
.edit { display: flex; flex-direction: column; gap: 6px; }
.edit textarea { min-height: 8em; resize: vertical; }
.edit-actions, .dialog-actions { display: flex; gap: 8px; justify-content: flex-end; }
.dialog-actions { margin-top: 10px; }
@media (max-width: 760px) {
  .split { grid-template-columns: 1fr; }
}
</style>
