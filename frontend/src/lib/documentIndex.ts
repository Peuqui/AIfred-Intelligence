// What the documents page knows about the search index of an entry VueFinder lists: the server's DirEntry carries
// ``indexable`` and ``index_chunks`` (lib/api/files.py) beyond VueFinder's own type, and the path of an entry is
// ``<area>://<path>``; the index endpoints take the path (a source's id) without the area.
import type { DirEntry } from "vuefinder";

export type IndexedEntry = DirEntry & { indexable?: boolean; index_chunks?: number };

// What indexing needs of an entry; the folder shown is a target too (no listing entry of its own).
export type IndexTarget = Pick<DirEntry, "type" | "path" | "basename"> & { indexable?: boolean };

export const relPath = (entry: { path: string }): string => entry.path.replace(/^[^:]+:\/\//, "");

export const chunksOf = (entry: IndexedEntry): number => entry.index_chunks ?? 0;
