<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { t } from "../i18n";
import { consoleHeight } from "../lib/devicePrefs";
import { isAtEnd } from "../lib/followScroll";
import ResizeGrip from "./ResizeGrip.vue";

const props = defineProps<{ lines: string[] }>();
const open = ref(true); // shown, as it was under Reflex
const list = ref<HTMLElement | null>(null);

// New lines scroll into view while the reader stands at the console's end (followScroll.ts); scrolled up to read, it
// stays. Measured before the lines reach the page (the watcher runs first), scrolled after.
watch(
  () => props.lines.length,
  async () => {
    const box = list.value;
    const atEnd = !box || isAtEnd(box);
    await nextTick();
    if (list.value && atEnd) list.value.scrollTop = list.value.scrollHeight;
  },
);
// Dragged by its grip, the user's height counts: the default cap (max-height) goes.
const heightStyle = computed(() => (consoleHeight.value ? { height: `${consoleHeight.value}px`, maxHeight: "none" } : undefined));
// Opened (and when first shown), it starts at its end: it begins by following.
watch(
  open,
  async (isOpen) => {
    await nextTick();
    if (isOpen && list.value) list.value.scrollTop = list.value.scrollHeight;
  },
  { immediate: true },
);
</script>

<template>
  <section class="debug">
    <button class="toggle" @click="open = !open">{{ open ? "▾" : "▸" }} {{ t("debug_console") }} ({{ lines.length }})</button>
    <div v-if="open" ref="list" class="lines" :style="heightStyle">
      <div class="text">
        <div v-for="(line, index) in lines" :key="index" :class="{ error: line.includes('✗') || line.includes('❌') }">{{ line }}</div>
      </div>
    </div>
    <ResizeGrip v-if="open" :target="list" :min-height="60" @resize="(height) => (consoleHeight = height)" @reset="consoleHeight = null" />
  </section>
</template>

<style scoped>
.toggle { background: none; border: none; color: var(--aifred-debug-accent); cursor: pointer; font: inherit; padding: 0; }
/* The Matrix terminal of the Reflex console: neon green, errors red. */
.lines {
  max-height: calc(var(--debug-max-height, 40vh) / var(--ui-zoom));
  overflow-y: auto;
  margin: 6px 0 0;
  padding: 8px;
  background: var(--aifred-page-bg);
  border: 2px solid var(--aifred-debug-border);
  border-radius: 8px;
  color: var(--aifred-debug-text);
  font-family: monospace;
  font-size: var(--text-small); /* monospace looks larger than its number: the small step, not 11 px as on the Reflex page */
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
/* The console's own size; the interface's size is taken out again. */
.text { zoom: calc(var(--console-zoom) / var(--ui-zoom)); }
.error { color: var(--aifred-danger); }
/* A wrapped line goes on under its text, not under the time: "14:17:48 | " is 11 characters of the monospace font. */
.text > div { padding-left: 11ch; text-indent: -11ch; }
</style>
