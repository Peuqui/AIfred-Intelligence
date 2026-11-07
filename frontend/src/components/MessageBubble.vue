<script setup lang="ts">
import { computed } from "vue";
import type { ChatEntry } from "../api/types";
import { t } from "../i18n";
import { renderMarkdown } from "../lib/markdown";
import { replay } from "../lib/speech";

const props = defineProps<{ entry: ChatEntry }>();
const html = computed(() => renderMarkdown(props.entry.content));
const isUser = computed(() => props.entry.role === "user");
const metadata = computed(() => props.entry.metadata ?? { audio_urls: [], browser_speech: [] });
const canReplay = computed(() => (metadata.value.audio_urls?.length ?? 0) + (metadata.value.browser_speech?.length ?? 0) > 0);

function play(): void {
  replay(metadata.value.audio_urls ?? [], metadata.value.browser_speech ?? []);
}
</script>

<template>
  <article class="bubble" :class="isUser ? 'user' : 'assistant'">
    <header v-if="!isUser && entry.agent_display_name">
      {{ entry.agent_emoji }} {{ entry.agent_display_name }}
      <span class="time">{{ entry.time_display }}</span>
      <button v-if="canReplay" class="replay" :title="t('audio_play_tooltip')" @click="play">🔊</button>
      <span v-if="entry.tts_note" class="note">{{ entry.tts_note }}</span>
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
.replay { background: none; border: none; padding: 0 4px; cursor: pointer; opacity: 0.7; font: inherit; }
.replay:hover { opacity: 1; }
.note { color: var(--aifred-text-secondary); font-weight: 400; font-size: 0.7rem; align-self: center; }
.content :deep(p) { margin: 0.5em 0; }
.content :deep(pre) { overflow-x: auto; background: var(--aifred-page-bg); padding: 8px; border-radius: 6px; }
.content :deep(details) { margin: 0.3em 0; }
.content :deep(table) { border-collapse: collapse; }
.content :deep(td), .content :deep(th) { border: 1px solid var(--aifred-border); padding: 2px 6px; }
.content :deep(img) { max-width: 100%; }
.content :deep(a) { color: var(--aifred-accent-blue); }
</style>
