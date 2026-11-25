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
  return {};
});
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, api: (path: string, options?: { body?: string }) => (options ? api(path, options) : api(path)), PROGRESS_POLL_MS: 1000 }));

const { LOCAL, useDocuments } = await import("./documents");

const info = (key: string): DocumentSourceInfo => ({ key, label: key });
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
  docs.source = "nextcloud_files";
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
  docs.source = "nextcloud_files";
  offered = [info(LOCAL), info("nextcloud_files")];
  resourceChanged("plugins");
  await settle();
  expect(docs.source).toBe("nextcloud_files");
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
  docs.source = "nextcloud_files";
  await docs.index([entry("source-nextcloud_files://Projekt/Plan.pdf"), entry("source-nextcloud_files://Projekt", { type: "dir", indexable: false })]);
  expect(bodyOf("documents/source/nextcloud_files/index")).toEqual({
    files: [{ id: "Projekt/Plan.pdf", name: "Plan.pdf" }],
    folders: ["Projekt"],
  });
});

test("taking files out of the index: local ones by path, those of a source by id, none of them touches the files", async () => {
  const docs = useDocuments();
  await docs.deindex([entry("documents://Unter/a.md"), entry("documents://c.md")]);
  expect(bodyOf("documents/local/deindex")).toEqual({ files: ["Unter/a.md", "c.md"] });
  await docs.loadSources();
  docs.source = "nextcloud_files";
  await docs.deindex([entry("source-nextcloud_files://Projekt/Plan.pdf")]);
  expect(bodyOf("documents/source/nextcloud_files/deindex")).toEqual({ file_ids: ["Projekt/Plan.pdf"] });
});
