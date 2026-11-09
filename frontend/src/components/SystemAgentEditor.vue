<script setup lang="ts">
// A system agent's or the Automatik LLM's editor on the agents page: 📝 its prompt layers and, for a cloud workflow
// (calibration), ☁️ provider, model (listed live by the provider) and reasoning. Each tab saves on its own.
import { onMounted, ref, watch } from "vue";
import { ApiError } from "../api/client";
import type { SystemAgentCard } from "../api/types";
import { t } from "../i18n";
import { useAgents } from "../stores/agents";
import ModalDialog from "./ModalDialog.vue";
import PromptLayers from "./PromptLayers.vue";

const props = defineProps<{ card: SystemAgentCard }>();
const emit = defineEmits<{ close: [] }>();
const agents = useAgents();

const tab = ref<"prompts" | "cloud">("prompts");
const provider = ref(props.card.cloud?.provider ?? "");
const model = ref(props.card.cloud?.model ?? "");
const reasoning = ref(props.card.cloud?.reasoning ?? false);
const models = ref<string[]>([]);
const notes = ref<string[]>([]);
const notice = ref("");
const saved = ref("");

async function listModels(): Promise<void> {
  notice.value = "";
  try {
    const listed = await agents.cloudModels(props.card.id, provider.value);
    models.value = listed.models;
    notes.value = listed.notes;
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
  }
}
watch(provider, listModels);
onMounted(() => {
  if (props.card.cloud) void listModels();
});

async function saveCloud(): Promise<void> {
  notice.value = "";
  saved.value = "";
  try {
    await agents.changeCloud(props.card.id, { provider: provider.value, model: model.value, reasoning: reasoning.value });
    saved.value = t("agents_saved");
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
  }
}
</script>

<template>
  <ModalDialog @close="emit('close')">
    <div class="editor">
      <header class="head">
        <strong>{{ card.label }}</strong>
        <nav class="tabs">
          <button :class="{ active: tab === 'prompts' }" @click="tab = 'prompts'">📝 {{ t("agent_editor_prompts") }}</button>
          <button v-if="card.cloud" :class="{ active: tab === 'cloud' }" @click="tab = 'cloud'">{{ t("agent_editor_cloud_tab") }}</button>
        </nav>
        <button class="close" :title="t('dialog_close')" @click="emit('close')">×</button>
      </header>

      <PromptLayers v-if="tab === 'prompts'" :agent-id="card.id" :keys="card.prompt_keys" :languages="card.languages" />

      <section v-if="tab === 'cloud' && card.cloud" class="cloud">
        <p v-if="notice" class="notice">⚠️ {{ notice }}</p>
        <p v-else-if="saved" class="saved">✅ {{ saved }}</p>
        <label>{{ t("agent_editor_cloud_provider") }}
          <select v-model="provider">
            <option v-for="choice in card.cloud.providers" :key="choice.id" :value="choice.id">
              {{ choice.name }}{{ choice.configured ? "" : ` (${t("agent_editor_cloud_no_key")})` }}
            </option>
          </select>
        </label>
        <label>{{ t("agent_editor_cloud_model") }}
          <select v-model="model">
            <option v-if="model && !models.includes(model)" :value="model">{{ model }}</option>
            <option v-for="name in models" :key="name" :value="name">{{ name }}</option>
          </select>
        </label>
        <p v-for="note in notes" :key="note" class="hint">{{ note }}</p>
        <label class="switch" :title="t('agent_editor_reasoning_tooltip')">
          <input v-model="reasoning" type="checkbox" /> 💭 {{ t("agent_editor_reasoning_toggle") }}
        </label>
        <p class="hint">{{ t("agent_editor_cloud_model_hint") }}</p>
        <div class="actions"><button :disabled="!model" @click="saveCloud">{{ t("agent_editor_save") }}</button></div>
      </section>
    </div>
  </ModalDialog>
</template>

<style scoped>
.editor { width: min(860px, calc(88 * var(--vw))); display: flex; flex-direction: column; gap: 10px; }
.head { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.tabs { display: flex; gap: 6px; flex: 1; flex-wrap: wrap; }
.tabs button.active { border-color: var(--aifred-primary); color: var(--aifred-primary); }
.close { background: none; border: none; font-size: 1.2rem; }
button, select { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 4px 8px; font: inherit; cursor: pointer; }
button:disabled { opacity: 0.5; cursor: default; }
input[type="checkbox"] { accent-color: var(--aifred-primary); }
.cloud { display: flex; flex-direction: column; gap: 10px; }
.cloud label { display: flex; flex-direction: column; gap: 4px; font-size: 0.85rem; color: var(--aifred-text-secondary); }
.cloud .switch { flex-direction: row; align-items: center; gap: 6px; color: var(--aifred-text-primary); }
.notice { margin: 0; color: var(--aifred-accent-amber); }
.saved { margin: 0; color: var(--aifred-accent-cyan); }
.hint { margin: 0; font-size: 0.85rem; color: var(--aifred-text-muted); }
.actions { display: flex; justify-content: flex-end; }
</style>
