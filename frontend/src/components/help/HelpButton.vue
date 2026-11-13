<script setup lang="ts">
// The light bulb next to a control: it opens the help of its topic.
import { Lightbulb } from "lucide-vue-next";
import { ref } from "vue";
import HelpDialog from "./HelpDialog.vue";
import { HELP_TOPICS, type HelpTopic } from "./topics";

defineProps<{ topic: HelpTopic }>();
const open = ref(false);
</script>

<template>
  <button class="help" type="button" :title="HELP_TOPICS[topic].tooltip()" @click="open = true"><Lightbulb :size="14" /></button>
  <HelpDialog v-if="open" :topic="topic" @close="open = false" />
</template>

<style scoped>
/* A bare bulb as in the Reflex interface; still a button for the keyboard and screen readers. */
.help { background: none; border: none; padding: 0; cursor: pointer; font: inherit; color: var(--aifred-accent-amber); transition: transform 0.2s ease; }
.help:hover { transform: scale(1.15); }
</style>
