<script setup lang="ts">
// The agents page (Peuqui 07.11.2026): a card per agent a chat can address, the switch top right as with
// the plugins. Switched off, an agent vanishes from every choice; a job or channel that names it reports
// an error. AIfred, the base agent, stays on and is never deleted. A card of an agent switched on carries
// its quick choices (model, persona, reasoning, thinking, speed: the row of the quick-choice modal). ✏️ opens
// the editor (persona, prompt texts, rights); ＋ makes a new agent from the template.
import { computed, onMounted, ref } from "vue";
import { Bot, Cloud, Import, NotebookPen, Package, Pencil, Trash2, TriangleAlert } from "lucide-vue-next";
import { RouterLink } from "vue-router";
import type { AgentCard, SystemAgentCard } from "../api/types";
import AgentBundles from "../components/AgentBundles.vue";
import AgentChoicesRow from "../components/AgentChoicesRow.vue";
import AgentEditor from "../components/AgentEditor.vue";
import ToggleSwitch from "../components/ToggleSwitch.vue";
import ModalDialog from "../components/ModalDialog.vue";
import SystemAgentEditor from "../components/SystemAgentEditor.vue";
import HelpButton from "../components/help/HelpButton.vue";
import { t } from "../i18n";
import { useAgentChoices } from "../stores/agentChoices";
import { useAgents } from "../stores/agents";
import { useSettings } from "../stores/settings";

const agents = useAgents();
const settings = useSettings();
const choices = useAgentChoices();
onMounted(() => void agents.load());

// The quick choices by agent: only agents switched on have a row.
const rows = computed(() => new Map((choices.choices?.agents ?? []).map((row) => [row.id, row])));

const editing = ref<string | null>(null);
const editingSystem = ref<SystemAgentCard | null>(null);
const bundles = ref<"export" | "import" | null>(null);

const creating = ref<{ id: string; display_name: string; emoji: string; description: string; color: string } | null>(null);
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
      <h2><Bot :size="16" /> {{ t("nav_agents") }}</h2>
      <HelpButton topic="thinking" />
      <button :title="t('agent_editor_export_tooltip')" @click="bundles = 'export'"><Package :size="16" /></button>
      <button :title="t('agent_editor_import_tooltip')" @click="bundles = 'import'"><Import :size="16" /></button>
      <button v-if="settings.current" @click="creating = { id: '', display_name: '', emoji: '🤖', description: '', color: settings.current.agent_default_color }">＋ {{ t("agent_editor_new") }}</button>
    </header>
    <form v-if="creating" class="creating" @submit.prevent="create">
      <input v-model="creating.id" :placeholder="t('agent_editor_agent_id_placeholder')" :title="t('agent_editor_id_hint')" pattern="[a-z0-9_]+" required />
      <input v-model="creating.display_name" :placeholder="t('agent_editor_name')" required />
      <input v-model="creating.emoji" class="emoji" :placeholder="t('agent_editor_emoji')" required />
      <input v-model="creating.color" type="color" class="color" :title="t('agent_editor_color_hint')" />
      <input v-model="creating.description" class="description-input" :placeholder="t('agent_editor_description')" />
      <button type="submit">{{ t("doc_create") }}</button>
      <button type="button" @click="creating = null">{{ t("agent_editor_cancel") }}</button>
    </form>
    <p v-if="agents.notice" class="notice"><TriangleAlert :size="16" /> {{ agents.notice }}</p>
    <p v-if="choices.notice" class="notice"><TriangleAlert :size="16" /> {{ choices.notice }}</p>
    <section class="cards">
      <article v-for="card in agents.cards" :key="card.id" class="card" :class="{ off: !card.enabled }">
        <header>
          <span class="emoji">{{ card.emoji }}</span>
          <div class="who">
            <strong>{{ card.display_name }}</strong>
            <span class="description">{{ card.description }}</span>
          </div>
          <ToggleSwitch
            :on="card.enabled"
            :disabled="card.base || agents.busy"
            :title="card.base ? t('agents_base_hint') : t('agents_switch_title')"
            @toggle="(on) => agents.switchAgent(card.id, on)"
          />
        </header>
        <AgentChoicesRow
          v-if="card.enabled && rows.get(card.id) && choices.choices"
          :label="`${card.display_name}-LLM:`"
          :model-id="rows.get(card.id)!.model_id"
          :models="choices.choices.models"
          :inherits="!card.base"
          :row="rows.get(card.id)"
          :ranges="choices.choices.sampling_ranges"
          :manual-offered="choices.choices.manual_context_offered"
          :busy="choices.busy"
          @change="(update) => choices.change(card.id, update)"
        />
        <p v-else-if="!card.enabled" class="off-hint">{{ t("agents_switched_off") }}</p>
        <footer>
          <RouterLink class="button" :to="{ path: '/memory', query: { agent: card.id } }"><NotebookPen :size="16" /> {{ t("agent_open_memory") }}</RouterLink>
          <button :title="t('agent_editor_edit')" @click="editing = card.id"><Pencil :size="16" /> {{ t("agent_editor_edit") }}</button>
          <button
            class="danger"
            :disabled="card.delete_refusal !== null || agents.busy"
            :title="card.delete_refusal ?? t('agent_editor_delete_agent')"
            @click="deleting = card"
          ><Trash2 :size="16" /> {{ t("agent_editor_delete_agent") }}</button>
        </footer>
      </article>
    </section>

    <h3>{{ t("agents_system_title") }}</h3>
    <p class="system-hint">{{ t("agents_system_hint") }}</p>
    <section class="cards">
      <article v-for="card in agents.system" :key="card.id" class="card">
        <header><div class="who"><strong>{{ card.label }}</strong><span v-if="card.cloud" class="description"><Cloud :size="16" /> {{ card.cloud.model }}</span></div></header>
        <footer>
          <button :title="t('agent_editor_edit')" @click="editingSystem = card"><Pencil :size="16" /> {{ t("agent_editor_edit") }}</button>
        </footer>
      </article>
    </section>

    <AgentEditor v-if="editing" :agent-id="editing" @close="editing = null" />
    <SystemAgentEditor v-if="editingSystem" :card="editingSystem" @close="editingSystem = null" />
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
h3 { margin: 8px 0 0; font-size: 0.95rem; }
.system-hint { margin: 0; font-size: 0.85rem; color: var(--aifred-text-secondary); }
.bar { display: flex; align-items: center; gap: 14px; }
.bar button:first-of-type { margin-left: auto; }
.creating { display: flex; gap: 8px; flex-wrap: wrap; }
.creating input { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 4px 8px; font: inherit; }
.creating .emoji { width: 4em; }
.creating .color { width: 3em; height: 32px; padding: 2px; cursor: pointer; }
.creating .description-input { flex: 1; min-width: 12em; }
.notice { margin: 0; color: var(--aifred-accent-amber); --icon-own: currentColor; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 420px), 1fr)); gap: 12px; }
.card { border: 1px solid var(--aifred-border); border-radius: 10px; padding: 12px; display: flex; flex-direction: column; gap: 10px; background: var(--aifred-input-bg); }
.card.off { opacity: 0.6; }
.card header { display: flex; align-items: flex-start; gap: 10px; }
.emoji { font-size: 1.6rem; line-height: 1; }
.who { display: flex; flex-direction: column; gap: 2px; flex: 1; min-width: 0; }
.description { font-size: 0.8rem; color: var(--aifred-text-secondary); overflow-wrap: anywhere; }
.off-hint { margin: 0; font-size: 0.85rem; color: var(--aifred-text-muted); }
.card footer { display: flex; justify-content: flex-end; gap: 8px; }
button, a.button { padding: 4px 8px; text-decoration: none; }
a.button { display: inline-flex; align-items: center; gap: 4px; }
.dialog-actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 10px; }
</style>
