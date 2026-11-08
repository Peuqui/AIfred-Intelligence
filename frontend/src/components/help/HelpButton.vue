<script setup lang="ts">
// The 💡 next to a control: it opens the help of its topic.
import { ref } from "vue";
import HelpDialog from "./HelpDialog.vue";
import { HELP_TOPICS, type HelpTopic } from "./topics";

defineProps<{ topic: HelpTopic }>();
const open = ref(false);
</script>

<template>
  <button class="help" type="button" :title="HELP_TOPICS[topic].tooltip()" @click="open = true">💡</button>
  <HelpDialog v-if="open" :topic="topic" @close="open = false" />
</template>

<style scoped>
.help { background: none; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 2px 6px; cursor: pointer; font: inherit; }
</style>
