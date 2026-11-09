<script setup lang="ts">
// An agent's editor on the agents page: 🎭 persona (name, emoji, description, further names for the speech
// recognition), the prompt layers per language, and 🔐 rights (the tools it may use, grouped by plugin as
// they are on offer now; "every tool" includes plugins switched on later). Each tab saves on its own.
import { onMounted, ref } from "vue";
import { ApiError } from "../api/client";
import type { AgentPersona, AgentRights } from "../api/types";
import { t } from "../i18n";
import { useAgents } from "../stores/agents";
import ModalDialog from "./ModalDialog.vue";
import ToggleSwitch from "./ToggleSwitch.vue";
import PromptLayers from "./PromptLayers.vue";

const props = defineProps<{ agentId: string }>();
const emit = defineEmits<{ close: [] }>();
const agents = useAgents();

type Tab = "persona" | "prompts" | "rights";
const tab = ref<Tab>("persona");
const notice = ref("");
const saved = ref("");

async function attempt(work: () => Promise<void>, done = t("agents_saved")): Promise<void> {
  notice.value = "";
  saved.value = "";
  try {
    await work();
    saved.value = done;
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
  }
}

// ── persona ──
const persona = ref<AgentPersona | null>(null);
const form = ref({ display_name: "", emoji: "", description: "", aliases: "" });
function fill(from: AgentPersona): void {
  persona.value = from;
  form.value = { display_name: from.display_name, emoji: from.emoji, description: from.description, aliases: from.aliases.join(", ") };
}
const savePersona = (): Promise<void> =>
  attempt(async () => {
    fill(await agents.changePersona(props.agentId, {
      display_name: form.value.display_name.trim(),
      emoji: form.value.emoji.trim(),
      description: form.value.description.trim(),
      aliases: form.value.aliases.split(","),
    }));
  });

// ── rights ──
const rights = ref<AgentRights | null>(null);
const all = ref(true);
const allowed = ref(new Set<string>());
function takeRights(from: AgentRights): void {
  rights.value = from;
  all.value = from.all;
  allowed.value = new Set(from.groups.flatMap((group) => group.tools.filter((tool) => tool.allowed).map((tool) => tool.name)));
}
function toggleTool(name: string): void {
  const next = new Set(allowed.value);
  if (next.has(name)) next.delete(name);
  else next.add(name);
  allowed.value = next;
}
function setGroup(names: string[], on: boolean): void {
  const next = new Set(allowed.value);
  for (const name of names) {
    if (on) next.add(name);
    else next.delete(name);
  }
  allowed.value = next;
}
const saveRights = (): Promise<void> =>
  attempt(async () => takeRights(await agents.changeRights(props.agentId, all.value ? null : [...allowed.value])));

onMounted(() =>
  attempt(async () => {
    const [loadedPersona, loadedRights] = await Promise.all([agents.persona(props.agentId), agents.rights(props.agentId)]);
    fill(loadedPersona);
    takeRights(loadedRights);
  }, ""),
);
</script>

<template>
  <ModalDialog @close="emit('close')">
    <div class="editor">
      <header class="head">
        <strong>{{ persona?.emoji }} {{ persona?.display_name }}</strong>
        <nav class="tabs">
          <button :class="{ active: tab === 'persona' }" @click="tab = 'persona'">{{ t("agents_tab_persona") }}</button>
          <button :class="{ active: tab === 'prompts' }" @click="tab = 'prompts'">📝 {{ t("agent_editor_prompts") }}</button>
          <button :class="{ active: tab === 'rights' }" @click="tab = 'rights'">{{ t("agents_tab_rights") }}</button>
        </nav>
        <button class="close" :title="t('dialog_close')" @click="emit('close')">×</button>
      </header>
      <p v-if="notice" class="notice">⚠️ {{ notice }}</p>
      <p v-else-if="saved" class="saved">✅ {{ saved }}</p>

      <form v-if="tab === 'persona' && persona" class="persona" @submit.prevent="savePersona">
        <label>{{ t("agent_editor_name") }}<input v-model="form.display_name" required /></label>
        <label>{{ t("agent_editor_emoji") }}<input v-model="form.emoji" class="emoji" required /></label>
        <label class="wide">{{ t("agent_editor_description") }}<input v-model="form.description" /></label>
        <label class="wide">{{ t("agents_aliases") }}<input v-model="form.aliases" /></label>
        <div class="actions wide"><button type="submit">{{ t("agent_editor_save") }}</button></div>
      </form>

      <PromptLayers v-if="tab === 'prompts' && persona" :agent-id="agentId" :keys="persona.prompt_keys" :languages="persona.languages" />

      <section v-if="tab === 'rights' && rights" class="rights">
        <label class="all"><input v-model="all" type="checkbox" /> {{ t("agents_all_tools") }}</label>
        <fieldset v-for="group in rights.groups" :key="group.name" :disabled="all">
          <legend>
            {{ group.name }}
            <!-- On only when every tool of the group is allowed; flipping it allows or forbids them all (Reflex's switch). -->
            <ToggleSwitch
              :on="all || group.tools.every((tool) => allowed.has(tool.name))"
              :disabled="all"
              @toggle="(on) => setGroup(group.tools.map((tool) => tool.name), on)"
            />
          </legend>
          <!-- A pill per tool, as on the Reflex page: orange when allowed, grey when not; beside it its security tier. -->
          <div class="pills">
            <span v-for="tool in group.tools" :key="tool.name" class="tool">
              <button
                type="button"
                class="pill"
                :class="{ on: all || allowed.has(tool.name) }"
                :aria-pressed="all || allowed.has(tool.name)"
                @click="toggleTool(tool.name)"
              >
                {{ tool.name }}
              </button>
              <span class="tier" :class="`t${tool.tier}`">T{{ tool.tier }}</span>
            </span>
          </div>
        </fieldset>
        <div class="actions"><button @click="saveRights">{{ t("agent_editor_save") }}</button></div>
      </section>
    </div>
  </ModalDialog>
</template>

<style scoped>
.editor { width: min(860px, 88vw); display: flex; flex-direction: column; gap: 10px; }
.head { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.tabs { display: flex; gap: 6px; flex: 1; flex-wrap: wrap; }
.tabs button.active { border-color: var(--aifred-primary); color: var(--aifred-primary); }
.close { background: none; border: none; font-size: 1.2rem; }
/* border-box: a field at width 100 % keeps its padding and border inside (the emoji field ran over the edge). */
button, input:not([type="checkbox"]), select { box-sizing: border-box; background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 4px 8px; font: inherit; }
button { cursor: pointer; }
button:disabled { opacity: 0.5; cursor: default; }
input[type="checkbox"] { accent-color: var(--aifred-primary); }
.notice { margin: 0; color: var(--aifred-accent-amber); }
.saved { margin: 0; color: var(--aifred-accent-cyan); }
.persona { display: grid; grid-template-columns: 1fr 120px; gap: 10px; }
.persona label { display: flex; flex-direction: column; gap: 4px; font-size: 0.85rem; color: var(--aifred-text-secondary); }
.persona .wide { grid-column: 1 / -1; }
.emoji { width: 100%; }
.actions { display: flex; gap: 8px; justify-content: flex-end; }
.rights { display: flex; flex-direction: column; gap: 10px; max-height: 60dvh; overflow-y: auto; }
fieldset { border: 1px solid var(--aifred-border); border-radius: 8px; display: flex; flex-wrap: wrap; gap: 6px 14px; padding: 8px 10px; }
fieldset:disabled { opacity: 0.55; }
legend { display: flex; gap: 6px; align-items: center; font-weight: 500; padding: 0 4px; }
legend button { font-size: 0.75rem; padding: 1px 6px; }
.pills { display: flex; flex-wrap: wrap; gap: 6px 10px; }
.tool { display: inline-flex; gap: 3px; align-items: center; }
.pill { border-radius: 999px; padding: 2px 10px; font-size: var(--text-small); color: var(--aifred-text-muted); background: var(--aifred-input-bg); border: 1px solid var(--aifred-border); cursor: pointer; }
.pill.on { color: var(--aifred-primary); background: color-mix(in srgb, var(--aifred-primary) 18%, transparent); border-color: var(--aifred-primary); }
.pill:disabled { cursor: default; }
/* The security tiers' colours of the Reflex page: T0 green, T1 blue, T2 amber, T3 red, T4 purple. */
.tier { font-size: 0.65rem; font-weight: 700; color: var(--aifred-text-muted); }
.tier.t0 { color: var(--aifred-accent-success); }
.tier.t1 { color: var(--aifred-accent-blue); }
.tier.t2 { color: var(--aifred-accent-amber); }
.tier.t3 { color: var(--aifred-danger); }
.tier.t4 { color: var(--aifred-accent-purple); }
@media (max-width: 760px) {
  .persona { grid-template-columns: 1fr; }
}
</style>
