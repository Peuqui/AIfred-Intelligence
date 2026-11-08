<script setup lang="ts">
// Vigilantia's levels: arming, what each camera detects, the live view; the tabs beside them; a link to the lifecycle.
import { inject } from "vue";
import { t } from "../../i18n";
import { OPEN_HELP } from "./topics";

const openHelp = inject(OPEN_HELP);

// Each level: its heading and its rows (icon, label, text).
const LEVELS = [
  {
    title: t("vigilantia_help_section_master"),
    rows: [
      ["👁️", t("vigilantia_help_eye_open_label"), t("vigilantia_help_eye_open_body")],
      ["🙈", t("vigilantia_help_eye_closed_label"), t("vigilantia_help_eye_closed_body")],
    ],
  },
  {
    title: t("vigilantia_help_section_sources"),
    rows: [
      ["📷", t("vigilantia_help_sources_label"), t("vigilantia_help_sources_body")],
      ["🙂", t("vigilantia_help_face_label"), t("vigilantia_help_face_body")],
      ["〰️", t("vigilantia_help_motion_label"), t("vigilantia_help_motion_body")],
      ["🧍", t("vigilantia_help_person_label"), t("vigilantia_help_person_body")],
      ["📡", t("vigilantia_help_edgeai_label"), t("vigilantia_help_edgeai_body")],
    ],
  },
  { title: t("vigilantia_help_section_preview"), rows: [["📹", t("vigilantia_help_preview_label"), t("vigilantia_help_preview_body")]] },
  {
    title: t("vigilantia_help_section_related"),
    rows: [
      ["👤", t("vigilantia_help_personarium_label"), t("vigilantia_help_personarium_body")],
      ["🗂️", t("vigilantia_help_casus_label"), t("vigilantia_help_casus_body")],
      ["📸", t("vigilantia_help_multipose_label"), t("vigilantia_help_multipose_body")],
    ],
  },
];
</script>

<template>
  <p class="intro">{{ t("vigilantia_help_intro") }}</p>
  <section v-for="level in LEVELS" :key="level.title">
    <h3>{{ level.title }}</h3>
    <div v-for="[icon, label, body] in level.rows" :key="label" class="row">
      <span class="icon">{{ icon }}</span>
      <div><strong>{{ label }}</strong><p>{{ body }}</p></div>
    </div>
  </section>
  <button v-if="openHelp" class="link" @click="openHelp('lifecycle')">{{ t("vigilantia_help_lifecycle_link") }}</button>
</template>

<style scoped>
.intro { margin: 0; color: var(--aifred-text-secondary); }
section { padding-top: 10px; border-top: 1px solid var(--aifred-border); }
h3 { margin: 0 0 6px; font-size: 0.95rem; color: var(--aifred-accent-gold); }
.row { display: grid; grid-template-columns: 28px 1fr; gap: 8px; padding: 4px 0; }
.icon { font-size: 1.1rem; }
strong { font-size: 0.9rem; }
p { margin: 2px 0 0; font-size: 0.88rem; color: var(--aifred-text-secondary); }
.link { align-self: flex-start; background: none; border: none; padding: 0; color: var(--aifred-accent-amber); text-decoration: underline; cursor: pointer; font: inherit; }
</style>
