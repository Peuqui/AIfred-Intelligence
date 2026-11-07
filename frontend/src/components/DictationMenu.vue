<script setup lang="ts">
// The dictation's settings beside the microphone: who recognizes it (the STT-Suite on this machine or
// the browser's own recognition, which sends the speech out of the house in Chrome and Edge) and
// whether a dictation goes into the input field first. Both live in settings.json (lib/dictation.py).
import { ref } from "vue";
import type { DictationSettings } from "../api/types";
import { t } from "../i18n";

defineProps<{ settings: DictationSettings }>();
const emit = defineEmits<{ change: [changes: Partial<DictationSettings>] }>();
const open = ref(false);
</script>

<template>
  <span class="menu">
    <button class="toggle" :title="t('dictation_settings')" :aria-expanded="open" @click="open = !open">▾</button>
    <div v-if="open" class="panel" role="dialog" :aria-label="t('dictation_settings')">
      <p class="heading">{{ t("dictation_engine") }}</p>
      <label>
        <input
          type="radio"
          name="dictation-engine"
          :checked="settings.engine === 'stt_suite'"
          @change="emit('change', { engine: 'stt_suite' })"
        />
        {{ t("dictation_engine_stt_suite") }}
      </label>
      <label>
        <input
          type="radio"
          name="dictation-engine"
          :checked="settings.engine === 'browser'"
          @change="emit('change', { engine: 'browser' })"
        />
        {{ t("dictation_engine_browser") }}
      </label>
      <p class="hint">{{ t("dictation_engine_browser_hint") }}</p>
      <label class="edit">
        <input
          type="checkbox"
          :checked="settings.show_transcription"
          @change="emit('change', { show_transcription: ($event.target as HTMLInputElement).checked })"
        />
        {{ t("dictation_edit_first") }}
      </label>
    </div>
  </span>
</template>

<style scoped>
.menu { position: relative; display: inline-flex; }
.toggle { background: none; border: none; padding: 0 2px; cursor: pointer; color: var(--aifred-text-muted); font: inherit; }
.panel {
  position: absolute;
  bottom: calc(100% + 8px);
  left: 0;
  z-index: 10;
  width: 280px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  background: var(--aifred-card-bg);
  border: 1px solid var(--aifred-border-light);
  border-radius: 8px;
  font-size: 0.85rem;
}
.heading { margin: 0; font-weight: 600; color: var(--aifred-text-secondary); }
.hint { margin: 0 0 4px 22px; font-size: 0.75rem; color: var(--aifred-text-muted); }
label { display: flex; align-items: center; gap: 6px; cursor: pointer; }
.edit { border-top: 1px solid var(--aifred-border); padding-top: 8px; }
</style>
