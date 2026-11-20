<script setup lang="ts">
// The answer being written (its sections rendered as they come) or, before any text, the turn's progress line on its
// own. ChatView places it in the last turn, where the answer lands once done, so that the turn's question sticks above it.
import type { StreamSection } from "../lib/streamBlocks";
import type { ProgressLine } from "../lib/turnProgress";
import { useChat } from "../stores/chat";
import TurnProgress from "./TurnProgress.vue";

defineProps<{ sections: StreamSection[]; progress: ProgressLine | null; color?: string }>();
const chat = useChat();
</script>

<template>
  <article v-if="chat.streaming" class="streaming" :style="color ? { '--agent-color': color } : undefined">
    <header>{{ chat.streaming.emoji }} {{ chat.streaming.displayName }}</header>
    <!-- eslint-disable-next-line vue/no-v-html -- sanitized by renderMarkdown (DOMPurify) -->
    <div v-for="(section, part) in sections" :key="part" v-html="section.html" />
    <p v-if="chat.toolStatus" class="tool">{{ chat.toolStatus }}</p>
    <TurnProgress v-if="progress" :line="progress" />
  </article>
  <TurnProgress v-else-if="progress" class="waiting" :line="progress" />
</template>

<style scoped>
/* As the finished bubble (MessageBubble), its rim dashed while it streams. */
.streaming {
  box-sizing: border-box; width: 100%; padding: 10px 14px; border-radius: 10px; border: 1px dashed var(--aifred-border-light);
  border-left: 1px solid var(--agent-color, var(--aifred-border-light));
  color: var(--aifred-answer-text);
}
.streaming header { font-size: var(--text-small); font-weight: 600; color: var(--agent-color, var(--aifred-primary)); }
.tool { margin: 4px 0 0; font-size: var(--text-small); color: var(--aifred-accent-cyan); }
.waiting { align-self: flex-start; padding: 0 14px; }
</style>
