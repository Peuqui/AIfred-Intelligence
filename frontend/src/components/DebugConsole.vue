<script setup lang="ts">
import { nextTick, ref, watch } from "vue";
import { t } from "../i18n";

const props = defineProps<{ lines: string[] }>();
const open = ref(false);
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
    <pre v-if="open" ref="list">{{ lines.join("\n") }}</pre>
  </section>
</template>

<style scoped>
.toggle { background: none; border: none; color: var(--aifred-text-secondary); cursor: pointer; font: inherit; padding: 0; }
pre {
  max-height: var(--debug-max-height, 40vh);
  overflow-y: auto;
  margin: 6px 0 0;
  padding: 8px;
  background: var(--aifred-page-bg);
  border: 1px solid var(--aifred-border);
  border-radius: 6px;
  font-size: 0.75rem;
  white-space: pre-wrap;
}
</style>
