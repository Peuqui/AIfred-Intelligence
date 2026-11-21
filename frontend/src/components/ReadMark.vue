<script setup lang="ts">
// The line under the last bubble read: everything below it is new (lib/unread.ts). It is the page's divider (App.vue
// .divider, the line that fades out to both sides) in green (Peuqui 21.11.2026), with "new" beside it.
import { t } from "../i18n";
</script>

<template>
  <div class="read-mark" role="separator" data-read-mark>
    <div class="divider line" />
    <span>{{ t("unread_line") }}</span>
  </div>
</template>

<style scoped>
.read-mark { display: flex; align-items: center; gap: 8px; color: var(--aifred-accent-success); font-size: var(--text-small); font-weight: 600; }
.line { flex: 1; margin: 0; --divider-color: var(--aifred-accent-success); --divider-glow: var(--aifred-accent-success); }
</style>
