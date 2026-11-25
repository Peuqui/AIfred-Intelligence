// The context-menu entries of the documents page that belong to the search index (the entries VueFinder brings are the
// file manager's): index files, index a folder with everything below it, take files out of the index and, for the local
// folder, copy the path a tool takes. FileBrowser.vue adds them to its menu; what they do is the page's.
import type { Item } from "vuefinder";
import { t } from "../i18n";
import { type IndexedEntry, type IndexTarget, chunksOf, relPath } from "./documentIndex";

export interface IndexActions {
  index: (targets: IndexTarget[]) => void;
  deindex: (files: IndexTarget[]) => void;
  // Only where the path means something to a tool (the local documents folder).
  copyPath?: (path: string) => void;
}

const isIndexableFile = (entry: IndexedEntry): boolean => entry.type === "file" && !!entry.indexable;
const isIndexedFile = (entry: IndexedEntry): boolean => entry.type === "file" && chunksOf(entry) > 0;

export function indexMenuItems(actions: IndexActions): Item[] {
  const items: Item[] = [
    {
      id: "aifred_index",
      title: () => t("doc_index"),
      action: (_app, entries) => actions.index((entries as IndexedEntry[]).filter(isIndexableFile)),
      show: (_app, context) => (context.items as IndexedEntry[]).some(isIndexableFile),
      order: 110,
    },
    {
      id: "aifred_index_folder",
      title: () => t("doc_index_folders"),
      action: (_app, entries) => actions.index(entries.filter((entry) => entry.type === "dir")),
      show: (_app, context) => context.items.some((entry) => entry.type === "dir"),
      order: 111,
    },
    {
      id: "aifred_deindex",
      title: () => t("doc_deindex"),
      action: (_app, entries) => actions.deindex((entries as IndexedEntry[]).filter(isIndexedFile)),
      show: (_app, context) => (context.items as IndexedEntry[]).some(isIndexedFile),
      order: 112,
    },
  ];
  const { copyPath } = actions;
  if (copyPath) {
    items.push({
      id: "aifred_copy_path",
      title: () => t("doc_copy_path"),
      action: (_app, entries) => { if (entries[0]) copyPath(relPath(entries[0])); },
      show: (_app, context) => context.items.length === 1 && context.items[0]?.type === "file",
      order: 113,
    });
  }
  return items;
}
