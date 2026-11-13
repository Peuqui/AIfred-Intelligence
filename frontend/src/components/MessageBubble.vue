<script setup lang="ts">
import { RefreshCw } from "lucide-vue-next";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { ChatEntry } from "../api/types";
import { t } from "../i18n";
import { renderMarkdown } from "../lib/markdown";
import { isSpeaking, toggleReading } from "../lib/speech";
import TtsIcon from "./TtsIcon.vue";
import { useSettings } from "../stores/settings";
import ImageLightbox from "./ImageLightbox.vue";

// color: the agent's (agents.json); none for an agent deleted since, whose bubble then has no stripe of colour.
// pinned: the user's question sticks at the top of the chat; stuck: it does so right now. Then it shows a few lines only
// (--pinned-question-lines) and a link below unfolds the rest; as long as it stands in the history, it shows in full.
const props = defineProps<{ entry: ChatEntry; speechOn: boolean; color?: string; pinned?: boolean; stuck?: boolean }>();
const settings = useSettings();
const emit = defineEmits<{ readAgain: [timestamp: string] }>();
const html = computed(() => renderMarkdown(props.entry.content));
const isUser = computed(() => props.entry.role === "user");
const metadata = computed(() => props.entry.metadata ?? { audio_urls: [], browser_speech: [] });
const canReplay = computed(() => (metadata.value.audio_urls?.length ?? 0) + (metadata.value.browser_speech?.length ?? 0) > 0);

// Whether the folded question has more to show is known by measuring: the screen's line breaks are not the text's.
const content = ref<HTMLElement>();
const unfolded = ref(false);
const overflowing = ref(false);
const folded = computed(() => !!props.pinned && !!props.stuck && !unfolded.value);
// Back in the history it shows in full again; the next time it sticks it starts folded.
watch(() => props.stuck, (stuck) => {
  if (!stuck) unfolded.value = false;
});

// Folding must not move anything below the question (the box keeps its scroll position by itself and would carry the
// question back over the line where it sticks, and it would unfold again): the height the fold takes away stays as
// space under the folded question (margin), and scrolls away beneath it. The full height is that of the question where
// it stands in the history.
const root = ref<HTMLElement>();
const fullHeight = ref(0);
const spaceBelow = ref(0);

function measure(): void {
  const element = content.value;
  const bubbleElement = root.value;
  if (!element || !bubbleElement || !props.pinned) return;
  if (!props.stuck) fullHeight.value = bubbleElement.offsetHeight;
  spaceBelow.value = folded.value ? Math.max(0, fullHeight.value - bubbleElement.offsetHeight) : 0;
  // Unfolded nothing is cut off; the link stays, to fold again.
  if (folded.value) overflowing.value = element.scrollHeight > element.clientHeight + 1;
}
const resizes = new ResizeObserver(measure);
onMounted(() => {
  measure();
  if (content.value) resizes.observe(content.value);
  if (root.value) resizes.observe(root.value);
});
onBeforeUnmount(() => resizes.disconnect());
watch(() => [props.entry.content, props.pinned, props.stuck, folded.value], () => void nextTick(measure));

function play(): void {
  toggleReading(metadata.value.audio_urls ?? [], metadata.value.browser_speech ?? []);
}

// A click on one of the bubble's own pictures (an upload, a camera picture, a plot) shows it large, the others of
// the bubble one key away; a picture behind a link keeps its link.
const pictures = ref<string[]>([]);
const shownAt = ref<number | null>(null);

function enlarge(event: MouseEvent): void {
  const picture = event.target instanceof HTMLImageElement ? event.target : null;
  if (!picture || picture.closest("a") || !picture.src.includes("/_upload/")) return;
  const bubble = event.currentTarget as HTMLElement;
  pictures.value = [...bubble.querySelectorAll("img")].map((image) => image.src).filter((src) => src.includes("/_upload/"));
  shownAt.value = pictures.value.indexOf(picture.src);
}
</script>

<template>
  <article
    ref="root"
    class="bubble"
    :class="isUser ? 'user' : 'assistant'"
    :style="[!isUser && color ? { '--agent-color': color } : {}, spaceBelow ? { marginBottom: `${spaceBelow}px` } : {}]"
  >
    <!-- The user's name over their bubble and their sign beside it, as on the Reflex page. -->
    <header v-if="isUser" class="user-head">
      {{ settings.current?.user_name || "User" }} <span class="sign">{{ settings.current?.user_emoji }}</span>
    </header>
    <header v-if="!isUser && entry.agent_display_name">
      {{ entry.agent_emoji }} {{ entry.agent_display_name }}
      <span class="time">{{ entry.time_display }}</span>
      <button v-if="canReplay" class="replay" :title="t('audio_play_tooltip')" @click="play"><TtsIcon :playing="isSpeaking(metadata.audio_urls ?? [], metadata.browser_speech ?? [])" /></button>
      <button
        v-if="speechOn && entry.timestamp"
        class="replay"
        :title="canReplay ? t('audio_regenerate_tooltip') : t('audio_generate_tooltip')"
        @click="emit('readAgain', entry.timestamp)"
      >
        <RefreshCw :size="16" />
      </button>
      <span v-if="entry.tts_note" class="note">{{ entry.tts_note }}</span>
    </header>
    <!-- eslint-disable-next-line vue/no-v-html -- sanitized by renderMarkdown (DOMPurify) -->
    <div ref="content" class="content" :class="{ folded }" @click="enlarge" v-html="html" />
    <button v-if="pinned && stuck && (overflowing || unfolded)" type="button" class="more" @click="unfolded = !unfolded">
      {{ unfolded ? t("question_less") : t("question_more") }}
    </button>
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
/* As on the Reflex page: an answer as wide as the chat (tables, code), the user's question at most 70 % on the right. */
.bubble { box-sizing: border-box; padding: 10px 14px; border-radius: 10px; line-height: 1.45; overflow-wrap: anywhere; }
.user { align-self: flex-end; max-width: 70%; background: var(--aifred-input-bg); }
/* Every agent's bubble neutral as on the Reflex page; its colour a stripe at the left and its name (--agent-color, from
   agents.json). An agent deleted since has none: the stripe is the rim's colour. */
.assistant {
  width: 100%;
  background: var(--aifred-card-bg);
  border: 1px solid var(--aifred-agent-bubble-border);
  border-left: 1px solid var(--agent-color, var(--aifred-agent-bubble-border));
}
.assistant .content { color: var(--aifred-answer-text); }
.assistant header { color: var(--agent-color, var(--aifred-primary)); }
.user-head { justify-content: flex-end; color: var(--aifred-user-name); }
.sign { font-weight: 400; }
header { font-size: var(--text-small); font-weight: 600; color: var(--aifred-primary); margin-bottom: 4px; display: flex; gap: 8px; }
.time { color: var(--aifred-text-muted); font-weight: 400; }
.replay { background: none; border: none; padding: 0 4px; cursor: pointer; opacity: 0.7; font: inherit; }
.replay:hover { opacity: 1; }
/* The speaker in the header is the one to hit with a thumb: larger than the text line, at full strength. */
.replay:has(.tts-icon) { opacity: 1; }
.replay :deep(.tts-icon) { height: 2.2em; width: 2.2em; vertical-align: middle; }
.note { color: var(--aifred-text-secondary); font-weight: 400; font-size: var(--text-small); align-self: center; }
.content.folded { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: var(--pinned-question-lines); overflow: hidden; }
.more { background: none; border: none; padding: 0; margin-top: 2px; font: inherit; font-size: var(--text-small); color: var(--aifred-text-muted); text-decoration: underline; cursor: pointer; }
.content :deep(p) { margin: 0.5em 0; }
.content :deep(em.metrics) { font-size: var(--text-small); color: var(--aifred-text-secondary); }
.content :deep(pre) { overflow-x: auto; background: var(--aifred-page-bg); padding: 8px; border-radius: 6px; }
/* Every collapsible (thinking, sources, …) a box of its own inside the answer, as on the Reflex page: lighter than the
   bubble, at most so high (config.py, theme.css) with a scrollbar of its own; its title line sticks at its top. A
   sandbox page keeps its own height and no frame. */
.content :deep(details) {
  background: var(--aifred-collapsible-bg);
  border: 1px solid var(--aifred-border-light);
  border-radius: 6px;
  padding: 0 0.75em;
  margin: 0.4em 0 1em;
  max-height: var(--thinking-max-height-desktop);
  overflow-y: auto;
}
/* The title line carries input_bg in the answer's HTML (formatting.py); only !important brings it to the box's colour. */
/* Closed, the box is its title line, the same space above and below; open, its content gets room at the bottom. */
.content :deep(details[open]) { padding-bottom: 0.5em; }
.content :deep(details > summary) { padding: 3px 0 !important; color: var(--aifred-text-secondary); background: var(--aifred-collapsible-bg) !important; }
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
