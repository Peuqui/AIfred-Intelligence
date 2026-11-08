<script setup lang="ts">
import { computed, ref } from "vue";
import type { ChatEntry } from "../api/types";
import { t } from "../i18n";
import { renderMarkdown } from "../lib/markdown";
import { replay } from "../lib/speech";
import { agentHue } from "../lib/agentTint";
import { useSettings } from "../stores/settings";
import ImageLightbox from "./ImageLightbox.vue";

const props = defineProps<{ entry: ChatEntry; speechOn: boolean }>();
const settings = useSettings();
const emit = defineEmits<{ readAgain: [timestamp: string] }>();
const html = computed(() => renderMarkdown(props.entry.content));
const isUser = computed(() => props.entry.role === "user");
const metadata = computed(() => props.entry.metadata ?? { audio_urls: [], browser_speech: [] });
const canReplay = computed(() => (metadata.value.audio_urls?.length ?? 0) + (metadata.value.browser_speech?.length ?? 0) > 0);

function play(): void {
  replay(metadata.value.audio_urls ?? [], metadata.value.browser_speech ?? []);
}

// A click on one of the bubble's own pictures (an upload, a camera picture, a plot) shows it large, the others of
// the bubble one key away; a picture behind a link keeps its link.
const pictures = ref<string[]>([]);
const shownAt = ref<number | null>(null);

function enlarge(event: MouseEvent): void {
  const picture = event.target instanceof HTMLImageElement ? event.target : null;
  if (!picture || picture.closest("a") || !picture.src.includes("/_upload/")) return;
  const content = event.currentTarget as HTMLElement;
  pictures.value = [...content.querySelectorAll("img")].map((image) => image.src).filter((src) => src.includes("/_upload/"));
  shownAt.value = pictures.value.indexOf(picture.src);
}
</script>

<template>
  <article
    class="bubble"
    :class="isUser ? 'user' : ['assistant', `agent-${entry.agent}`]"
    :style="isUser ? undefined : { '--agent-hue': agentHue(entry.agent || '') }"
  >
    <!-- The user's name over their bubble and their sign beside it, as on the Reflex page. -->
    <header v-if="isUser" class="user-head">
      {{ settings.current?.user_name || "User" }} <span class="sign">{{ settings.current?.user_emoji }}</span>
    </header>
    <header v-if="!isUser && entry.agent_display_name">
      {{ entry.agent_emoji }} {{ entry.agent_display_name }}
      <span class="time">{{ entry.time_display }}</span>
      <button v-if="canReplay" class="replay" :title="t('audio_play_tooltip')" @click="play">🔊</button>
      <button
        v-if="speechOn && entry.timestamp"
        class="replay"
        :title="canReplay ? t('audio_regenerate_tooltip') : t('audio_generate_tooltip')"
        @click="emit('readAgain', entry.timestamp)"
      >
        🔄
      </button>
      <span v-if="entry.tts_note" class="note">{{ entry.tts_note }}</span>
    </header>
    <!-- eslint-disable-next-line vue/no-v-html -- sanitized by renderMarkdown (DOMPurify) -->
    <div class="content" @click="enlarge" v-html="html" />
    <ImageLightbox
      v-if="shownAt !== null"
      :count="pictures.length"
      :start="shownAt"
      :pictures="(at: number) => [pictures[at] ?? '']"
      @close="shownAt = null"
    />
  </article>
</template>

<style scoped>
.bubble { max-width: min(860px, 92%); padding: 10px 14px; border-radius: 10px; line-height: 1.45; overflow-wrap: anywhere; }
.user { align-self: flex-end; background: var(--aifred-input-bg); }
/* Each agent a quiet tint of its own over card_bg (strength in theme.py). */
.assistant {
  --tint: hsl(var(--agent-hue) 55% 55%);
  align-self: flex-start;
  background: color-mix(in srgb, var(--tint) var(--aifred-agent-tint-mix), var(--aifred-card-bg));
  border: 1px solid color-mix(in srgb, var(--tint) var(--aifred-agent-tint-border-mix), var(--aifred-border));
}
/* Sokrates bronze, Salomo gold, as on the Reflex page (theme.py). */
.agent-sokrates { background: var(--aifred-sokrates-bubble-bg); border-color: var(--aifred-sokrates-bubble-border); }
.agent-salomo { background: var(--aifred-salomo-bubble-bg); border-color: var(--aifred-salomo-bubble-border); }
.agent-salomo header { color: var(--aifred-accent-warning); }
.user-head { justify-content: flex-end; color: var(--aifred-user-name); }
.sign { font-weight: 400; }
header { font-size: 0.8rem; font-weight: 600; color: var(--aifred-primary); margin-bottom: 4px; display: flex; gap: 8px; }
.time { color: var(--aifred-text-muted); font-weight: 400; }
.replay { background: none; border: none; padding: 0 4px; cursor: pointer; opacity: 0.7; font: inherit; }
.replay:hover { opacity: 1; }
.note { color: var(--aifred-text-secondary); font-weight: 400; font-size: 0.7rem; align-self: center; }
.content :deep(p) { margin: 0.5em 0; }
.content :deep(pre) { overflow-x: auto; background: var(--aifred-page-bg); padding: 8px; border-radius: 6px; }
/* Every collapsible (thinking, sources, …) a box of its own inside the answer, as on the Reflex page: lighter than the
   bubble, at most so high (config.py, theme.css) with a scrollbar of its own; its title line sticks at its top. A
   sandbox page keeps its own height and no frame. */
.content :deep(details) {
  background: var(--aifred-collapsible-bg);
  border: 1px solid var(--aifred-border-light);
  border-radius: 6px;
  padding: 0 0.75em 0.5em;
  margin: 0.4em 0 1em;
  max-height: var(--thinking-max-height-desktop);
  overflow-y: auto;
}
/* The title line carries input_bg in the answer's HTML (formatting.py); only !important brings it to the box's colour. */
.content :deep(details > summary) { padding: 6px 0 !important; color: var(--aifred-text-secondary); background: var(--aifred-collapsible-bg) !important; }
.content :deep(details[data-sandbox]) { max-height: var(--sandbox-max-height); overflow: hidden; background: transparent; border: none; padding: 0; }
.content :deep(.thinking-compact) { color: var(--aifred-text-soft); }
.content :deep(details a) { color: var(--aifred-accent-cyan); }
@media (max-width: 760px) {
  .content :deep(details:not([data-sandbox])) { max-height: var(--thinking-max-height-mobile); }
}
.content :deep(table) { border-collapse: collapse; }
.content :deep(td), .content :deep(th) { border: 1px solid var(--aifred-border); padding: 2px 6px; }
.content :deep(img) { max-width: 100%; }
.content :deep(img[src*="/_upload/"]) { cursor: zoom-in; }
.content :deep(a) { color: var(--aifred-accent-blue); }
</style>
