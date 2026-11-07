<script setup lang="ts">
import { ref } from "vue";
import { t } from "../i18n";

defineProps<{ running: boolean; queued: number }>();
const emit = defineEmits<{ send: [text: string]; stop: [] }>();
const text = ref("");

function send(): void {
  const value = text.value.trim();
  if (!value) return;
  emit("send", value);
  text.value = "";
}

// Enter sends, Shift+Enter breaks the line (as in the Reflex UI).
function onKey(event: KeyboardEvent): void {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    send();
  }
}
</script>

<template>
  <div class="input">
    <textarea v-model="text" rows="3" :placeholder="t('text_input_placeholder')" @keydown="onKey" />
    <div class="actions">
      <span v-if="queued" class="queued">{{ t("messages_waiting", { count: queued }) }}</span>
      <span class="hint">{{ t("enter_sends_hint") }}</span>
      <button v-if="running" class="stop" @click="emit('stop')">⏹ {{ t("stop_turn") }}</button>
      <button class="send" :disabled="!text.trim()" @click="send">{{ t("send_text") }}</button>
    </div>
  </div>
</template>

<style scoped>
.input { display: flex; flex-direction: column; gap: 6px; }
textarea {
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
  padding: 10px;
  font: inherit;
  background: var(--aifred-input-bg);
  color: var(--aifred-text-primary);
  border: 1px solid var(--aifred-border);
  border-radius: 8px;
}
.actions { display: flex; align-items: center; justify-content: flex-end; gap: 10px; flex-wrap: wrap; }
.hint, .queued { font-size: 0.75rem; color: var(--aifred-text-muted); margin-right: auto; }
.queued { color: var(--aifred-accent-amber); margin-right: 0; }
button { padding: 6px 14px; border: none; border-radius: 6px; cursor: pointer; font: inherit; }
.send { background: var(--aifred-primary); color: var(--aifred-text-on-accent); }
.send:disabled { opacity: 0.5; cursor: default; }
.stop { background: var(--aifred-danger); color: var(--aifred-text-on-accent); }
</style>
