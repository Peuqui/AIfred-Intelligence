<script setup lang="ts">
// The line under the last bubble read: everything below it is new (lib/unread.ts). Red as the stop button.
import { t } from "../i18n";
</script>

<template>
  <div class="read-mark" role="separator" data-read-mark><span>{{ t("unread_line") }}</span></div>
</template>

<style scoped>
.read-mark { display: flex; align-items: center; gap: 8px; color: var(--aifred-radix-red-text); font-size: var(--text-small); font-weight: 600; }
.read-mark::before { content: ""; flex: 1; border-top: 2px solid currentColor; order: 1; }
.read-mark span { order: 2; }
</style>
