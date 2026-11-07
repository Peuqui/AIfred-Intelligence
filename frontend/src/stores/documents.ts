// The local documents folder (data/documents/): its listing with the place of each file in the search
// index, and what the page does to it. Every change answers with the folder's listing again.
import { defineStore } from "pinia";
import { ref } from "vue";
import { ApiError, api } from "../api/client";
import type { DocumentDeleted, DocumentFolder, DocumentPreview } from "../api/types";

export const useDocuments = defineStore("documents", () => {
  const listing = ref<DocumentFolder | null>(null);
  const notice = ref("");
  const busy = ref(false);

  // Every call goes through here: busy while it runs, its reason as the page's notice when it fails.
  async function call<T>(work: () => Promise<T>): Promise<T | null> {
    busy.value = true;
    notice.value = "";
    try {
      return await work();
    } catch (error) {
      notice.value = error instanceof ApiError ? error.detail : String(error);
      return null;
    } finally {
      busy.value = false;
    }
  }

  const post = <T>(path: string, body: object): Promise<T> => api<T>(path, { method: "POST", body: JSON.stringify(body) });

  async function open(folder: string): Promise<void> {
    const opened = await call(() => api<DocumentFolder>(`documents/local?folder=${encodeURIComponent(folder)}`));
    if (opened) listing.value = opened;
  }

  async function newFolder(name: string): Promise<void> {
    const folder = listing.value?.folder ?? "";
    const opened = await call(() => post<DocumentFolder>("documents/local/folders", { parent: folder, name }));
    if (opened) listing.value = opened;
  }

  async function rename(old: string, name: string): Promise<void> {
    const folder = listing.value?.folder ?? "";
    const opened = await call(() => post<DocumentFolder>("documents/local/rename", { folder, old, new: name }));
    if (opened) listing.value = opened;
  }

  async function remove(files: string[], folders: string[], fromDisk: boolean, fromIndex: boolean): Promise<DocumentDeleted | null> {
    const folder = listing.value?.folder ?? "";
    const deleted = await call(() =>
      post<DocumentDeleted>("documents/local/delete", { folder, files, folders, from_disk: fromDisk, from_index: fromIndex }),
    );
    if (deleted) {
      listing.value = deleted.listing;
      if (deleted.failed.length) notice.value = deleted.failed.map((failure) => `${failure.name}: ${failure.reason}`).join(" · ");
    }
    return deleted;
  }

  function preview(path: string): Promise<DocumentPreview | null> {
    return call(() => api<DocumentPreview>(`documents/local/preview?path=${encodeURIComponent(path)}`));
  }

  return { listing, notice, busy, open, newFolder, rename, remove, preview };
});
