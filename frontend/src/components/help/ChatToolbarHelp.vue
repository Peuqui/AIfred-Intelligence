<script setup lang="ts">
// What every button of the chat does: on a phone or tablet nothing shows the tooltips. The one 💡 of the chat; the
// discussion modes' own help is a link from here.
import { ChevronDown, EllipsisVertical, Eye, EyeOff, Lock, LockOpen, Paperclip, Send, SlidersHorizontal, Square, X } from "lucide-vue-next";
import { inject } from "vue";
import DictationBadge from "../DictationBadge.vue";
import { t } from "../../i18n";
import { OPEN_HELP } from "./topics";

const openHelp = inject(OPEN_HELP);
</script>

<template>
  <dl>
    <dt><Paperclip :size="16" /></dt><dd>{{ t("chat_toolbar_help_attach") }}</dd>
    <dt><DictationBadge phase="ready" class="badge" /> <X :size="14" /></dt><dd>{{ t("chat_toolbar_help_mic") }}</dd>
    <dt><Send :size="16" /> <Square :size="14" /></dt><dd>{{ t("chat_toolbar_help_send") }}</dd>
    <dt>A− / A+</dt><dd>{{ t("chat_toolbar_help_zoom") }}</dd>
    <dt><EllipsisVertical :size="16" /></dt><dd>{{ t("chat_toolbar_help_menu") }}</dd>
    <dt><LockOpen :size="14" /> / <Lock :size="14" /></dt><dd>{{ t("chat_toolbar_help_memory") }}</dd>
    <dt><SlidersHorizontal :size="14" /></dt><dd>{{ t("chat_toolbar_help_choices") }}</dd>
    <dt>🎩 🏛️ 👑</dt><dd>{{ t("chat_toolbar_help_agent") }}</dd>
    <dt>{{ t("multi_agent_standard") }} <ChevronDown :size="14" /></dt>
    <dd>
      {{ t("chat_toolbar_help_mode") }}
      <button v-if="openHelp" class="link" @click="openHelp('debate')">{{ t("chat_toolbar_help_mode_link") }}</button>
    </dd>
    <dt><EyeOff :size="14" /> / <Eye :size="14" /></dt><dd>{{ t("chat_toolbar_help_vision") }}</dd>
  </dl>
  <p class="where">{{ t("chat_toolbar_help_where") }}</p>
</template>

<style scoped>
.badge { width: 1.3em; height: 1.3em; vertical-align: -0.3em; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 8px 12px; margin: 0; }
dt { white-space: nowrap; }
dd { margin: 0; }
.link { display: block; margin-top: 4px; background: none; border: none; padding: 0; color: var(--aifred-accent-amber); text-decoration: underline; cursor: pointer; font: inherit; }
.where { margin: 0; color: var(--aifred-text-secondary); }
</style>
