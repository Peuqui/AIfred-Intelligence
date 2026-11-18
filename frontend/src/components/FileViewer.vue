<script setup lang="ts">
// The dialog of the viewer (lib/fileViewer.ts), mounted once in the shell: a file's text or image, from whichever page.
import { FileText } from "lucide-vue-next";
import { t } from "../i18n";
import { viewed } from "../lib/fileViewer";
import ModalDialog from "./ModalDialog.vue";
</script>

<template>
  <ModalDialog v-if="viewed" @close="viewed = null">
    <header class="viewer-head">
      <strong><FileText :size="16" /> {{ viewed.name }}</strong>
      <button :title="t('dialog_close')" @click="viewed = null">×</button>
    </header>
    <img v-if="viewed.imageUrl" class="viewer-image" :src="viewed.imageUrl" :alt="viewed.name" />
    <pre v-else class="viewer-text">{{ viewed.text }}</pre>
  </ModalDialog>
</template>

<style scoped>
.viewer-head { display: flex; justify-content: space-between; align-items: center; gap: 20px; }
.viewer-head button { background: none; border: none; font-size: 1.2rem; }
.viewer-text { margin: 0; width: min(820px, calc(86 * var(--vw))); max-height: calc(70 * var(--dvh)); overflow: auto; white-space: pre-wrap; font-size: 0.85rem; background: var(--aifred-page-bg); padding: 12px; border-radius: 8px; }
.viewer-image { max-width: min(820px, calc(86 * var(--vw))); max-height: calc(70 * var(--dvh)); object-fit: contain; }
</style>
