<script setup lang="ts">
// The line between the last bubble read and the first unread one (lib/unread.ts). It is the page's divider (App.vue
// .divider, the line that fades out to both sides) in green (Peuqui 21.11.2026), with "new" beside it.
// It takes no room: it stands in the gap between two bubbles (--bubble-gap, ChatView.vue). Its height and margins
// together take back the gap its parent adds for it, so it can move with the mark and nothing around it moves.
import { t } from "../i18n";
</script>

<template>
  <div class="read-mark" role="separator" data-read-mark>
    <div class="divider line" />
    <span>{{ t("unread_line") }}</span>
  </div>
</template>

<style scoped>
.read-mark {
  position: relative; z-index: 1; /* over the bubbles' edges it reaches into, under the sticking question (z-index 3) */
  height: 0; margin: calc(var(--bubble-gap) / -2) 0;
  display: flex; align-items: center; gap: 8px;
  color: var(--aifred-accent-success); font-size: var(--text-small); font-weight: 600;
}
.read-mark span { padding: 0 6px; border-radius: 999px; background: var(--aifred-chat-panel-bg); line-height: 1.2; }
.line { flex: 1; margin: 0; --divider-color: var(--aifred-accent-success); --divider-glow: var(--aifred-accent-success); }
</style>
