<script setup lang="ts">
// The agents' quick choices behind a button of the main screen (Peuqui 07.11.2026): every agent with
// its model and switches, the Automatik's model, the Vision agent's. A change takes effect at once,
// from the next turn on; it is a setting of AIfred, not of the open chat.
import { SlidersHorizontal, TriangleAlert } from "lucide-vue-next";
import { computed, onMounted } from "vue";
import { t } from "../i18n";
import { useAgentChoices } from "../stores/agentChoices";
import AgentChoicesRow from "./AgentChoicesRow.vue";
import HelpButton from "./help/HelpButton.vue";
import ModalDialog from "./ModalDialog.vue";

const emit = defineEmits<{ close: [] }>();
const store = useAgentChoices();
onMounted(() => void store.load());

const chatAgents = computed(() => store.choices?.agents.filter((row) => row.id !== "vision") ?? []);
const vision = computed(() => store.choices?.agents.find((row) => row.id === "vision"));
</script>

<template>
  <ModalDialog @close="emit('close')">
    <header class="head">
      <strong><SlidersHorizontal :size="16" /> {{ t("agent_choices_title") }}</strong>
      <HelpButton topic="thinking" />
      <button class="close" :title="t('dialog_close')" @click="emit('close')">×</button>
    </header>
    <p v-if="store.notice" class="notice"><TriangleAlert :size="16" /> {{ store.notice }}</p>
    <div v-if="store.choices" class="rows">
      <AgentChoicesRow
        v-for="row in chatAgents"
        :key="row.id"
        :label="`${row.display_name}-LLM:`"
        :model-id="row.model_id"
        :models="store.choices.models"
        :inherits="row.id !== 'aifred'"
        :row="row"
        :ranges="store.choices.sampling_ranges"
        :manual-offered="store.choices.manual_context_offered"
        :busy="store.busy"
        @change="(update) => store.change(row.id, update)"
      />
      <AgentChoicesRow
        :label="t('automatic_llm')"
        :model-id="store.choices.automatik_model_id"
        :models="store.choices.models"
        :inherits="true"
        :busy="store.busy"
        @change="(update) => store.change('automatik', update)"
      />
      <AgentChoicesRow
        v-if="vision"
        :label="t('vision_llm')"
        :model-id="vision.model_id"
        :models="store.choices.vision_models"
        :inherits="false"
        :row="vision"
        :ranges="store.choices.sampling_ranges"
        :manual-offered="store.choices.manual_context_offered"
        :busy="store.busy"
        @change="(update) => store.change('vision', update)"
      />
    </div>
    <p v-else-if="!store.notice" class="loading">…</p>
  </ModalDialog>
</template>

<style scoped>
.head { display: flex; align-items: center; gap: 12px; }
.head strong { margin-right: auto; }
.close { background: none; border: none; color: inherit; font-size: 1.3rem; cursor: pointer; }
.rows { width: min(720px, calc(88 * var(--vw))); }
.notice { margin: 0; color: var(--aifred-accent-amber); }
.loading { color: var(--aifred-text-muted); }
</style>
