<script setup lang="ts">
// What every button of the bar above the chat does: on a phone or tablet nothing shows the tooltips.
import { t } from "../../i18n";
</script>

<template>
  <dl>
    <dt>A− / A+</dt><dd>{{ t("chat_toolbar_help_zoom") }}</dd>
    <dt>🎛️</dt><dd>{{ t("chat_toolbar_help_choices") }}</dd>
    <dt>🔄</dt><dd>{{ t("chat_toolbar_help_regenerate") }}</dd>
    <dt>🔓 / 🔒</dt><dd>{{ t("chat_toolbar_help_memory") }}</dd>
    <dt>🗑️</dt><dd>{{ t("chat_toolbar_help_clear") }}</dd>
    <dt>📌</dt><dd>{{ t("chat_toolbar_help_remember") }}</dd>
    <dt>🔗</dt><dd>{{ t("chat_toolbar_help_share") }}</dd>
    <dt>💾</dt><dd>{{ t("chat_toolbar_help_download") }}</dd>
    <dt>📂</dt><dd>{{ t("chat_toolbar_help_outputs") }}</dd>
    <dt>🎩 AIfred ▾</dt><dd>{{ t("chat_toolbar_help_agent") }}</dd>
    <dt>▾ 💡</dt><dd>{{ t("chat_toolbar_help_mode") }}</dd>
    <dt>🙈 / 👁️</dt><dd>{{ t("chat_toolbar_help_vision") }}</dd>
  </dl>
  <p class="where">{{ t("chat_toolbar_help_where") }}</p>
</template>

<style scoped>
dl { display: grid; grid-template-columns: max-content 1fr; gap: 8px 12px; margin: 0; }
dt { white-space: nowrap; }
dd { margin: 0; }
.where { margin: 0; color: var(--aifred-text-secondary); }
</style>
