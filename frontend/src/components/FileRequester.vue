<script setup lang="ts">
// The file requester: VueFinder over one area of the server (lib/file_areas.py, lib/api/files.py). It offers what the
// area allows — browse, folders, rename, delete, copy, move, upload, download, preview, search — and in the context
// menu of a folder "create link" (VueFinder has none). With ``pick`` it returns a folder: the one selected, else the
// one open. Any place that needs files opens it with its area.
import { computed, getCurrentInstance, onMounted, ref } from "vue";
import {
  type DirEntry, type FeaturesConfig, type Item, RemoteDriver, VueFinder, VueFinderPlugin, contextMenuItems, useVueFinder,
} from "vuefinder";
import "vuefinder/dist/style.css";
import finderDe from "vuefinder/dist/locales/de.js";
import finderEn from "vuefinder/dist/locales/en.js";
import { ApiError, api, apiUrl } from "../api/client";
import type { FileAreaInfo } from "../api/types";
import { language, t } from "../i18n";
import ModalDialog from "./ModalDialog.vue";

const props = defineProps<{ area: string; title: string; pick?: boolean }>();
const emit = defineEmits<{ close: []; pick: [path: string] }>();

// VueFinder with its translations, installed on first use (the page loads this component only when it opens: VueFinder
// and its libraries are about 1 MB); a second use leaves the installed plugin as it is.
getCurrentInstance()?.appContext.app.use(VueFinderPlugin, { i18n: { de: finderDe, en: finderEn } });

const finderId = `files-${props.area}`;
const prefix = `${props.area}://`;
const driver = new RemoteDriver({ baseURL: apiUrl(`files/${props.area}`) });
const info = ref<FileAreaInfo | null>(null);
const notice = ref("");
const current = ref(prefix);
const selected = ref<DirEntry[]>([]);

// The area's actions as VueFinder's features; what the server has no endpoint for stays off.
const features = computed<FeaturesConfig>(() => {
  const allowed = new Set(info.value?.actions ?? []);
  return {
    newfolder: allowed.has("newfolder"), rename: allowed.has("rename"), delete: allowed.has("delete"),
    upload: allowed.has("upload"), download: allowed.has("download"), preview: allowed.has("preview"),
    search: allowed.has("search"), copy: allowed.has("copy"), move: allowed.has("move"),
    newfile: false, edit: false, archive: false, unarchive: false, language: false, theme: false,
    fullscreen: true, history: true, pinned: true,
  };
});

// ── a link to a folder (its own dialog: VueFinder knows no links) ──
const linking = ref<DirEntry | null>(null);
const linkName = ref("");
const linkItem: Item = {
  id: "aifred_link",
  title: () => t("files_make_link"),
  action: (_app, items) => {
    const folder = items[0];
    if (!folder) return;
    linking.value = folder;
    linkName.value = `${folder.basename} (Link)`;
  },
  show: (_app, context) => !!info.value?.actions.includes("symlink") && context.items.length === 1 && context.items[0]?.type === "dir",
  order: 100,
};
const menu = [...contextMenuItems, linkItem];

async function makeLink(): Promise<void> {
  if (!linking.value) return;
  notice.value = "";
  try {
    await api(`files/${props.area}/symlink`, {
      method: "POST",
      body: JSON.stringify({ path: current.value, name: linkName.value.trim(), target: linking.value.path }),
    });
    linking.value = null;
    await useVueFinder(finderId).refresh();
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

onMounted(async () => {
  try {
    info.value = await api<FileAreaInfo>(`files/${props.area}/info`);
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
  }
});
</script>

<template>
  <ModalDialog @close="emit('close')">
    <div class="requester">
      <header class="head">
        <strong>📁 {{ title }}</strong>
        <code v-if="info" class="root">{{ info.root }}</code>
        <button class="close" :title="t('dialog_close')" @click="emit('close')">×</button>
      </header>
      <p v-if="notice" class="notice">⚠️ {{ notice }}</p>
      <div v-if="info" class="finder">
        <VueFinder
          :id="finderId"
          :driver="driver"
          :features="features"
          :locale="language"
          :config="{ theme: 'midnight', persist: false, initialPath: prefix }"
          :context-menu-items="menu"
          @select="(items: DirEntry[]) => (selected = items)"
          @path-change="(path: string) => (current = path)"
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
      <strong>🔗 {{ t("files_make_link") }}</strong>
      <span>{{ t("files_link_target") }} <code>{{ linking.path.slice(prefix.length) }}</code></span>
      <span>{{ t("files_link_place") }} <code>{{ current.slice(prefix.length) || "/" }}</code></span>
      <label>{{ t("files_link_name") }}<input v-model="linkName" required /></label>
      <p v-if="notice" class="notice">⚠️ {{ notice }}</p>
      <div class="actions">
        <button type="button" @click="linking = null">{{ t("doc_cancel") }}</button>
        <button type="submit" class="primary">{{ t("files_make_link") }}</button>
      </div>
    </form>
  </ModalDialog>
</template>

<style scoped>
.requester { width: min(1100px, 92vw); display: flex; flex-direction: column; gap: 10px; }
.head { display: flex; align-items: center; gap: 12px; }
.root { color: var(--aifred-text-secondary); font-size: 0.8rem; margin-right: auto; }
.close { background: none; border: none; color: inherit; font-size: 1.3rem; cursor: pointer; }
.finder { height: min(68dvh, 720px); }
.pick { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; font-size: 0.9rem; }
.link { display: flex; flex-direction: column; gap: 10px; width: min(520px, 88vw); }
.link label { display: flex; flex-direction: column; gap: 4px; font-size: 0.85rem; color: var(--aifred-text-secondary); }
button, input { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 4px 10px; font: inherit; }
button { cursor: pointer; }
button.primary { border-color: var(--aifred-primary); color: var(--aifred-primary); }
.actions { display: flex; gap: 8px; justify-content: flex-end; }
.notice { margin: 0; color: var(--aifred-accent-amber); }
</style>

<style>
/* VueFinder puts its own dialogs (new folder, rename, delete …) on <body> at z-index 70: below the requester's
   ModalDialog (100), they opened unseen. Not scoped: they live outside this component. */
.vuefinder__themer.vuefinder__modal-layout { z-index: 110; }
</style>
