// The documents page: the local documents folder (data/documents/) and the further sources the enabled
// plugins offer (Nextcloud …), each with the place of every file in the search index, and what the page
// does to them. Every change of the local folder answers with its listing again. Indexing is a job of
// the server: the store asks how far it is while it runs (whoever started it) and lists the open
// folder again when it has ended. An upload into a source is not indexed: the user decides per file.
import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { ApiError, PROGRESS_POLL_MS, api } from "../api/client";
import type {
  DocumentDeleted,
  DocumentFolder,
  DocumentIndexProgress,
  DocumentPreview,
  DocumentSourceInfo,
  DocumentsUploaded,
  SourceDeindexed,
  SourceListing,
  SourceUploaded,
} from "../api/types";

export const LOCAL = "local";

export const useDocuments = defineStore("documents", () => {
  const listing = ref<DocumentFolder | null>(null);
  const notice = ref("");
  const busy = ref(false);
  // The server's indexing run as last asked (the page shows its progress and, once ended, its outcome).
  const indexing = ref<DocumentIndexProgress | null>(null);
  // Why files of the last upload were refused (type, name).
  const rejected = ref<string[]>([]);
  let polling: number | null = null;
  // The sources (local first), the one open, and for a further source its open folder as a trail of
  // ids (root = empty) and the search its listing shows.
  const sources = ref<DocumentSourceInfo[]>([]);
  const source = ref(LOCAL);
  const remote = ref<SourceListing | null>(null);
  const trail = ref<{ id: string; name: string }[]>([]);
  const query = ref("");
  const openFolderId = computed(() => trail.value.at(-1)?.id ?? "");

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

  // While the run goes on it is asked again; at its end the open folder is listed anew (indexed counts).
  async function follow(progress: DocumentIndexProgress): Promise<void> {
    indexing.value = progress;
    if (!progress.running) {
      await reload();
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

  // ── Further sources ──────────────────────────────────────────────────

  const sourcePath = (): string => `documents/source/${encodeURIComponent(source.value)}`;

  async function loadSources(): Promise<void> {
    const found = await call(() => api<DocumentSourceInfo[]>("documents/sources"));
    if (found) sources.value = found;
  }

  async function listRemote(): Promise<void> {
    const params = new URLSearchParams({ folder_id: openFolderId.value, query: query.value });
    const listed = await call(() => api<SourceListing>(`${sourcePath()}?${params}`));
    remote.value = listed;
  }

  // The open folder of whichever source is open.
  function reload(): Promise<void> {
    return source.value === LOCAL ? open(listing.value?.folder ?? "") : listRemote();
  }

  async function openSource(key: string): Promise<void> {
    source.value = key;
    trail.value = [];
    query.value = "";
    remote.value = null;
    await reload();
  }

  async function enterRemote(id: string, name: string): Promise<void> {
    trail.value = [...trail.value, { id, name }];
    query.value = "";
    await listRemote();
  }

  async function backRemote(depth: number): Promise<void> {
    trail.value = trail.value.slice(0, depth);
    query.value = "";
    await listRemote();
  }

  async function searchRemote(text: string): Promise<void> {
    query.value = text.trim();
    await listRemote();
  }

  async function uploadRemote(files: File[]): Promise<void> {
    const form = new FormData();
    form.append("folder_id", openFolderId.value);
    for (const file of files) form.append("files", file, file.name);
    const uploaded = await call(() => api<SourceUploaded>(`${sourcePath()}/upload`, { method: "POST", body: form }));
    if (!uploaded) return;
    rejected.value = uploaded.rejected.map((refusal) => refusal.reason);
    await listRemote();
  }

  async function indexRemote(files: { id: string; name: string }[], folders: string[]): Promise<void> {
    const progress = await call(() => post<DocumentIndexProgress>(`${sourcePath()}/index`, { files, folders }));
    if (progress) await follow(progress);
  }

  async function deindexRemote(fileIds: string[]): Promise<void> {
    if (await call(() => post<SourceDeindexed>(`${sourcePath()}/deindex`, { file_ids: fileIds }))) await listRemote();
  }

  return {
    listing, notice, busy, indexing, rejected, open, newFolder, rename, remove, preview, upload, index, watchIndexing,
    sources, source, remote, trail, query, loadSources, openSource, enterRemote, backRemote, searchRemote, uploadRemote,
    indexRemote, deindexRemote,
  };
});
