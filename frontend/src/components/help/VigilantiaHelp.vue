<script setup lang="ts">
// Vigilantia's levels: arming, what each camera detects, the live view; the tabs beside them; a link to the lifecycle.
import {
  Activity, ArrowRight, Camera, Eye, EyeOff, PersonStanding, Radar, ScanFace, ScrollText, User, Video,
} from "lucide-vue-next";
import { inject } from "vue";
import { t } from "../../i18n";
import { OPEN_HELP } from "./topics";

const openHelp = inject(OPEN_HELP);

// Each level: its heading and its rows (icon, label, text); an icon is the one of the control the row explains.
const LEVELS = [
  {
    title: t("vigilantia_help_section_master"),
    rows: [
      { icon: Eye, label: t("vigilantia_help_eye_open_label"), body: t("vigilantia_help_eye_open_body") },
      { icon: EyeOff, label: t("vigilantia_help_eye_closed_label"), body: t("vigilantia_help_eye_closed_body") },
    ],
  },
  {
    title: t("vigilantia_help_section_sources"),
    rows: [
      { icon: Camera, label: t("vigilantia_help_sources_label"), body: t("vigilantia_help_sources_body") },
      { icon: ScanFace, label: t("vigilantia_help_face_label"), body: t("vigilantia_help_face_body") },
      { icon: Activity, label: t("vigilantia_help_motion_label"), body: t("vigilantia_help_motion_body") },
      { icon: PersonStanding, label: t("vigilantia_help_person_label"), body: t("vigilantia_help_person_body") },
      { icon: Radar, label: t("vigilantia_help_edgeai_label"), body: t("vigilantia_help_edgeai_body") },
    ],
  },
  { title: t("vigilantia_help_section_preview"), rows: [{ icon: Video, label: t("vigilantia_help_preview_label"), body: t("vigilantia_help_preview_body") }] },
  {
    title: t("vigilantia_help_section_related"),
    rows: [
      { icon: User, label: t("vigilantia_help_personarium_label"), body: t("vigilantia_help_personarium_body") },
      { icon: ScrollText, label: t("vigilantia_help_casus_label"), body: t("vigilantia_help_casus_body") },
      { icon: Camera, label: t("vigilantia_help_multipose_label"), body: t("vigilantia_help_multipose_body") },
    ],
  },
];
</script>

<template>
  <p class="intro">{{ t("vigilantia_help_intro") }}</p>
  <section v-for="level in LEVELS" :key="level.title">
    <h3>{{ level.title }}</h3>
    <div v-for="row in level.rows" :key="row.label" class="row">
      <span class="icon"><component :is="row.icon" :size="14" /></span>
      <div><strong>{{ row.label }}</strong><p>{{ row.body }}</p></div>
    </div>
  </section>
  <button v-if="openHelp" class="link" @click="openHelp('lifecycle')"><ArrowRight :size="14" /> {{ t("vigilantia_help_lifecycle_link") }}</button>
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
