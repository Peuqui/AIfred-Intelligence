<script setup lang="ts">
// What the turn does while no text comes (lib/turnProgress.ts): a pulsing line, with a bar while pages are read.
import type { ProgressLine } from "../lib/turnProgress";

defineProps<{ line: ProgressLine }>();
</script>

<template>
  <p class="progress">
    <span class="icon">{{ line.icon }}</span>
    <span class="text">{{ line.text }}</span>
    <template v-if="line.count">
      <span class="bar"><span :style="{ width: `${Math.round(line.done * 100)}%` }" /></span>
      <span class="count">{{ line.count }}</span>
    </template>
    <span v-if="line.failed" class="failed">({{ line.failed }})</span>
  </p>
</template>

<style scoped>
.progress { display: flex; align-items: center; gap: 8px; margin: 4px 0 0; font-size: 0.85rem; }
.icon { animation: pulse 2s ease-in-out infinite; }
.text { font-weight: 600; color: var(--aifred-primary); animation: pulse 2s ease-in-out infinite; }
.bar { width: 120px; height: 12px; border: 1px solid var(--aifred-border); border-radius: 4px; overflow: hidden; }
.bar span { display: block; height: 100%; background: var(--aifred-primary); transition: width 0.3s ease; }
.count { font-weight: 600; color: var(--aifred-primary); }
.failed { color: var(--aifred-accent-amber); }
@keyframes pulse { 50% { opacity: 0.5; } }
</style>
