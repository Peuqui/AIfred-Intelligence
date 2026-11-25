<script setup lang="ts">
// VueFinder over one area of the server (lib/file_areas.py, lib/api/files.py), the ONE place that sets it up: its driver,
// the area's actions as its features, AIfred's colours and the context menu, in which "create link" is added (VueFinder
// has none) and "Preview" goes to whoever embeds the browser (``open``: the file requester shows the dialog, the documents
// page the preview beside the list). It offers what the area allows — browse, folders, rename, delete, copy, move,
// upload, download, search. The slots of VueFinder (icon, menubar-end, status-bar …) pass through; the embedder may add
// entries to the context menu (``menuItems``) and show the area under a name of its own (``label``: VueFinder's status
// bar names the area by its technical name, "source-nextcloud_files").
import { TriangleAlert } from "lucide-vue-next";
import { computed, getCurrentInstance, onMounted, ref, useSlots } from "vue";
import {
  ContextMenuIds, type DirEntry, type FeaturesConfig, type Item, RemoteDriver, VueFinder, VueFinderPlugin, contextMenuItems, parseBackendError, useVueFinder,
} from "vuefinder";
import "vuefinder/dist/style.css";
import finderDe from "vuefinder/dist/locales/de.js";
import finderEn from "vuefinder/dist/locales/en.js";
import { ApiError, api, apiUrl } from "../api/client";
import { CLICK_SETTLE_MS, type Press, plainClick } from "../lib/plainClick";
import type { FileAreaInfo } from "../api/types";
import { language, t } from "../i18n";

const props = defineProps<{ area: string; menuItems?: Item[]; label?: string }>();
const emit = defineEmits<{
  info: [info: FileAreaInfo];
  select: [items: DirEntry[]];
  pathChange: [path: string];
  open: [entry: DirEntry];
  link: [folder: DirEntry];
  uploaded: [];
  refused: [name: string, reason: string];
}>();

// VueFinder with its translations, installed on first use (the page loads this component only when it is needed: VueFinder
// and its libraries are about 1 MB); a second use leaves the installed plugin as it is.
getCurrentInstance()?.appContext.app.use(VueFinderPlugin, { i18n: { de: finderDe, en: finderEn } });

// Uppy only says "network error" for a file the server refused (a type, a name); the server's reason sits in the body of
// its answer, and the embedder is told it (``refused``): VueFinder's dialog closes before anyone has read anything.
type Uploader = Parameters<RemoteDriver["configureUploader"]>;
class AreaDriver extends RemoteDriver {
  configureUploader(uppy: Uploader[0], context: Uploader[1]): void {
    super.configureUploader(uppy, context);
    uppy.on("upload-error", (file, error) => {
      const answer = (error as { request?: XMLHttpRequest }).request;
      if (file && answer) emit("refused", file.name ?? "", parseBackendError(answer.responseText, answer.status, answer.statusText));
    });
  }
}

const finderId = `files-${props.area}`;
const driver = new AreaDriver({ baseURL: apiUrl(`files/${props.area}`) });
const info = ref<FileAreaInfo | null>(null);
const notice = ref("");

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

// A link to a folder: VueFinder knows none, the embedder asks for its name and calls the server.
const linkItem: Item = {
  id: "aifred_link",
  title: () => t("files_make_link"),
  action: (_app, items) => {
    const folder = items[0];
    if (folder) emit("link", folder);
  },
  show: (_app, context) => !!info.value?.actions.includes("symlink") && context.items.length === 1 && context.items[0]?.type === "dir",
  order: 100,
};

// The menu entry "Preview" goes to the embedder, not to VueFinder's own; "Preview as" (text/image) would open VueFinder's
// own viewer, a second one beside ours, which picks the kind of a file itself.
const menu = [
  ...contextMenuItems.filter((item) => item.id !== ContextMenuIds.openAs).map((item) =>
    item.id === ContextMenuIds.preview
      ? { ...item, action: (_app: unknown, items: DirEntry[]) => { if (items[0]) emit("open", items[0]); } }
      : item,
  ),
  linkItem,
  ...(props.menuItems ?? []),
];

// One click or tap on an entry opens it, the same on every device and in every embedder (Peuqui 25.11.2026): a folder
// is entered, a file goes to the embedder's preview. VueFinder's own way (a click selects, a double click opens) is
// off: its double click would act on the entry that lies under the second click once the folder has changed. Selecting
// several stays with Ctrl/Shift and the frame; the right click and the long press (500 ms) bring the context menu and
// open nothing. The click is read from the pointer, not from the selection: a second click on the selected file shows
// it again.
let selection: DirEntry[] = [];
let press: Press | null = null;
let folderOpenedAt = -Infinity;

function pointerDown(event: PointerEvent): void {
  press = { x: event.clientX, y: event.clientY, at: event.timeStamp, plain: event.button === 0 && !event.ctrlKey && !event.metaKey && !event.shiftKey };
}

function pointerUp(event: PointerEvent): void {
  const pressed = press;
  press = null;
  if (!pressed || !plainClick(pressed, event, folderOpenedAt)) return;
  const key = (event.target as Element | null)?.closest<HTMLElement>("[data-key]")?.dataset.key;
  if (!key) return;
  // VueFinder selects the entry after the pointer is up (on a touch screen 100 ms later): open what it selected.
  setTimeout(() => {
    const [only, ...more] = selection;
    if (!only || more.length || `${only.type}:${only.path}` !== key) return;
    if (only.type === "dir") {
      folderOpenedAt = performance.now();
      void useVueFinder(finderId).open(only.path);
    } else {
      emit("open", only);
    }
  }, CLICK_SETTLE_MS);
}

function selected(items: DirEntry[]): void {
  selection = items;
  emit("select", items);
}

function doubleClick(event: { preventDefault: () => void }): void {
  event.preventDefault();
}

const passedSlots = Object.keys(useSlots()).filter((name) => name !== "status-bar");

defineExpose({ refresh: () => useVueFinder(finderId).refresh() });

onMounted(async () => {
  try {
    info.value = await api<FileAreaInfo>(`files/${props.area}/info`);
    emit("info", info.value);
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
  }
});
</script>

<template>
  <div class="browser" :class="{ labelled: label }" @pointerdown.capture="pointerDown" @pointerup.capture="pointerUp" @pointercancel.capture="press = null">
    <p v-if="notice" class="notice"><TriangleAlert :size="16" /> {{ notice }}</p>
    <VueFinder
      v-if="info"
      :id="finderId"
      :driver="driver"
      :features="features"
      :locale="language"
      :config="{ theme: 'midnight', persist: false, initialPath: `${area}://`, listItemHeight: 48 }"
      :context-menu-items="menu"
      @file-dclick="doubleClick"
      @folder-dclick="doubleClick"
      @select="selected"
      @path-change="(path: string) => emit('pathChange', path)"
      @upload-complete="emit('uploaded')"
    >
      <template v-for="name in passedSlots" :key="name" #[name]="slotProps"><slot :name="name" v-bind="slotProps ?? {}" /></template>
      <template #status-bar="slotProps">
        <span v-if="label" class="area-label">{{ label }}</span>
        <slot name="status-bar" v-bind="slotProps ?? {}" />
      </template>
    </VueFinder>
  </div>
</template>

<style scoped>
.browser { height: 100%; min-height: 0; display: flex; flex-direction: column; gap: 8px; }
.notice { margin: 0; color: var(--aifred-accent-amber); --icon-own: currentColor; }
.area-label { color: var(--aifred-text-secondary); margin-right: 10px; }
/* With a name of our own the status bar's storage selector (the technical name) is left out. */
.labelled :deep(.vuefinder__status-bar__storage-container) { display: none; }
/* What the embedder puts into the status bar (a run's progress, its failures) may be longer than one line: it takes the
   rest of the bar and wraps. */
/* Buttons the embedder adds to the menu bar (menubar-end) go to a second row where the bar has no room for them (phone). */
.browser :deep(.vuefinder__menubar) { flex: none; }
.browser :deep(.vuefinder__menubar__container) { flex-wrap: wrap; height: auto; }
.browser :deep(.vuefinder__status-bar__wrapper) { flex-wrap: wrap; gap: 0 8px; }
.browser :deep(.vuefinder__status-bar__actions) { flex: 1 1 12rem; min-width: 0; flex-wrap: wrap; text-align: right; }
</style>

<style>
/* VueFinder puts its own dialogs (new folder, rename, delete …) on <body> at z-index 70: below the requester's
   ModalDialog (100), they opened unseen. Not scoped: they live outside this component. */
.vuefinder__themer.vuefinder__modal-layout { z-index: 110; }

/* VueFinder in AIfred's colours (Peuqui 25.11.2026): its theme "midnight" stays the base (dark, its rules for menus and
   icons), its variables take the colours of theme.py. Body-level dialogs of VueFinder carry the same attribute, so the
   selector reaches them too; "html" makes it win over VueFinder's own rule of the same shape. */
html .vuefinder__themer[data-theme="midnight"] {
  --vf-bg-primary: var(--aifred-card-bg);
  --vf-bg-secondary: var(--aifred-page-bg);
  --vf-bg-tertiary: var(--aifred-input-bg);
  --vf-bg-hover: var(--aifred-button-soft-bg-hover);
  --vf-bg-selected: var(--aifred-primary-bg);
  --vf-bg-disabled: var(--aifred-input-bg);
  --vf-text-primary: var(--aifred-text-primary);
  --vf-text-secondary: var(--aifred-text-soft);
  --vf-text-tertiary: var(--aifred-text-secondary);
  --vf-text-disabled: var(--aifred-text-muted);
  --vf-text-inverse: var(--aifred-page-bg);
  --vf-border-primary: var(--aifred-border);
  --vf-border-secondary: var(--aifred-border-light);
  --vf-border-focus: var(--aifred-primary);
  --vf-border-error: var(--aifred-danger);
  --vf-accent-primary: var(--aifred-primary);
  --vf-accent-secondary: var(--aifred-radix-orange-text);
  --vf-accent-success: var(--aifred-accent-success);
  --vf-accent-warning: var(--aifred-accent-warning);
  --vf-accent-error: var(--aifred-danger);
  --vf-interactive-hover: var(--aifred-button-soft-bg-hover);
  --vf-interactive-active: var(--aifred-primary-bg);
  --vf-interactive-focus: var(--aifred-primary-bg);
  --vf-selection-bg: var(--aifred-primary-bg);
  --vf-selection-border: var(--aifred-primary);
  --vf-dropdown-bg: var(--aifred-card-bg);
  --vf-dropdown-border: var(--aifred-border-light);
  --vf-dropdown-text: var(--aifred-text-primary);
  --vf-dropdown-hover: var(--aifred-input-bg);
  --vf-overlay-bg: var(--aifred-backdrop);
  --vf-overlay-border: var(--aifred-border-light);
  --vf-overlay-text: var(--aifred-text-secondary);
}
/* Folders outlined in the yellow of files and documents, as AIfred's own icons, lightly filled; menus with AIfred's rim instead of a blue glow. */
html .vuefinder__themer[data-theme="midnight"] .vuefinder__item-icon__folder,
html .vuefinder__themer[data-theme="midnight"] .vuefinder__item-icon__folder--open {
  fill: color-mix(in srgb, var(--aifred-radix-yellow-text) 25%, transparent); stroke: var(--aifred-radix-yellow-text);
}
/* Long names break in the list instead of being cut at one line (Peuqui 25.11.2026): the rows are taller (config
   listItemHeight, below) and show two lines; only what is longer still ends in an ellipsis. */
html .vuefinder__themer[data-theme="midnight"] .vuefinder__explorer__item-list-content .vuefinder__explorer__item-name {
  white-space: normal; overflow-wrap: anywhere; line-height: 1.2;
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; line-clamp: 2;
}
/* AIfred's typeface instead of VueFinder's own. */
html .vuefinder__themer[data-theme="midnight"], html .vuefinder__themer[data-theme="midnight"] * { font-family: inherit; }
html .vuefinder__themer[data-theme="midnight"] .vuefinder__context-menu {
  border-color: var(--aifred-border-light); box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
}
</style>
