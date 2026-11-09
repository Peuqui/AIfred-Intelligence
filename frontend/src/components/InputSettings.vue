<script setup lang="ts">
// The input's settings, a section of the settings page: who recognizes a dictation (the STT-Suite on this machine
// or the browser's own recognition, which sends the speech out of the house in Chrome and Edge) and whether it goes
// into the field first (both in settings.json, lib/dictation.py), and whether Enter sends (this device only).
import type { Settings, SettingsUpdate } from "../api/types";
import { t } from "../i18n";
import { enterSends } from "../lib/devicePrefs";

defineProps<{ settings: Settings }>();
const emit = defineEmits<{ change: [changes: SettingsUpdate] }>();
</script>

<template>
  <section class="input-settings">
    <h3>{{ t("input_settings") }}</h3>
    <p class="heading">{{ t("dictation_engine") }}</p>
    <label>
      <input
        type="radio"
        name="dictation-engine"
        :checked="settings.dictation_engine === 'stt_suite'"
        @change="emit('change', { dictation_engine: 'stt_suite' })"
      />
      {{ t("dictation_engine_stt_suite") }}
    </label>
    <label>
      <input
        type="radio"
        name="dictation-engine"
        :checked="settings.dictation_engine === 'browser'"
        @change="emit('change', { dictation_engine: 'browser' })"
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
    <label class="edit" :title="t('enter_sends_hint')">
      <input v-model="enterSends" type="checkbox" />
      {{ t("enter_sends") }}
    </label>
    <p class="hint">{{ t("enter_sends_hint") }} · {{ t("input_settings_device_only") }}</p>
  </section>
</template>

<style scoped>
.input-settings { display: flex; flex-direction: column; gap: 6px; }
h3 { margin: 0 0 4px; font-size: 1rem; }
.heading { margin: 0; font-weight: 600; color: var(--aifred-text-secondary); }
.hint { margin: 0 0 4px 22px; font-size: var(--text-small); color: var(--aifred-text-muted); }
label { display: flex; align-items: center; gap: 6px; cursor: pointer; }
.edit { border-top: 1px solid var(--aifred-border); padding-top: 8px; }
input { accent-color: var(--aifred-primary); }
</style>
