// The documents page's state: the sources it offers (the local documents folder and the further ones the enabled plugins
// offer: Nextcloud …) and the search index's job. Listing, uploading and every change of the files is the file browser's
// (FileBrowser.vue, /files/{area}); what is left here is what the search index does with them. Indexing is a job of the
// server: the store asks how far it is while it runs (whoever started it); the page lists its folder again when it has
// ended. An upload into a source is not indexed: the user decides per file.
import { defineStore } from "pinia";
import { ref } from "vue";
import { ApiError, PROGRESS_POLL_MS, api } from "../api/client";
import type { Deindexed, DocumentIndexProgress, DocumentSourceInfo } from "../api/types";
import { type IndexTarget, relPath } from "../lib/documentIndex";
import { onResourceChanged } from "../lib/resourceChanges";

export const LOCAL = "local";

export const useDocuments = defineStore("documents", () => {
  const notice = ref("");
  // The server's indexing run as last asked (the page shows its progress and, once ended, its outcome).
  const indexing = ref<DocumentIndexProgress | null>(null);
  // Why files of the last upload were refused (type, name).
  const rejected = ref<string[]>([]);
  let polling: number | null = null;
  // The sources (local first) and the one open.
  const sources = ref<DocumentSourceInfo[]>([]);
  const source = ref(LOCAL);

  // Every call goes through here: its reason as the page's notice when it fails.
  async function call<T>(work: () => Promise<T>): Promise<T | null> {
    notice.value = "";
    try {
      return await work();
    } catch (error) {
      notice.value = error instanceof ApiError ? error.detail : String(error);
      return null;
    }
  }

  const post = <T>(path: string, body: object): Promise<T> => api<T>(path, { method: "POST", body: JSON.stringify(body) });

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

  // On opening the page and after an upload: a run another tab started shows its progress too.
  async function watchIndexing(): Promise<void> {
    const progress = await call(() => api<DocumentIndexProgress>("documents/index"));
    if (progress?.running) await follow(progress);
  }

  const sourcePath = (): string => `documents/source/${encodeURIComponent(source.value)}`;

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

  // Files out of the index, the files stay where they are.
  async function deindex(files: IndexTarget[]): Promise<void> {
    const body = source.value === LOCAL ? { files: files.map(relPath) } : { file_ids: files.map(relPath) };
    await call(() => post<Deindexed>(`${source.value === LOCAL ? "documents/local" : sourcePath()}/deindex`, body));
  }

  async function loadSources(): Promise<void> {
    const found = await call(() => api<DocumentSourceInfo[]>("documents/sources"));
    if (found) sources.value = found;
  }

  // A plugin switched on or off (the server announces it): the list of sources follows; a source that is gone
  // takes the open one with it, and the page falls back to the local folder (core).
  async function followPlugins(): Promise<void> {
    await loadSources();
    if (!sources.value.some((offered) => offered.key === source.value)) source.value = LOCAL;
  }

  onResourceChanged("plugins", () => void followPlugins());

  return { notice, indexing, rejected, sources, source, index, deindex, watchIndexing, loadSources };
});
