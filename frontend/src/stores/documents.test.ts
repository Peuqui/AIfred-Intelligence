// The list of document sources follows the plugins live; a source that disappears takes the open one with it and the
// page falls back to the local folder (core).
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { DocumentSourceInfo } from "../api/types";
import { resourceChanged } from "../lib/resourceChanges";

let offered: DocumentSourceInfo[];
const api = vi.fn(async (path: string) => {
  if (path === "documents/sources") return offered;
  if (path === "documents/local?folder=") return { folder: "", items: [] };
  return { source: "nextcloud_files", folder_id: "", query: "", writable: true, items: [] };
});
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, api: (path: string) => api(path), PROGRESS_POLL_MS: 1000 }));

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
