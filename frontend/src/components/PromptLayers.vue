<script setup lang="ts">
// The prompt layers of an agent, a system agent or the Automatik LLM (the same endpoints): pick layer and language,
// edit the text, save; it takes effect from the next call on. A language without the file shows that it is missing.
import { computed, onMounted, ref, watch } from "vue";
import { ApiError } from "../api/client";
import { language, t } from "../i18n";
import { useAgents } from "../stores/agents";

const props = defineProps<{ agentId: string; keys: string[]; languages: string[] }>();
const agents = useAgents();

const lang = ref("");
const key = ref("");
const text = ref("");
const original = ref("");
const exists = ref(true);
const notice = ref("");
const saved = ref("");
const dirty = computed(() => text.value !== original.value);

async function attempt(work: () => Promise<void>, done: string): Promise<void> {
  notice.value = "";
  saved.value = "";
  try {
    await work();
    saved.value = done;
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
  }
}

async function load(): Promise<void> {
  if (!key.value || !lang.value) return;
  await attempt(async () => {
    const loaded = await agents.prompt(props.agentId, lang.value, key.value);
    text.value = original.value = loaded.text;
    exists.value = loaded.exists;
  }, "");
}
watch([lang, key], load);

const save = (): Promise<void> =>
  attempt(async () => {
    const written = await agents.writePrompt(props.agentId, lang.value, key.value, text.value);
    original.value = written.text;
    exists.value = written.exists;
  }, t("agents_saved"));

onMounted(() => {
  // The interface's language when there are prompts in it; an empty list leaves none.
  lang.value = props.languages.includes(language.value) ? language.value : (props.languages[0] ?? "");
  key.value = props.keys[0] ?? "";
});
</script>

<template>
  <section class="prompts">
    <p v-if="notice" class="notice">⚠️ {{ notice }}</p>
    <p v-else-if="saved" class="saved">✅ {{ saved }}</p>
    <div class="pick">
      <select v-model="key">
        <option v-for="layer in keys" :key="layer" :value="layer">{{ layer }}</option>
      </select>
      <select v-model="lang">
        <option v-for="language in languages" :key="language" :value="language">{{ language.toUpperCase() }}</option>
      </select>
      <span v-if="dirty" class="dirty">{{ t("agent_editor_unsaved_warning") }}</span>
    </div>
    <p v-if="!exists" class="hint">{{ t("agents_prompt_missing") }}</p>
    <textarea v-model="text" spellcheck="false"></textarea>
    <div class="actions">
      <button :disabled="!dirty" @click="text = original">{{ t("agent_editor_discard") }}</button>
      <button :disabled="!dirty && exists" @click="save">{{ t("agent_editor_save") }}</button>
    </div>
  </section>
</template>

<style scoped>
.prompts { display: flex; flex-direction: column; gap: 10px; }
button, select, textarea { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 4px 8px; font: inherit; }
button { cursor: pointer; }
button:disabled { opacity: 0.5; cursor: default; }
.notice { margin: 0; color: var(--aifred-accent-amber); }
.saved { margin: 0; color: var(--aifred-accent-cyan); }
.pick { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.dirty { color: var(--aifred-accent-amber); font-size: 0.85rem; }
.hint { margin: 0; font-size: 0.85rem; color: var(--aifred-text-muted); }
textarea { width: 100%; min-height: 45dvh; resize: vertical; font-family: monospace; font-size: 0.85rem; box-sizing: border-box; }
.actions { display: flex; gap: 8px; justify-content: flex-end; }
</style>
