<script setup lang="ts">
import { nextTick, ref, watch } from "vue";
import { t } from "../i18n";

const props = defineProps<{ lines: string[] }>();
const open = ref(true); // shown, as it was under Reflex
const list = ref<HTMLElement | null>(null);

// New lines scroll into view while the console is open.
watch(
  () => props.lines.length,
  async () => {
    await nextTick();
    if (list.value) list.value.scrollTop = list.value.scrollHeight;
  },
);
</script>

<template>
  <section class="debug">
    <button class="toggle" @click="open = !open">{{ open ? "▾" : "▸" }} {{ t("debug_console") }} ({{ lines.length }})</button>
    <div v-if="open" ref="list" class="lines">
      <div class="text">
        <div v-for="(line, index) in lines" :key="index" :class="{ error: line.includes('✗') || line.includes('❌') }">{{ line }}</div>
      </div>
    </div>
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
  font-size: 11px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
/* The console's own size; the interface's size is taken out again. */
.text { zoom: calc(var(--console-zoom) / var(--ui-zoom)); }
.error { color: var(--aifred-danger); }
</style>
