// The documents page: the local documents folder (data/documents/) and the further sources the enabled
// plugins offer (Nextcloud …), each with the place of every file in the search index, and what the page
// does to them. Every change of the local folder answers with its listing again. Indexing is a job of
// the server: the store asks how far it is while it runs (whoever started it); the page lists its folder again when
// it has ended. An upload into a source is not indexed: the user decides per file.
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
import { type IndexTarget, relPath } from "../lib/documentIndex";
import { onResourceChanged } from "../lib/resourceChanges";

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

  function preview(path: string): Promise<DocumentPreview | null> {
    return call(() => api<DocumentPreview>(`documents/local/preview?path=${encodeURIComponent(path)}`));
  }

  // A file of the open source, as the index's parser reads it (the id may hold slashes: Nextcloud's is the path).
  function previewRemote(fileId: string): Promise<DocumentPreview | null> {
    return call(() => api<DocumentPreview>(`${sourcePath()}/preview/${fileId.split("/").map(encodeURIComponent).join("/")}`));
  }

  // While the run goes on it is asked again. Whoever shows the list lists it anew when the run has ended (the counts
  // of indexed sections), it sees that in ``indexing.running``.
  async function follow(progress: DocumentIndexProgress): Promise<void> {
    indexing.value = progress;
    if (!progress.running) return;
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

  // The open source's index endpoints take paths (a source's ids) from its root, as VueFinder's entries carry them.
  async function index(targets: IndexTarget[]): Promise<void> {
    const files = targets.filter((target) => target.type === "file" && target.indexable);
    const folders = targets.filter((target) => target.type === "dir").map(relPath);
    const progress = await call(() =>
      source.value === LOCAL
        ? post<DocumentIndexProgress>("documents/local/index", { folder: "", files: files.map(relPath), folders })
        : post<DocumentIndexProgress>(`${sourcePath()}/index`, { files: files.map((file) => ({ id: relPath(file), name: file.basename })), folders }),
    );
    if (progress) await follow(progress);
  }

  // Files out of the index, the files stay where they are. The local delete takes one folder and names in it.
  async function deindex(files: IndexTarget[]): Promise<void> {
    if (source.value !== LOCAL) {
      await call(() => post<SourceDeindexed>(`${sourcePath()}/deindex`, { file_ids: files.map(relPath) }));
      return;
    }
    const byFolder = new Map<string, string[]>();
    for (const file of files) {
      const rel = relPath(file);
      const folder = rel.split("/").slice(0, -1).join("/");
      byFolder.set(folder, [...(byFolder.get(folder) ?? []), file.basename]);
    }
    for (const [folder, names] of byFolder) {
      const deleted = await call(() =>
        post<DocumentDeleted>("documents/local/delete", { folder, files: names, folders: [], from_disk: false, from_index: true }),
      );
      if (deleted?.failed.length) notice.value = deleted.failed.map((failure) => `${failure.name}: ${failure.reason}`).join(" · ");
    }
  }

  // ── Further sources ──────────────────────────────────────────────────

  const sourcePath = (): string => `documents/source/${encodeURIComponent(source.value)}`;

  async function loadSources(): Promise<void> {
    const found = await call(() => api<DocumentSourceInfo[]>("documents/sources"));
    if (found) sources.value = found;
  }

  // A plugin switched on or off (the server announces it): the list of sources follows; a source that is gone
  // takes the open one with it, and the page falls back to the local folder (core).
  async function followPlugins(): Promise<void> {
    await loadSources();
    if (!sources.value.some((offered) => offered.key === source.value)) await openSource(LOCAL);
  }

  onResourceChanged("plugins", () => void followPlugins());

  async function listRemote(): Promise<void> {
    const params = new URLSearchParams({ folder_id: openFolderId.value, query: query.value });
    const listed = await call(() => api<SourceListing>(`${sourcePath()}?${params}`));
    remote.value = listed;
  }

  async function openSource(key: string): Promise<void> {
    source.value = key;
    trail.value = [];
    query.value = "";
    remote.value = null;
    await (source.value === LOCAL ? open(listing.value?.folder ?? "") : listRemote());
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

  return {
    listing, notice, busy, indexing, rejected, open, newFolder, rename, preview, previewRemote, upload, index, deindex, watchIndexing,
    sources, source, remote, trail, query, loadSources, openSource, enterRemote, backRemote, searchRemote, uploadRemote,
  };
});
