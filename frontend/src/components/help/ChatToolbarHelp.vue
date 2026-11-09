<script setup lang="ts">
// What every button of the chat does: on a phone or tablet nothing shows the tooltips. The one 💡 of the chat; the
// discussion modes' own help is a link from here.
import { inject } from "vue";
import { t } from "../../i18n";
import { OPEN_HELP } from "./topics";

const openHelp = inject(OPEN_HELP);
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
    <dt>🎩 🏛️ 👑</dt><dd>{{ t("chat_toolbar_help_agent") }}</dd>
    <dt>{{ t("multi_agent_standard") }} ▾</dt>
    <dd>
      {{ t("chat_toolbar_help_mode") }}
      <button v-if="openHelp" class="link" @click="openHelp('debate')">{{ t("chat_toolbar_help_mode_link") }}</button>
    </dd>
    <dt>🙈 / 👁️</dt><dd>{{ t("chat_toolbar_help_vision") }}</dd>
  </dl>
  <p class="where">{{ t("chat_toolbar_help_where") }}</p>
</template>

<style scoped>
dl { display: grid; grid-template-columns: max-content 1fr; gap: 8px 12px; margin: 0; }
dt { white-space: nowrap; }
dd { margin: 0; }
.link { display: block; margin-top: 4px; background: none; border: none; padding: 0; color: var(--aifred-accent-amber); text-decoration: underline; cursor: pointer; font: inherit; }
.where { margin: 0; color: var(--aifred-text-secondary); }
</style>
