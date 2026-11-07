<script setup lang="ts">
import { computed } from "vue";
import type { ChatEntry } from "../api/types";
import { renderMarkdown } from "../lib/markdown";

const props = defineProps<{ entry: ChatEntry }>();
const html = computed(() => renderMarkdown(props.entry.content));
const isUser = computed(() => props.entry.role === "user");
</script>

<template>
  <article class="bubble" :class="isUser ? 'user' : 'assistant'">
    <header v-if="!isUser && entry.agent_display_name">
      {{ entry.agent_emoji }} {{ entry.agent_display_name }}
      <span class="time">{{ entry.time_display }}</span>
    </header>
    <!-- eslint-disable-next-line vue/no-v-html -- sanitized by renderMarkdown (DOMPurify) -->
    <div class="content" v-html="html" />
  </article>
</template>

<style scoped>
.bubble { max-width: min(860px, 92%); padding: 10px 14px; border-radius: 10px; line-height: 1.45; overflow-wrap: anywhere; }
.user { align-self: flex-end; background: var(--aifred-input-bg); }
.assistant { align-self: flex-start; background: var(--aifred-card-bg); border: 1px solid var(--aifred-border); }
header { font-size: 0.8rem; font-weight: 600; color: var(--aifred-primary); margin-bottom: 4px; display: flex; gap: 8px; }
.time { color: var(--aifred-text-muted); font-weight: 400; }
.content :deep(p) { margin: 0.5em 0; }
.content :deep(pre) { overflow-x: auto; background: var(--aifred-page-bg); padding: 8px; border-radius: 6px; }
.content :deep(details) { margin: 0.3em 0; }
.content :deep(table) { border-collapse: collapse; }
.content :deep(td), .content :deep(th) { border: 1px solid var(--aifred-border); padding: 2px 6px; }
.content :deep(img) { max-width: 100%; }
.content :deep(a) { color: var(--aifred-accent-blue); }
</style>
