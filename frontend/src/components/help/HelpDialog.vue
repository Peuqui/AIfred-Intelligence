<script setup lang="ts">
// A help over the page: the text of its topic. A topic can lead to another (OPEN_HELP), the dialog then shows that one.
import { Lightbulb } from "lucide-vue-next";
import { type Component, provide, ref } from "vue";
import { t } from "../../i18n";
import ModalDialog from "../ModalDialog.vue";
import CasusHelp from "./CasusHelp.vue";
import DebateHelp from "./DebateHelp.vue";
import LifecycleHelp from "./LifecycleHelp.vue";
import ThinkingHelp from "./ThinkingHelp.vue";
import ChatToolbarHelp from "./ChatToolbarHelp.vue";
import VigilantiaHelp from "./VigilantiaHelp.vue";
import { HELP_TOPICS, type HelpTopic, OPEN_HELP } from "./topics";

const props = defineProps<{ topic: HelpTopic }>();
const emit = defineEmits<{ close: [] }>();
const current = ref<HelpTopic>(props.topic);
provide(OPEN_HELP, (topic) => (current.value = topic));

const BODIES: Record<HelpTopic, Component> = {
  chat: ChatToolbarHelp, debate: DebateHelp, thinking: ThinkingHelp, lifecycle: LifecycleHelp, vigilantia: VigilantiaHelp, casus: CasusHelp,
};
</script>

<template>
  <ModalDialog @close="emit('close')">
    <div class="help">
      <header class="head">
        <strong><Lightbulb :size="16" /> {{ HELP_TOPICS[current].title() }}</strong>
        <button class="close" :title="t('dialog_close')" @click="emit('close')">×</button>
      </header>
      <component :is="BODIES[current]" />
      <button class="done" @click="emit('close')">{{ t("dialog_close") }}</button>
    </div>
  </ModalDialog>
</template>

<style scoped>
.help { display: flex; flex-direction: column; gap: 14px; width: min(680px, calc(90 * var(--vw))); line-height: 1.5; }
.head { display: flex; align-items: center; justify-content: space-between; gap: 12px; font-size: 1.05rem; }
.head strong { color: var(--aifred-accent-gold); --icon-own: currentColor; }
.close { background: none; border: none; color: inherit; font-size: 1.3rem; cursor: pointer; }
.done { align-self: center; padding: 6px 18px; background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; font: inherit; cursor: pointer; }
</style>
