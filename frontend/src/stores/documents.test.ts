// The list of document sources follows the plugins live; a source that disappears takes the open one with it and the
// page falls back to the local folder (core).
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { DocumentSourceInfo } from "../api/types";
import type { IndexTarget } from "../lib/documentIndex";
import { resourceChanged } from "../lib/resourceChanges";

let offered: DocumentSourceInfo[];
const api = vi.fn(async (path: string, _options?: { body?: string }) => {
  if (path === "documents/sources") return offered;
  if (path === "documents/local/index" || path.endsWith("/index")) return { running: false, current: "", done: 0, total: 0, indexed: 0, chunks: 0, failed: [] };
  if (path.endsWith("/deindex")) return { chunks_removed: 0 };
  if (path === "documents/local/delete") return { listing: { folder: "", items: [] }, failed: [] };
  if (path === "documents/local?folder=") return { folder: "", items: [] };
  return { source: "nextcloud_files", folder_id: "", query: "", writable: true, items: [] };
});
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, api: (path: string, options?: { body?: string }) => (options ? api(path, options) : api(path)), PROGRESS_POLL_MS: 1000 }));

const { LOCAL, useDocuments } = await import("./documents");

const info = (key: string): DocumentSourceInfo => ({ key, label: key, writable: true });
const settle = async (): Promise<void> => {
  await vi.advanceTimersByTimeAsync(400);
};

beforeEach(() => {
  vi.useFakeTimers();
  setActivePinia(createPinia());
  api.mockClear();
  offered = [info(LOCAL), info("nextcloud_files")];
});
afterEach(() => vi.useRealTimers());

test("the event \"plugins\" loads the sources anew", async () => {
  const docs = useDocuments();
  await docs.loadSources();
  expect(docs.sources.map((source) => source.key)).toEqual([LOCAL, "nextcloud_files"]);
  offered = [info(LOCAL), info("nextcloud_files"), info("google_drive")];
  resourceChanged("plugins");
  await settle();
  expect(docs.sources.map((source) => source.key)).toEqual([LOCAL, "nextcloud_files", "google_drive"]);
});

test("the open source is gone after a switch off: back to the local folder", async () => {
  const docs = useDocuments();
  await docs.loadSources();
  await docs.openSource("nextcloud_files");
  expect(docs.source).toBe("nextcloud_files");
  offered = [info(LOCAL)];
  resourceChanged("plugins");
  await settle();
  expect(docs.source).toBe(LOCAL);
  expect(docs.sources.map((source) => source.key)).toEqual([LOCAL]);
});

test("another source stays open while a different one goes away", async () => {
  const docs = useDocuments();
  offered = [info(LOCAL), info("nextcloud_files"), info("google_drive")];
  await docs.loadSources();
  await docs.openSource("nextcloud_files");
  offered = [info(LOCAL), info("nextcloud_files")];
  resourceChanged("plugins");
  await settle();
  expect(docs.source).toBe("nextcloud_files");
});

test("the preview of a source file asks the open source, the id's slashes stay and its other characters are escaped", async () => {
  const docs = useDocuments();
  await docs.loadSources();
  await docs.openSource("nextcloud_files");
  api.mockResolvedValueOnce({ name: "Plan #1.txt", text: "Erst denken" } as never);
  expect(await docs.previewRemote("Projekt/Plan #1.txt")).toEqual({ name: "Plan #1.txt", text: "Erst denken" });
  expect(api).toHaveBeenLastCalledWith("documents/source/nextcloud_files/preview/Projekt/Plan%20%231.txt");
});

// What VueFinder lists: the path of an entry is <area>://<path>, a source's id is that path.
const entry = (path: string, extra: Partial<IndexTarget> = {}): IndexTarget => ({ type: "file", basename: path.split("/").at(-1) ?? "", path, indexable: true, ...extra });
const bodyOf = (path: string): unknown => JSON.parse(String(api.mock.calls.find(([called]) => called === path)?.[1]?.body));

test("indexing in the local folder sends the paths from its root, files the index reads and folders apart", async () => {
  const docs = useDocuments();
  await docs.index([
    entry("documents://Unter/a.md"),
    entry("documents://b.png", { indexable: false }),
    entry("documents://Ordner", { type: "dir", indexable: false }),
  ]);
  expect(bodyOf("documents/local/index")).toEqual({ folder: "", files: ["Unter/a.md"], folders: ["Ordner"] });
});

test("indexing in a source goes to that source's endpoint with the ids and names of its files, folders by id", async () => {
  const docs = useDocuments();
  await docs.loadSources();
  await docs.openSource("nextcloud_files");
  await docs.index([entry("source-nextcloud_files://Projekt/Plan.pdf"), entry("source-nextcloud_files://Projekt", { type: "dir", indexable: false })]);
  expect(bodyOf("documents/source/nextcloud_files/index")).toEqual({
    files: [{ id: "Projekt/Plan.pdf", name: "Plan.pdf" }],
    folders: ["Projekt"],
  });
});

test("taking files out of the index: local ones per folder without touching the disk, those of a source by id", async () => {
  const docs = useDocuments();
  await docs.deindex([entry("documents://Unter/a.md"), entry("documents://Unter/b.md"), entry("documents://c.md")]);
  const deletes = api.mock.calls.filter(([called]) => called === "documents/local/delete").map(([, options]) => JSON.parse(String(options?.body)));
  expect(deletes).toEqual([
    { folder: "Unter", files: ["a.md", "b.md"], folders: [], from_disk: false, from_index: true },
    { folder: "", files: ["c.md"], folders: [], from_disk: false, from_index: true },
  ]);
  await docs.loadSources();
  await docs.openSource("nextcloud_files");
  await docs.deindex([entry("source-nextcloud_files://Projekt/Plan.pdf")]);
  expect(bodyOf("documents/source/nextcloud_files/deindex")).toEqual({ file_ids: ["Projekt/Plan.pdf"] });
});
