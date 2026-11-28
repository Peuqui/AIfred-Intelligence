<script setup lang="ts">
// The line between the last bubble read and the first unread one (lib/unread.ts). It is the page's divider (App.vue
// .divider, the line that fades out to both sides) in green (Peuqui 21.11.2026), with no word beside it (Peuqui
// 28.11.2026); "new" is only its label for screen readers.
// It takes no room: it stands in the gap between two bubbles (--bubble-gap, ChatView.vue). Its height and margins
// together take back the gap its parent adds for it, so it can move with the mark and nothing around it moves.
import { t } from "../i18n";
</script>

<template>
  <div class="read-mark" role="separator" :aria-label="t('unread_line')" data-read-mark>
    <div class="divider line" />
  </div>
</template>

<style scoped>
.read-mark {
  position: relative; z-index: 1; /* over the bubbles' edges its glow reaches into, under the sticking question (z-index 3) */
  height: 0; margin: calc(var(--bubble-gap) / -2) 0;
  display: flex; align-items: center;
}
/* A little thicker than the page's divider (1.5px): close to the bubbles it looked thin (Peuqui 28.11.2026). */
.line { flex: 1; margin: 0; height: 2.5px; --divider-color: var(--aifred-accent-success); --divider-glow: var(--aifred-accent-success); }
</style>
