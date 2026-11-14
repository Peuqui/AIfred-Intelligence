<script setup lang="ts">
// Reasoning (a prompt) against thinking (the model's own chain of thought), and what the two give together.
import { Brain, MessageCircleMore } from "lucide-vue-next";
import { t } from "../../i18n";
</script>

<template>
  <section class="reasoning">
    <h3><MessageCircleMore :size="14" /> {{ t("reasoning_thinking_help_reasoning_title") }}</h3>
    <p>{{ t("reasoning_thinking_help_reasoning_desc") }}</p>
    <p class="effect">{{ t("reasoning_thinking_help_reasoning_effect") }}</p>
  </section>
  <section class="thinking">
    <h3><Brain :size="14" /> {{ t("reasoning_thinking_help_thinking_title") }}</h3>
    <p>{{ t("reasoning_thinking_help_thinking_desc") }}</p>
    <p>{{ t("reasoning_thinking_help_thinking_levels") }}</p>
    <p class="effect">{{ t("reasoning_thinking_help_thinking_effect") }}</p>
  </section>
  <section>
    <h3>{{ t("reasoning_thinking_help_combinations_title") }}</h3>
    <dl>
      <dt><MessageCircleMore :size="14" />+<Brain :size="14" /></dt><dd>{{ t("reasoning_thinking_help_both_on") }}</dd>
      <dt><MessageCircleMore :size="14" /></dt><dd>{{ t("reasoning_thinking_help_reasoning_only") }}</dd>
      <dt><Brain :size="14" /></dt><dd>{{ t("reasoning_thinking_help_thinking_only") }}</dd>
      <dt>—</dt><dd>{{ t("reasoning_thinking_help_both_off") }}</dd>
    </dl>
  </section>
  <p class="where">{{ t("reasoning_thinking_help_where") }}</p>
</template>

<style scoped>
section { padding: 10px 12px; border: 1px solid var(--aifred-border); border-radius: 8px; }
h3 { margin: 0 0 6px; font-size: 0.95rem; color: var(--aifred-accent-gold); }
.reasoning h3 { color: var(--aifred-accent-amber); --icon-own: currentColor; }
.thinking h3 { color: var(--aifred-accent-blue); --icon-own: currentColor; }
p { margin: 0 0 6px; }
.effect { color: var(--aifred-text-secondary); font-style: italic; }
dl { display: grid; grid-template-columns: 60px 1fr; gap: 6px 8px; margin: 0; }
dt { font-size: 1rem; }
dd { margin: 0; }
.where { margin: 0; color: var(--aifred-text-secondary); }
</style>
