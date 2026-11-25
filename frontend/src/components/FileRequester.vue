<script setup lang="ts">
// The file requester: a dialog around the file browser (FileBrowser.vue) over one area of the server. Any place that
// needs files opens it with its area; a file opens in the viewer (lib/fileViewer.ts). With ``pick`` it returns a folder:
// the one selected, else the one open. A link to a folder asks for its name here.
import { FolderOpen, Link, TriangleAlert } from "lucide-vue-next";
import { computed, ref } from "vue";
import type { DirEntry } from "vuefinder";
import { ApiError, api } from "../api/client";
import type { FileAreaInfo } from "../api/types";
import { t } from "../i18n";
import { areaPlace, viewFile } from "../lib/fileViewer";
import FileBrowser from "./FileBrowser.vue";
import ModalDialog from "./ModalDialog.vue";

const props = defineProps<{ area: string; title: string; pick?: boolean }>();
const emit = defineEmits<{ close: []; pick: [path: string] }>();

const prefix = `${props.area}://`;
const info = ref<FileAreaInfo | null>(null);
const notice = ref("");
const current = ref(prefix);
const selected = ref<DirEntry[]>([]);
const browser = ref<InstanceType<typeof FileBrowser> | null>(null);

// ── a link to a folder (its own dialog: VueFinder knows no links) ──
const linking = ref<DirEntry | null>(null);
const linkName = ref("");
function askLink(folder: DirEntry): void {
  linking.value = folder;
  linkName.value = `${folder.basename} (Link)`;
  notice.value = "";
}

async function makeLink(): Promise<void> {
  if (!linking.value) return;
  notice.value = "";
  try {
    await api(`files/${props.area}/symlink`, {
      method: "POST",
      body: JSON.stringify({ path: current.value, name: linkName.value.trim(), target: linking.value.path }),
    });
    linking.value = null;
    await browser.value?.refresh();
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
  }
}

// ── picking a folder ──
const chosen = computed(() => {
  const only = selected.value.length === 1 ? selected.value[0] : undefined;
  return only?.type === "dir" ? only.path : current.value;
});
const chosenText = computed(() => `${info.value?.root ?? ""}/${chosen.value.slice(prefix.length)}`.replace(/\/+$/, "") || "/");
</script>

<template>
  <ModalDialog full @close="emit('close')">
    <div class="requester">
      <header class="head">
        <strong><FolderOpen :size="16" /> {{ title }}</strong>
        <code v-if="info" class="root">{{ info.root }}</code>
        <button class="close" :title="t('dialog_close')" @click="emit('close')">×</button>
      </header>
      <div class="finder">
        <FileBrowser
          ref="browser"
          :area="area"
          @info="(loaded) => (info = loaded)"
          @select="(items) => (selected = items)"
          @path-change="(path) => (current = path)"
          @open="(entry) => viewFile(areaPlace(area, entry))"
          @link="askLink"
        />
      </div>
      <footer v-if="pick" class="pick">
        <span>{{ t("files_chosen_folder") }} <code>{{ chosenText }}</code></span>
        <button class="primary" @click="emit('pick', chosen.slice(prefix.length))">{{ t("files_pick_folder") }}</button>
      </footer>
    </div>
  </ModalDialog>

  <ModalDialog v-if="linking" @close="linking = null">
    <form class="link" @submit.prevent="makeLink">
      <strong><Link :size="16" /> {{ t("files_make_link") }}</strong>
      <span>{{ t("files_link_target") }} <code>{{ linking.path.slice(prefix.length) }}</code></span>
      <span>{{ t("files_link_place") }} <code>{{ current.slice(prefix.length) || "/" }}</code></span>
      <label>{{ t("files_link_name") }}<input v-model="linkName" required /></label>
      <p v-if="notice" class="notice"><TriangleAlert :size="16" /> {{ notice }}</p>
      <div class="actions">
        <button type="button" @click="linking = null">{{ t("doc_cancel") }}</button>
        <button type="submit" class="primary">{{ t("files_make_link") }}</button>
      </div>
    </form>
  </ModalDialog>
</template>

<style scoped>
.requester { width: var(--file-dialog-width); flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 10px; }
.head { display: flex; align-items: center; gap: 12px; }
.root { color: var(--aifred-text-secondary); font-size: 0.8rem; margin-right: auto; }
.close { background: none; border: none; color: inherit; font-size: 1.3rem; cursor: pointer; }
.finder { flex: 1; min-height: 0; }
.pick { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; font-size: 0.9rem; }
.link { display: flex; flex-direction: column; gap: 10px; width: min(520px, calc(88 * var(--vw))); }
.link label { display: flex; flex-direction: column; gap: 4px; font-size: 0.85rem; color: var(--aifred-text-secondary); }
input { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 4px 10px; font: inherit; }
button { padding: 4px 10px; }
.actions { display: flex; gap: 8px; justify-content: flex-end; }
.notice { margin: 0; color: var(--aifred-accent-amber); --icon-own: currentColor; }
</style>
