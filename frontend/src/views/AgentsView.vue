<script setup lang="ts">
// The agents page (Peuqui 07.11.2026): a card per agent a chat can address, the switch top right as with
// the plugins. Switched off, an agent vanishes from every choice; a job or channel that names it reports
// an error. AIfred, the base agent, stays on and is never deleted. A card of an agent switched on carries
// its quick choices (model, persona, reasoning, thinking, speed: the row of the quick-choice modal). ✏️ opens
// the editor (persona, prompt texts, rights); ＋ makes a new agent from the template.
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import type { AgentCard } from "../api/types";
import AgentBundles from "../components/AgentBundles.vue";
import AgentChoicesRow from "../components/AgentChoicesRow.vue";
import AgentEditor from "../components/AgentEditor.vue";
import ModalDialog from "../components/ModalDialog.vue";
import { t } from "../i18n";
import { useAgentChoices } from "../stores/agentChoices";
import { useAgents } from "../stores/agents";

const agents = useAgents();
const choices = useAgentChoices();
onMounted(() => void agents.load());

// The quick choices by agent: only agents switched on have a row.
const rows = computed(() => new Map((choices.choices?.agents ?? []).map((row) => [row.id, row])));

const editing = ref<string | null>(null);
const bundles = ref<"export" | "import" | null>(null);

const creating = ref<{ id: string; display_name: string; emoji: string; description: string } | null>(null);
async function create(): Promise<void> {
  if (!creating.value) return;
  const agent = { ...creating.value, id: creating.value.id.trim(), display_name: creating.value.display_name.trim() };
  if (await agents.create(agent)) {
    creating.value = null;
    editing.value = agent.id;
  }
}

const deleting = ref<AgentCard | null>(null);
async function confirmDelete(): Promise<void> {
  if (!deleting.value) return;
  const id = deleting.value.id;
  deleting.value = null;
  await agents.remove(id);
}
</script>

<template>
  <main class="agents">
    <header class="bar">
      <h2>{{ t("nav_agents") }}</h2>
      <button :title="t('agent_editor_export_tooltip')" @click="bundles = 'export'">📦</button>
      <button :title="t('agent_editor_import_tooltip')" @click="bundles = 'import'">📥</button>
      <button @click="creating = { id: '', display_name: '', emoji: '🤖', description: '' }">＋ {{ t("agent_editor_new") }}</button>
    </header>
    <form v-if="creating" class="creating" @submit.prevent="create">
      <input v-model="creating.id" :placeholder="t('agent_editor_agent_id_placeholder')" :title="t('agent_editor_id_hint')" pattern="[a-z0-9_]+" required />
      <input v-model="creating.display_name" :placeholder="t('agent_editor_name')" required />
      <input v-model="creating.emoji" class="emoji" :placeholder="t('agent_editor_emoji')" required />
      <input v-model="creating.description" class="description-input" :placeholder="t('agent_editor_description')" />
      <button type="submit">{{ t("doc_create") }}</button>
      <button type="button" @click="creating = null">{{ t("agent_editor_cancel") }}</button>
    </form>
    <p v-if="agents.notice" class="notice">⚠️ {{ agents.notice }}</p>
    <p v-if="choices.notice" class="notice">⚠️ {{ choices.notice }}</p>
    <section class="cards">
      <article v-for="card in agents.cards" :key="card.id" class="card" :class="{ off: !card.enabled }">
        <header>
          <span class="emoji">{{ card.emoji }}</span>
          <div class="who">
            <strong>{{ card.display_name }}</strong>
            <span class="description">{{ card.description }}</span>
          </div>
          <label class="switch" :title="card.base ? t('agents_base_hint') : t('agents_switch_title')">
            <input
              type="checkbox"
              :checked="card.enabled"
              :disabled="card.base || agents.busy"
              @change="agents.switchAgent(card.id, !card.enabled)"
            />
            <span class="track"></span>
          </label>
        </header>
        <AgentChoicesRow
          v-if="card.enabled && rows.get(card.id) && choices.choices"
          :label="`${card.display_name}-LLM:`"
          :model-id="rows.get(card.id)!.model_id"
          :models="choices.choices.models"
          :inherits="!card.base"
          :row="rows.get(card.id)"
          :ranges="choices.choices.sampling_ranges"
          :busy="choices.busy"
          @change="(update) => choices.change(card.id, update)"
        />
        <p v-else-if="!card.enabled" class="off-hint">{{ t("agents_switched_off") }}</p>
        <footer>
          <RouterLink class="button" :to="{ path: '/memory', query: { agent: card.id } }">🧠 {{ t("agent_open_memory") }}</RouterLink>
          <button :title="t('agent_editor_edit')" @click="editing = card.id">✏️ {{ t("agent_editor_edit") }}</button>
          <button
            class="danger"
            :disabled="card.delete_refusal !== null || agents.busy"
            :title="card.delete_refusal ?? t('agent_editor_delete_agent')"
            @click="deleting = card"
          >🗑️ {{ t("agent_editor_delete_agent") }}</button>
        </footer>
      </article>
    </section>

    <AgentEditor v-if="editing" :agent-id="editing" @close="editing = null" />
    <AgentBundles v-if="bundles" :mode="bundles" :cards="agents.cards" @close="bundles = null" @imported="agents.load()" />

    <ModalDialog v-if="deleting" @close="deleting = null">
      <strong>{{ deleting.emoji }} {{ deleting.display_name }}: {{ t("agent_editor_delete_confirm") }}</strong>
      <div class="dialog-actions">
        <button @click="deleting = null">{{ t("doc_cancel") }}</button>
        <button class="danger" @click="confirmDelete">{{ t("agent_editor_delete") }}</button>
      </div>
    </ModalDialog>
  </main>
</template>

<style scoped>
.agents { overflow-y: auto; padding: 12px 0; display: flex; flex-direction: column; gap: 12px; }
h2 { margin: 0; font-size: 1rem; }
.bar { display: flex; align-items: center; gap: 14px; }
.bar button:first-of-type { margin-left: auto; }
.creating { display: flex; gap: 8px; flex-wrap: wrap; }
.creating input { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 4px 8px; font: inherit; }
.creating .emoji { width: 4em; }
.creating .description-input { flex: 1; min-width: 12em; }
.notice { margin: 0; color: var(--aifred-accent-amber); }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 420px), 1fr)); gap: 12px; }
.card { border: 1px solid var(--aifred-border); border-radius: 10px; padding: 12px; display: flex; flex-direction: column; gap: 10px; background: var(--aifred-input-bg); }
.card.off { opacity: 0.6; }
.card header { display: flex; align-items: flex-start; gap: 10px; }
.emoji { font-size: 1.6rem; line-height: 1; }
.who { display: flex; flex-direction: column; gap: 2px; flex: 1; min-width: 0; }
.description { font-size: 0.8rem; color: var(--aifred-text-secondary); overflow-wrap: anywhere; }
.off-hint { margin: 0; font-size: 0.85rem; color: var(--aifred-text-muted); }
.card footer { display: flex; justify-content: flex-end; gap: 8px; }
button, a.button { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 4px 8px; font: inherit; cursor: pointer; text-decoration: none; }
button:disabled { opacity: 0.5; cursor: default; }
.danger { border-color: var(--aifred-danger); color: var(--aifred-danger); }
.dialog-actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 10px; }
.switch { position: relative; display: inline-block; width: 40px; height: 22px; flex: none; cursor: pointer; }
.switch input { opacity: 0; width: 0; height: 0; }
.track { position: absolute; inset: 0; background: var(--aifred-border); border-radius: 11px; transition: background 0.2s; }
.track::before { content: ""; position: absolute; width: 16px; height: 16px; left: 3px; top: 3px; background: var(--aifred-text-primary); border-radius: 50%; transition: transform 0.2s; }
.switch input:checked + .track { background: var(--aifred-primary); }
.switch input:checked + .track::before { transform: translateX(18px); }
.switch input:disabled + .track { opacity: 0.5; cursor: default; }
</style>
