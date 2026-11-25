<script setup lang="ts">
// The dialog of the viewer (lib/fileViewer.ts), mounted once in the shell: a file from whichever page, shown by
// FilePreview (the same as beside the list of the documents page). The box is as wide as the page (--file-dialog-width,
// App.vue) and fills the whole height of the dialog (ModalDialog full: it does not jump with every file).
import { t } from "../i18n";
import { viewed } from "../lib/fileViewer";
import FilePreview from "./FilePreview.vue";
import ModalDialog from "./ModalDialog.vue";
</script>

<template>
  <ModalDialog v-if="viewed" full @close="viewed = null">
    <div class="viewer">
      <FilePreview :place="viewed">
        <template #actions><button :title="t('dialog_close')" @click="viewed = null">×</button></template>
      </FilePreview>
    </div>
  </ModalDialog>
</template>

<style scoped>
.viewer { width: var(--file-dialog-width); flex: 1; min-height: 0; }
.viewer button { background: none; border: none; font-size: 1.2rem; }
</style>
