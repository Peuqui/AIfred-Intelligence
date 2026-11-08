<script setup lang="ts">
// When which model loads and what unloads it: faces, persons, image describers, TTS, STT, language models, then the
// practice with the cards and the calibration.
import { t } from "../../i18n";

// Each section: title, a line of size and backend (or none), what it does, what starts it (or none).
const SECTIONS = [
  [t("model_lifecycle_help_base_title"), t("model_lifecycle_help_base_size"), t("model_lifecycle_help_base_body"), t("model_lifecycle_help_base_triggers")],
  [t("model_lifecycle_help_person_title"), "", t("model_lifecycle_help_person_body"), ""],
  [t("model_lifecycle_help_vlm_title"), t("model_lifecycle_help_vlm_size"), t("model_lifecycle_help_vlm_body"), t("model_lifecycle_help_vlm_triggers")],
  [t("model_lifecycle_help_tts_title"), t("model_lifecycle_help_tts_size"), t("model_lifecycle_help_tts_body"), t("model_lifecycle_help_tts_triggers")],
  [t("model_lifecycle_help_stt_title"), "", t("model_lifecycle_help_stt_body"), t("model_lifecycle_help_stt_triggers")],
  [t("model_lifecycle_help_llm_title"), t("model_lifecycle_help_llm_size"), t("model_lifecycle_help_llm_body"), t("model_lifecycle_help_llm_triggers")],
  [t("model_lifecycle_help_vram_title"), "", t("model_lifecycle_help_vram_body"), ""],
  [t("model_lifecycle_help_calibration_title"), "", t("model_lifecycle_help_calibration_body"), t("model_lifecycle_help_calibration_resweep")],
];
</script>

<template>
  <p class="intro">{{ t("model_lifecycle_help_intro") }}</p>
  <section v-for="[title, size, body, triggers] in SECTIONS" :key="title">
    <h3>{{ title }}</h3>
    <p v-if="size" class="size">{{ size }}</p>
    <p>{{ body }}</p>
    <p v-if="triggers" class="triggers">{{ triggers }}</p>
  </section>
</template>

<style scoped>
.intro { margin: 0; color: var(--aifred-text-secondary); }
section { padding-top: 10px; border-top: 1px solid var(--aifred-border); }
h3 { margin: 0 0 4px; font-size: 0.95rem; color: var(--aifred-accent-gold); }
p { margin: 0 0 4px; font-size: 0.9rem; }
.size { font-family: monospace; font-size: 0.8rem; color: var(--aifred-text-secondary); }
.triggers { color: var(--aifred-text-secondary); }
</style>
