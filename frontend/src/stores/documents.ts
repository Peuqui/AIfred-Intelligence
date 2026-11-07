// The local documents folder (data/documents/): its listing with the place of each file in the search
// index, and what the page does to it. Every change answers with the folder's listing again. Indexing
// is a job of the server: the store asks how far it is while it runs (whoever started it) and opens
// the folder again when it has ended.
import { defineStore } from "pinia";
import { ref } from "vue";
import { ApiError, api } from "../api/client";
import type { DocumentDeleted, DocumentFolder, DocumentIndexProgress, DocumentPreview, DocumentsUploaded } from "../api/types";

const PROGRESS_POLL_MS = 700;

export const useDocuments = defineStore("documents", () => {
  const listing = ref<DocumentFolder | null>(null);
  const notice = ref("");
  const busy = ref(false);
  // The server's indexing run as last asked (the page shows its progress and, once ended, its outcome).
  const indexing = ref<DocumentIndexProgress | null>(null);
  // Why files of the last upload were refused (type, name).
  const rejected = ref<string[]>([]);
  let polling: number | null = null;

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

  // While the run goes on it is asked again; at its end the folder opens anew (indexed counts).
  async function follow(progress: DocumentIndexProgress): Promise<void> {
    indexing.value = progress;
    if (!progress.running) {
      await open(listing.value?.folder ?? "");
      return;
    }
    polling ??= window.setTimeout(async () => {
      polling = null;
      try {
        await follow(await api<DocumentIndexProgress>("documents/index"));
      } catch (error) {
        notice.value = error instanceof ApiError ? error.detail : String(error);
      }
    }, PROGRESS_POLL_MS);
  }

  // On opening the page: a run another tab started shows its progress too.
  async function watchIndexing(): Promise<void> {
    const progress = await call(() => api<DocumentIndexProgress>("documents/index"));
    if (progress?.running) await follow(progress);
  }

  async function upload(files: File[]): Promise<void> {
    const form = new FormData();
    form.append("folder", listing.value?.folder ?? "");
    for (const file of files) form.append("files", file, file.name);
    const uploaded = await call(() => api<DocumentsUploaded>("documents/local/upload", { method: "POST", body: form }));
    if (!uploaded) return;
    listing.value = uploaded.listing;
    rejected.value = uploaded.rejected.map((refusal) => refusal.reason);
    if (uploaded.saved.length) await follow(uploaded.indexing);
  }

  async function index(files: string[], folders: string[]): Promise<void> {
    const folder = listing.value?.folder ?? "";
    const progress = await call(() => post<DocumentIndexProgress>("documents/local/index", { folder, files, folders }));
    if (progress) await follow(progress);
  }

  return { listing, notice, busy, indexing, rejected, open, newFolder, rename, remove, preview, upload, index, watchIndexing };
});
